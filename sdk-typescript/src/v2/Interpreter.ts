/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ProcessHandle, ProcessService, TIMED_OUT_LOG_PAGE_LIMIT, collectAllLogs, startProcess } from './Process.js'
import { HttpTransport, runtimeUniqueId } from './http.js'
import type {
  InterpreterContextJSON,
  InterpreterContextOptions,
  InterpreterError,
  InterpreterOutput,
  InterpreterContextReference,
  InterpreterResult,
  InterpreterRunOptions,
  LogFrame,
  WaitResult,
} from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import { badRequest } from './options.js'

const INTERRUPT_GRACE_MS = 10_000
const INTERRUPT_RETRY_MS = 250

type InterpreterTask<T> = (timeout: number | undefined) => Promise<T>

type QueueWaiter = {
  active: boolean
  start: () => void
  timer?: ReturnType<typeof setTimeout>
}

class ContextExecutionQueue {
  private readonly waiters: QueueWaiter[] = []
  private running = false
  private closing = false
  private onDrained: (() => void) | undefined

  private hasActiveWaiters(): boolean {
    return this.waiters.some((waiter) => waiter.active)
  }

  private maybeEvict(): void {
    if (this.running || !this.closing || this.hasActiveWaiters()) return
    const onDrained = this.onDrained
    this.onDrained = undefined
    onDrained?.()
  }

  public requestEviction(onDrained: () => void): boolean {
    this.closing = true
    this.onDrained = onDrained
    return !this.running && !this.hasActiveWaiters()
  }

  private removeWaiter(waiter: QueueWaiter): void {
    const index = this.waiters.indexOf(waiter)
    if (index >= 0) this.waiters.splice(index, 1)
  }

  private async acquire(deadline: number | undefined): Promise<boolean> {
    if (!this.running && this.waiters.length === 0) {
      this.running = true
      return true
    }
    return new Promise((resolve) => {
      const waiter: QueueWaiter = {
        active: true,
        start: () => {
          if (!waiter.active) return
          waiter.active = false
          if (waiter.timer) clearTimeout(waiter.timer)
          this.running = true
          resolve(true)
        },
      }
      this.waiters.push(waiter)
      if (deadline === undefined) return
      const remaining = deadline - Date.now()
      if (remaining <= 0) {
        waiter.active = false
        this.removeWaiter(waiter)
        this.maybeEvict()
        resolve(false)
        return
      }
      waiter.timer = setTimeout(() => {
        if (!waiter.active) return
        waiter.active = false
        this.removeWaiter(waiter)
        this.maybeEvict()
        resolve(false)
      }, remaining)
    })
  }

  private release(): void {
    this.running = false
    for (;;) {
      const next = this.waiters.shift()
      if (!next) {
        this.maybeEvict()
        return
      }
      if (!next.active) continue
      next.start()
      return
    }
  }

  public async run<T>(timeout: number | undefined, task: InterpreterTask<T>, onTimeout: () => T): Promise<T> {
    const deadline = queueDeadline(timeout)
    if (deadline !== undefined && Date.now() >= deadline) return onTimeout()
    const acquired = await this.acquire(deadline)
    if (!acquired) return onTimeout()
    try {
      const remaining = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
      if (remaining !== undefined && remaining <= 0) return onTimeout()
      return await task(remaining)
    } finally {
      this.release()
    }
  }
}

function isolatedContextId(language: string): string {
  return runtimeUniqueId(`isolated-${language}`)
}

function implicitContextId(language: string): string {
  return `code-${language}`
}

function queueDeadline(timeout: number | undefined): number | undefined {
  if (timeout === undefined || timeout === 0) return undefined
  if (!Number.isFinite(timeout) || timeout < 0) {
    throw badRequest('timeout must be a non-negative finite number')
  }
  return Date.now() + timeout
}

function isSessionNotFoundError(error: unknown): error is DaytonaError {
  return error instanceof DaytonaError && error.code === 'SESSION_NOT_FOUND'
}

function isJsonRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isInterpreterContextJson(value: unknown): value is InterpreterContextJSON {
  return isJsonRecord(value)
    && typeof value.sandboxId === 'string'
    && typeof value.contextId === 'string'
    && typeof value.language === 'string'
}

function contextIdentity(context: InterpreterContextReference | InterpreterContextJSON): InterpreterContextJSON {
  if (isInterpreterContextJson(context)) return context
  return {
    sandboxId: context.sandboxId,
    contextId: context.id,
    language: context.language,
  }
}

function assertContextBelongsToSandbox(
  context: InterpreterContextReference | InterpreterContextJSON,
  sandboxId: string,
): InterpreterContextJSON {
  const identity = contextIdentity(context)
  if (identity.sandboxId !== sandboxId) {
    throw badRequest('interpreter context belongs to a different sandbox')
  }
  return identity
}

function validateOptionalTimeout(timeout: number | undefined): void {
  if (timeout !== undefined && (!Number.isFinite(timeout) || timeout < 0)) {
    throw badRequest('timeout must be a non-negative finite number')
  }
}

/** Caller budget (ms) left after `startedAt`; `undefined`/`0` stay unbounded. */
function remainingBudgetMs(timeout: number | undefined, startedAt: number): number | undefined {
  if (timeout === undefined || timeout === 0) return undefined
  return timeout - (Date.now() - startedAt)
}

function assertExplicitContextEnvUnsupported(env: Record<string, string> | undefined): void {
  if (env !== undefined) {
    throw badRequest('env is only supported when creating a context or running without an explicit context')
  }
}

async function waitOrInterrupt(
  handle: ProcessHandle,
  timeout: number | undefined,
  callerDeadline: number | undefined,
): Promise<WaitResult> {
  // A caller budget that startProcess already exhausted must EXPIRE here, not
  // become `timeoutMs=0` (= wait until terminal) in the request.
  const result: WaitResult = callerDeadline !== undefined && timeout !== undefined && timeout <= 0
    ? { reason: 'timed_out' }
    : await handle.wait({ timeout })
  if (result.reason !== 'timed_out') return result
  // Past the budget: interrupt the kernel (^C semantics). Whatever terminal
  // result reported by the API is returned as-is, even
  // when it lands after the caller's deadline — same as the Python SDK.
  const interruptDeadline = Date.now() + INTERRUPT_GRACE_MS
  let interrupted = false
  while (!interrupted && Date.now() < interruptDeadline) {
    try {
      await handle.kill({ signal: 'INT' })
      interrupted = true
    } catch (error) {
      if (error instanceof DaytonaError && error.code === 'PROCESS_TERMINAL') {
        return await handle.wait({ timeout: INTERRUPT_GRACE_MS })
      }
      if (!(error instanceof DaytonaError && error.code === 'UNSUPPORTED_OPERATION')) throw error
      const wait = await handle.wait({ timeout: INTERRUPT_RETRY_MS })
      if (wait.reason !== 'timed_out') return wait
    }
  }
  if (!interrupted) return result
  return await handle.wait({ timeout: INTERRUPT_GRACE_MS })
}

async function collectResult(
  handle: ProcessHandle,
  timeout: number | undefined,
  callerDeadline: number | undefined,
): Promise<InterpreterResult> {
  const result = await waitOrInterrupt(handle, timeout, callerDeadline)
  const logs = await collectAllLogs(handle, result.reason === 'timed_out' ? { maxPages: TIMED_OUT_LOG_PAGE_LIMIT } : {})
  const stdout = logs.filter((frame) => frame.channel === 'stdout').map((frame) => frame.data).join('')
  const stderr = logs.filter((frame) => frame.channel === 'stderr').map((frame) => frame.data).join('')
  const outputs = logs
    .filter((frame) => frame.channel === 'display' || frame.channel === 'execute_result')
    .map((frame) => parseInterpreterOutput(frame))
    .filter((frame): frame is InterpreterOutput => frame !== undefined)
  const errors = logs
    .filter((frame) => frame.channel === 'error')
    .map((frame) => parseInterpreterError(frame))
    .filter((frame): frame is InterpreterError => frame !== undefined)
  return {
    process: { ...result, processId: handle.id },
    results: outputs,
    logs: { stdout, stderr },
    error: errors.at(-1),
  }
}

function timedOutInterpreterResult(): InterpreterResult {
  return {
    process: { processId: '', reason: 'timed_out' },
    results: [],
    logs: { stdout: '', stderr: '' },
  }
}

function parseInterpreterOutput(frame: LogFrame): InterpreterOutput | undefined {
  const parsed = parseJsonRecord(frame.data)
  if (parsed === undefined) return undefined
  const data = mimeBundle(parsed)
  if (data === undefined) return undefined
  return {
    data,
    isMainResult: frame.channel === 'execute_result',
    text: stringMime(data, 'text/plain'),
    html: stringMime(data, 'text/html'),
    png: stringMime(data, 'image/png'),
    svg: stringMime(data, 'image/svg+xml'),
    json: data['application/json'],
  }
}

function parseInterpreterError(frame: LogFrame): InterpreterError | undefined {
  const parsed = parseJsonRecord(frame.data)
  if (parsed === undefined) return undefined
  const traceback = Array.isArray(parsed.traceback) ? parsed.traceback.map((line) => String(line)) : []
  return {
    name: String(parsed.ename ?? ''),
    value: String(parsed.evalue ?? ''),
    traceback,
  }
}

function parseJsonRecord(payload: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(payload)
    return isJsonRecord(parsed) ? parsed : undefined
  } catch (error) {
    if (error instanceof SyntaxError) return undefined
    throw error
  }
}

function mimeBundle(parsed: Record<string, unknown>): Record<string, unknown> | undefined {
  if ('data' in parsed) {
    const rawData = parsed.data
    if (isJsonRecord(rawData)) return rawData
    if (typeof parsed.mime === 'string') return { [parsed.mime]: rawData }
    return undefined
  }
  return parsed
}

function stringMime(data: Record<string, unknown>, mime: string): string | undefined {
  const value = data[mime]
  return typeof value === 'string' ? value : undefined
}

export class InterpreterContext {
  public readonly id: string
  public readonly language: string
  public readonly sandboxId: string
  readonly #transport: HttpTransport
  readonly #executionQueue: ContextExecutionQueue
  readonly #onClose: () => Promise<void>

  constructor(
    transport: HttpTransport,
    sandboxId: string,
    id: string,
    language: string,
    executionQueue: ContextExecutionQueue,
    onClose: () => Promise<void>,
  ) {
    this.#transport = transport
    this.#executionQueue = executionQueue
    this.#onClose = onClose
    this.sandboxId = sandboxId
    this.id = id
    this.language = language
  }

  public toJSON(): InterpreterContextJSON {
    return {
      sandboxId: this.sandboxId,
      contextId: this.id,
      language: this.language,
    }
  }

  /**
   * Execute code inside this explicit interpreter context.
   * If the named context vanished, the typed SESSION_NOT_FOUND error
   * surfaces unchanged so the caller can recreate the context and decide what
   * code, if any, is safe to rerun.
   */
  public async run(code: string, options: Omit<InterpreterRunOptions, 'sessionId' | 'language' | 'isolated' | 'context'> = {}): Promise<InterpreterResult> {
    assertExplicitContextEnvUnsupported(options.env)
    return this.#executionQueue.run(
      options.timeout,
      async (remainingTimeout) => {
        const deadline = remainingTimeout === undefined ? undefined : Date.now() + remainingTimeout
        const handle = await startProcess(this.#transport, this.sandboxId, {
          code,
          kind: 'code',
          language: this.language,
          sessionId: this.id,
          cwd: options.cwd,
        }, undefined, remainingTimeout === undefined ? undefined : { timeout: Math.max(1, remainingTimeout) })
        const timeoutAfterStart = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
        return collectResult(handle, timeoutAfterStart, deadline)
      },
      timedOutInterpreterResult,
    )
  }

  public async close(): Promise<void> {
    await this.#onClose()
  }
}

export class InterpreterService {
  private readonly contextQueues = new Map<string, ContextExecutionQueue>()

  constructor(
    private readonly transport: HttpTransport,
    private readonly process: ProcessService,
  ) {}

  private queueFor(sessionId: string): ContextExecutionQueue {
    const existing = this.contextQueues.get(sessionId)
    if (existing) return existing
    const queue = new ContextExecutionQueue()
    this.contextQueues.set(sessionId, queue)
    return queue
  }


  private removeQueueIfSame(sessionId: string, queue: ContextExecutionQueue): void {
    if (this.contextQueues.get(sessionId) === queue) this.contextQueues.delete(sessionId)
  }

  private evictQueueWhenDrained(sessionId: string, queue: ContextExecutionQueue | undefined): void {
    if (queue === undefined) return
    if (queue.requestEviction(() => this.removeQueueIfSame(sessionId, queue))) {
      this.removeQueueIfSame(sessionId, queue)
    }
  }

  private async deleteContextQueue(
    sessionId: string,
    queue: ContextExecutionQueue | undefined,
    ignoreMissing: boolean,
  ): Promise<void> {
    try {
      await this.transport.delete<void>(`/sessions/${strictUriEncode(sessionId)}`)
    } catch (error) {
      if (isSessionNotFoundError(error)) {
        this.evictQueueWhenDrained(sessionId, queue)
        if (ignoreMissing) return
      }
      throw error
    }
    this.evictQueueWhenDrained(sessionId, queue)
  }

  private async deleteContextBestEffort(sessionId: string, queue: ContextExecutionQueue): Promise<void> {
    try {
      await this.deleteContextQueue(sessionId, queue, true)
    } catch (error) {
      if (!(error instanceof DaytonaError)) throw error
    }
  }

  public async createContext(options: InterpreterContextOptions): Promise<InterpreterContext> {
    const session = await this.transport.post<toolbox.SessionDto>('/sessions', {
      id: options.id,
      kernel: options.language,
      cwd: options.cwd,
      env: options.env,
    })
    return new InterpreterContext(
      this.transport,
      this.process.sandboxId,
      session.id,
      options.language,
      this.queueFor(session.id),
      () => this.deleteContextQueue(session.id, this.contextQueues.get(session.id), false),
    )
  }

  public connectContext(sessionId: string, language: string): InterpreterContext
  public connectContext(context: InterpreterContextJSON): InterpreterContext
  public connectContext(
    sessionIdOrContext: string | InterpreterContextJSON,
    language?: string,
  ): InterpreterContext {
    const identity = typeof sessionIdOrContext === 'string'
      ? { sandboxId: this.process.sandboxId, contextId: sessionIdOrContext, language }
      : assertContextBelongsToSandbox(sessionIdOrContext, this.process.sandboxId)
    const resolvedLanguage = identity.language
    if (resolvedLanguage === undefined) {
      throw badRequest('language is required when reconnecting an interpreter context by id')
    }
    const queue = this.queueFor(identity.contextId)
    return new InterpreterContext(
      this.transport,
      this.process.sandboxId,
      identity.contextId,
      resolvedLanguage,
      queue,
      () => this.deleteContextQueue(identity.contextId, queue, false),
    )
  }

  public async listContexts(): Promise<InterpreterContext[]> {
    const data = await this.transport.get<toolbox.SessionListResponse | toolbox.SessionDto[]>('/sessions')
    const sessions = Array.isArray(data) ? data : data.items
    // `/sessions` serves language kernels only.
    return sessions.map((session) => this.connectContext(session.id, session.kernel))
  }

  public async deleteContext(sessionIdOrContext: string | InterpreterContext | InterpreterContextJSON): Promise<void> {
    const sessionId = typeof sessionIdOrContext === 'string'
      ? sessionIdOrContext
      : assertContextBelongsToSandbox(sessionIdOrContext, this.process.sandboxId).contextId
    await this.deleteContextQueue(sessionId, this.contextQueues.get(sessionId), false)
  }

  /**
   * Execute code and return the parsed interpreter result.
   * Rehydrated `kind:'code'` process handles can replay logs and wait/kill,
   * but they do not recreate this decomposed result on another worker.
   * A vanished explicit context surfaces the typed SESSION_NOT_FOUND
   * error; callers decide whether to recreate the context and what to rerun.
   */
  public async run(code: string, options: InterpreterRunOptions = {}): Promise<InterpreterResult> {
    const language = options.language ?? 'python'
    if (options.isolated && (options.sessionId !== undefined || options.context !== undefined)) {
      throw badRequest('isolated runs cannot also target an explicit interpreter context')
    }
    if (options.context !== undefined && options.sessionId !== undefined) {
      throw badRequest('interpreter runs accept either context or sessionId, not both')
    }
    if (options.isolated) {
      // The throwaway session's POST /sessions is part of this run and spends
      // the same caller budget as the execution itself (matches Python).
      validateOptionalTimeout(options.timeout)
      const startedAt = Date.now()
      const sessionId = isolatedContextId(language)
      const sessionQueue = this.queueFor(sessionId)
      const sessionTimeout = options.timeout === undefined || options.timeout === 0 ? undefined : { timeout: Math.max(1, options.timeout) }
      try {
        await this.transport.post<toolbox.SessionDto>('/sessions', {
          id: sessionId,
          kernel: language,
          cwd: options.cwd,
          env: options.env,
        }, sessionTimeout)
        const remaining = remainingBudgetMs(options.timeout, startedAt)
        if (remaining !== undefined && remaining <= 0) {
          throw createDaytonaError('interpreter run timed out while creating its isolated context', 408, 'REQUEST_TIMEOUT', 'SDK')
        }
        return await this.connectContext(sessionId, language).run(code, {
          cwd: options.cwd,
          timeout: remaining,
        })
      } finally {
        await this.deleteContextBestEffort(sessionId, sessionQueue)
      }
    }
    if (options.context !== undefined) {
      const context = assertContextBelongsToSandbox(options.context, this.process.sandboxId)
      assertExplicitContextEnvUnsupported(options.env)
      return this.connectContext(context).run(code, {
        cwd: options.cwd,
        timeout: options.timeout,
      })
    }
    if (options.sessionId !== undefined) {
      assertExplicitContextEnvUnsupported(options.env)
      return this.connectContext(options.sessionId, language).run(code, {
        cwd: options.cwd,
        timeout: options.timeout,
      })
    }
    return this.queueFor(implicitContextId(language)).run(
      options.timeout,
      async (remainingTimeout) => {
        const deadline = remainingTimeout === undefined ? undefined : Date.now() + remainingTimeout
        const handle = await startProcess(this.transport, this.process.sandboxId, {
          code,
          kind: 'code',
          language,
          cwd: options.cwd,
          env: options.env,
        }, undefined, remainingTimeout === undefined ? undefined : { timeout: Math.max(1, remainingTimeout) })
        const timeoutAfterStart = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
        return collectResult(handle, timeoutAfterStart, deadline)
      },
      timedOutInterpreterResult,
    )
  }
}
