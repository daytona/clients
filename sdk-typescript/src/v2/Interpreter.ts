/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ProcessHandle, TIMED_OUT_LOG_PAGE_LIMIT, collectAllLogs, startProcess } from './Process.js'
import { HttpTransport, runtimeUniqueId } from './http.js'
import type {
  InterpreterContextJSON,
  InterpreterContextOptions,
  InterpreterContextReference,
  InterpreterContextRunOptions,
  InterpreterError,
  InterpreterOutput,
  InterpreterResult,
  InterpreterRunOptions,
  LogFrame,
  WaitResult,
} from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import { secondsToMs } from './ProcessWire.js'
import { envelopeList, interpreterSessionFromWire } from './toolboxModels.js'
import { assertInternal, INTERNAL } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import { deepFrozen } from './frozen.js'

const RUN_OPTION_KEYS = optionKeys<InterpreterRunOptions>()({
  language: true, context: true, isolated: true, cwd: true, env: true, timeoutSeconds: true,
})
const CONTEXT_RUN_OPTION_KEYS = optionKeys<InterpreterContextRunOptions>()({ cwd: true, timeoutSeconds: true })
const CONTEXT_OPTION_KEYS = optionKeys<InterpreterContextOptions>()({ language: true, contextId: true, cwd: true, env: true })
/** `/sessions` also lists the legacy shell kernel, which is not an interpreter context. */
const SHELL_SESSION_KERNEL = 'shell'

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

function queueDeadline(timeoutMs: number | undefined): number | undefined {
  if (timeoutMs === undefined || timeoutMs === 0) return undefined
  return Date.now() + timeoutMs
}

/**
 * HTTP budget for a setup request (context creation, the code POST) of a run
 * with caller budget `timeoutMs`: an explicit `0` is unbounded, omitted keeps
 * the client's request timeout, a positive budget bounds it.
 */
function setupConfig(timeoutMs: number | undefined, remainingMs: number | undefined): { readonly timeout: number } | undefined {
  if (timeoutMs === 0) return { timeout: 0 }
  if (remainingMs === undefined) return undefined
  return { timeout: Math.max(1, remainingMs) }
}

function isSessionNotFoundError(error: unknown): error is DaytonaError {
  return error instanceof DaytonaError && error.code === 'SESSION_NOT_FOUND'
}

function isJsonRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function contextIdentity(context: InterpreterContextReference | InterpreterContextJSON): InterpreterContextJSON {
  if (!isJsonRecord(context)
    || typeof context.sandboxId !== 'string'
    || typeof context.contextId !== 'string'
    || typeof context.language !== 'string') {
    throw badRequest('interpreter context identity needs string sandboxId, contextId and language')
  }
  return { sandboxId: context.sandboxId, contextId: context.contextId, language: context.language }
}

function assertContextBelongsToSandbox(
  context: InterpreterContextReference | InterpreterContextJSON,
  sandboxId: string,
): InterpreterContextJSON {
  const identity = contextIdentity(context)
  if (identity.sandboxId !== sandboxId) throw badRequest('interpreter context belongs to a different sandbox')
  return identity
}

/** Caller budget (ms) left after `startedAt`; `undefined`/`0` keep their meaning. */
function remainingBudgetMs(timeoutMs: number | undefined, startedAt: number): number | undefined {
  if (timeoutMs === undefined || timeoutMs === 0) return timeoutMs
  return timeoutMs - (Date.now() - startedAt)
}

function waitMs(handle: ProcessHandle, timeoutMs: number | undefined): Promise<WaitResult> {
  return handle.wait(timeoutMs === undefined ? {} : { timeoutSeconds: timeoutMs / 1000 })
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
    : await waitMs(handle, timeout)
  // A `timed_out` result with an exit code is the code's own deadline (the
  // process has ended): nothing to interrupt, as in the Python SDK.
  if (result.reason !== 'timed_out' || result.exitCode !== undefined) return result
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
        return await waitMs(handle, INTERRUPT_GRACE_MS)
      }
      if (!(error instanceof DaytonaError && error.code === 'UNSUPPORTED_OPERATION')) throw error
      const wait = await waitMs(handle, INTERRUPT_RETRY_MS)
      if (wait.reason !== 'timed_out') return wait
    }
  }
  if (!interrupted) return result
  return await waitMs(handle, INTERRUPT_GRACE_MS)
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
  return deepFrozen({
    process: { ...result, processId: handle.id },
    results: outputs,
    logs: { stdout, stderr },
    ...(errors.length === 0 ? {} : { error: errors[errors.length - 1] }),
  })
}

function timedOutInterpreterResult(): InterpreterResult {
  return deepFrozen({
    process: { reason: 'timed_out' },
    results: [],
    logs: { stdout: '', stderr: '' },
  })
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

/** Run a context with a caller budget the service already holds in milliseconds (`0` = unbounded). */
function runContext(context: InterpreterContext, code: string, cwd: string | undefined, timeoutMs: number | undefined): Promise<InterpreterResult> {
  return context.run(code, {
    ...(cwd === undefined ? {} : { cwd }),
    ...(timeoutMs === undefined ? {} : { timeoutSeconds: timeoutMs / 1000 }),
  })
}

/**
 * An interpreter context: kernel state of its own in one sandbox. Built by
 * the interpreter service (`createContext()`, `connectContext()`,
 * `listContexts()`), never directly. Serialize it with `toJSON()` and
 * reconnect with `interpreter.connectContext()`.
 */
export class InterpreterContext {
  public readonly contextId: string
  public readonly language: string
  public readonly sandboxId: string
  readonly #transport: HttpTransport
  readonly #executionQueue: ContextExecutionQueue
  readonly #onClose: () => Promise<void>

  /** @internal */
  constructor(
    token: Internal,
    transport: HttpTransport,
    sandboxId: string,
    contextId: string,
    language: string,
    executionQueue: ContextExecutionQueue,
    onClose: () => Promise<void>,
  )
  /** Built by the SDK: get it from `sandbox.interpreter.createContext()` or `connectContext()`. */
  constructor(token: Internal, ...init: never[])
  constructor(
    token: Internal,
    transport: HttpTransport,
    sandboxId: string,
    contextId: string,
    language: string,
    executionQueue: ContextExecutionQueue,
    onClose: () => Promise<void>,
  ) {
    assertInternal(token, 'InterpreterContext')
    this.#transport = transport
    this.#executionQueue = executionQueue
    this.#onClose = onClose
    this.sandboxId = sandboxId
    this.contextId = contextId
    this.language = language
  }

  /** The context's JSON-safe identity, for `interpreter.connectContext()`. */
  public toJSON(): InterpreterContextJSON {
    return {
      sandboxId: this.sandboxId,
      contextId: this.contextId,
      language: this.language,
    }
  }

  /**
   * Execute code inside this interpreter context. Environment variables are
   * set when the context is created. If the context vanished, the typed
   * SESSION_NOT_FOUND error surfaces unchanged so the caller can recreate the
   * context and decide what code, if any, is safe to rerun.
   */
  public async run(code: string, options: InterpreterContextRunOptions = {}): Promise<InterpreterResult> {
    assertKnownOptions('InterpreterContextRunOptions', options, CONTEXT_RUN_OPTION_KEYS)
    return this.#runMs(code, options.cwd, secondsToMs('timeoutSeconds', options.timeoutSeconds))
  }

  /** Run with a caller budget in milliseconds (`0` = unbounded). */
  async #runMs(code: string, cwd: string | undefined, timeoutMs: number | undefined): Promise<InterpreterResult> {
    return this.#executionQueue.run(
      timeoutMs,
      async (remainingTimeout) => {
        const deadline = remainingTimeout === undefined ? undefined : Date.now() + remainingTimeout
        const handle = await startProcess(this.#transport, this.sandboxId, {
          code,
          kind: 'code',
          language: this.language,
          sessionId: this.contextId,
          cwd,
          keepLogs: 'on_exit_ttl',
        }, setupConfig(timeoutMs, remainingTimeout))
        const timeoutAfterStart = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
        return collectResult(handle, timeoutAfterStart, deadline)
      },
      timedOutInterpreterResult,
    )
  }

  /** Delete this context and its kernel state. */
  public async close(): Promise<void> {
    await this.#onClose()
  }
}

/** Sandbox interpreter namespace (`sandbox.interpreter`): run code in language kernels. */
export class InterpreterService {
  private readonly contextQueues = new Map<string, ContextExecutionQueue>()
  private readonly transport: HttpTransport
  private readonly sandboxId: string

  /** @internal */
  constructor(token: Internal, transport: HttpTransport, sandboxId: string)
  /** Built by the SDK: get it from `sandbox.interpreter`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: HttpTransport, sandboxId: string) {
    assertInternal(token, 'InterpreterService')
    this.transport = transport
    this.sandboxId = sandboxId
  }

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

  private bindContext(contextId: string, language: string): InterpreterContext {
    // Every context identity round-trips through toJSON(): a non-string id or
    // language would be written in a shape neither SDK reads back.
    if (typeof contextId !== 'string' || contextId === '') throw badRequest('interpreter context id must be a non-empty string')
    if (typeof language !== 'string' || language === '') throw badRequest('interpreter context language must be a non-empty string')
    const queue = this.queueFor(contextId)
    return new InterpreterContext(
      INTERNAL,
      this.transport,
      this.sandboxId,
      contextId,
      language,
      queue,
      () => this.deleteContextQueue(contextId, queue, false),
    )
  }

  /** Create an interpreter context: kernel state of its own. `cwd` and `env` apply to every run in it. */
  public async createContext(options: InterpreterContextOptions): Promise<InterpreterContext> {
    assertKnownOptions('InterpreterContextOptions', options, CONTEXT_OPTION_KEYS)
    const session = await this.transport.post<toolbox.SessionDto>('/sessions', {
      id: options.contextId,
      kernel: options.language,
      cwd: options.cwd,
      env: options.env,
    })
    return this.bindContext(interpreterSessionFromWire(session, options.contextId).id, options.language)
  }

  /** Reconnect a context by id (with `language`), handle or `toJSON()` identity, without a request. */
  public connectContext(context: string | InterpreterContextReference | InterpreterContextJSON, language?: string): InterpreterContext {
    if (typeof context !== 'string') {
      const identity = assertContextBelongsToSandbox(context, this.sandboxId)
      return this.bindContext(identity.contextId, identity.language)
    }
    if (language === undefined) throw badRequest('language is required when reconnecting an interpreter context by id')
    return this.bindContext(context, language)
  }

  public async listContexts(): Promise<InterpreterContext[]> {
    const data = await this.transport.get<toolbox.SessionListResponse>('/sessions')
    return envelopeList(data, 'items', 'session list response', interpreterSessionFromWire)
      .filter((session) => session.kernel !== SHELL_SESSION_KERNEL)
      .map((session) => this.bindContext(session.id, session.kernel))
  }

  public async deleteContext(context: string | InterpreterContextReference | InterpreterContextJSON): Promise<void> {
    const contextId = typeof context === 'string'
      ? context
      : assertContextBelongsToSandbox(context, this.sandboxId).contextId
    if (typeof contextId !== 'string' || contextId === '') throw badRequest('interpreter context id must be a non-empty string')
    await this.deleteContextQueue(contextId, this.contextQueues.get(contextId), false)
  }

  /**
   * Execute code and return the parsed interpreter result. Without `context`
   * (or `isolated`) runs share kernel state per `language` (default
   * `'python'`); `context` selects an interpreter context by handle,
   * `toJSON()` identity or id (an id needs `language`); `isolated: true`
   * runs in a throwaway context.
   * Rehydrated `kind:'code'` process handles can replay logs and wait/kill,
   * but they do not recreate this decomposed result on another worker.
   * A vanished explicit context surfaces the typed SESSION_NOT_FOUND
   * error; callers decide whether to recreate the context and what to rerun.
   */
  public async run(code: string, options: InterpreterRunOptions = {}): Promise<InterpreterResult> {
    assertKnownOptions('InterpreterRunOptions', options, RUN_OPTION_KEYS)
    const timeoutMs = secondsToMs('timeoutSeconds', options.timeoutSeconds)
    if (options.isolated && options.context !== undefined) {
      throw badRequest('isolated runs cannot also target an explicit interpreter context')
    }
    if (options.context !== undefined) {
      const context = this.connectContext(options.context, options.language)
      if (options.language !== undefined && options.language !== context.language) {
        throw badRequest(`language '${options.language}' does not match the context's language '${context.language}'`)
      }
      if (options.env !== undefined) {
        throw badRequest('env is only supported when creating a context or running without an explicit context')
      }
      return runContext(context, code, options.cwd, timeoutMs)
    }
    const language = options.language ?? 'python'
    if (options.isolated) {
      // The throwaway session's POST /sessions is part of this run and spends
      // the same caller budget as the execution itself (matches Python).
      const startedAt = Date.now()
      const sessionId = isolatedContextId(language)
      // The response is the truth: run in and clean up the id the sandbox
      // returned (its queue), even if it differs from the requested one.
      let cleanupId = sessionId
      try {
        const created = interpreterSessionFromWire(await this.transport.post<toolbox.SessionDto>('/sessions', {
          id: sessionId,
          kernel: language,
          cwd: options.cwd,
          env: options.env,
        }, setupConfig(timeoutMs, timeoutMs)), sessionId)
        cleanupId = created.id
        const remaining = remainingBudgetMs(timeoutMs, startedAt)
        if (timeoutMs !== undefined && timeoutMs > 0 && remaining !== undefined && remaining <= 0) {
          throw createDaytonaError('interpreter run timed out while creating its isolated context', 408, 'REQUEST_TIMEOUT', 'SDK')
        }
        return await runContext(this.bindContext(created.id, language), code, options.cwd, remaining)
      } finally {
        await this.deleteContextBestEffort(cleanupId, this.queueFor(cleanupId))
      }
    }
    return this.queueFor(implicitContextId(language)).run(
      timeoutMs,
      async (remainingTimeout) => {
        const deadline = remainingTimeout === undefined ? undefined : Date.now() + remainingTimeout
        const handle = await startProcess(this.transport, this.sandboxId, {
          code,
          kind: 'code',
          language,
          cwd: options.cwd,
          env: options.env,
          keepLogs: 'on_exit_ttl',
        }, setupConfig(timeoutMs, remainingTimeout))
        const timeoutAfterStart = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
        return collectResult(handle, timeoutAfterStart, deadline)
      },
      timedOutInterpreterResult,
    )
  }
}
