/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// allow: SIZE_OK — One resumable build-log state machine owns discovery, cursors, and cancellation.

import { queryText } from './pageParams.js'
import { ConnectionError, createDaytonaError, DaytonaError, errorFromEnvelopeBody, errorOptions, registerV2ErrorClass, ClientTimeoutError } from './errors/DaytonaError.js'
import type { ErrorOptions } from './errors/DaytonaError.js'
import { MAX_TIMER_DELAY_MS, boundedTimerDelay, badRequest, waitDeadline } from './options.js'
import { HttpTransport } from './http.js'
import type * as api from './gen/api-types.js'
import { sameBuildLogResource } from './buildLogIdentity.js'
import { BUILD_LOG_DONE_REASONS, BUILD_LOG_GAP_REASONS } from './types.js'
import type { BuildLogDoneReason, BuildLogGapReason } from './types.js'
import { knownOrUnknown } from './knownValue.js'
import { assertKnownOptions, assertOptionsObject, optionKeys, optionalSeconds } from './options.js'
import { deepFrozen } from './frozen.js'
import { nestedDeeperThan } from './jsonDepth.js'

export type { BuildLogDoneReason } from './types.js'

export interface BuildLogFollowResult {
  readonly status: string
  readonly reason: BuildLogDoneReason
  readonly rawReason: string
  readonly cursor?: string
  readonly outputComplete: boolean
}
export type BuildLogEvent =
  | { readonly type: 'state'; readonly status: string; readonly error?: string; readonly deadlineAt?: string }
  | { readonly type: 'log'; readonly dataBase64: string; readonly startOffset: string; readonly nextOffset: string; readonly cursor: string }
  | { readonly type: 'gap'; readonly reason: BuildLogGapReason; readonly rawReason: string; readonly requestedOffset: string; readonly firstAvailableOffset: string; readonly droppedBytes: string; readonly cursor: string }
  | { readonly type: 'unavailable'; readonly reason: string }
  | { readonly type: 'source_changed'; readonly reason: string }
  | { readonly type: 'done'; readonly result: BuildLogFollowResult }
  | { readonly type: 'error'; readonly error: string; readonly code: string; readonly retryable: boolean; readonly traceId?: string }

export interface BuildLogFollowOptions {
  readonly onLogs: (chunk: string) => void | Promise<void>
  readonly onEvent?: (event: BuildLogEvent) => void | Promise<void>
  readonly cursor?: string
  /** Total observation budget in seconds; zero, omitted or above about 24.8 days adds no deadline. */
  readonly timeoutSeconds?: number
  readonly signal?: AbortSignal
}

const BUILD_LOG_FOLLOW_OPTION_KEYS = optionKeys<BuildLogFollowOptions>()({ onLogs: true, onEvent: true, cursor: true, timeoutSeconds: true, signal: true })

/** Last successfully delivered checkpoint. Callback failures never auto-retry. */
/** {@link BuildLogInterruptedError}'s options: the common ones plus the last delivered cursor. */
export interface BuildLogInterruptedErrorOptions extends ErrorOptions {
  readonly cursor?: string
}

/**
 * Observation failed; `cursor` is the last successfully delivered checkpoint.
 * `code` defaults to `BUILD_LOG_INTERRUPTED` and `source` to `SDK`.
 */
export class BuildLogInterruptedError extends DaytonaError {
  public readonly cursor?: string

  constructor(message: string, options?: BuildLogInterruptedErrorOptions) {
    const { cursor, ...rest } = errorOptions(options) ?? {}
    super(message, { ...rest, code: rest.code ?? 'BUILD_LOG_INTERRUPTED', source: rest.source ?? 'SDK' })
    this.cursor = cursor
  }
}

class ProtocolError extends Error {}
class Retry extends Error {
  constructor(message: string, readonly retryAfter = 0) { super(message) }
}
class CallbackError extends Error {
  constructor(readonly cause: unknown) { super('build log callback failed') }
}
const MAX_EVENT = 128 * 1024
// Bound one-shot responses client-side without trusting Content-Length.
const MAX_ONE_SHOT_BYTES = 2 * 1024 * 1024

/** JSON-boundary scratch values never escape the validated DTO constructors. */
function jsonObject(json: string): Record<string, unknown> {
  const value: unknown = JSON.parse(json)
  // Nested deeper than every JSON body may be (`JSON_MAX_DEPTH`, as in Python).
  if (nestedDeeperThan(value)) throw new ProtocolError('JSON nested too deeply')
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new ProtocolError('expected JSON object')
  // SAFETY: JSON.parse produced a non-null, non-array object, whose keys are
  // strings. This assertion establishes indexing only: every property remains
  // unknown until checked below. It does not assert any DTO field or value type.
  return value as Record<string, unknown>
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new ProtocolError('expected string')
  return value
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new ProtocolError('expected boolean')
  return value
}
function nullableText(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return value
  return text(value)
}
function offset(str: string): bigint {
  if (!/^(0|[1-9][0-9]{0,19})$/.test(str) || BigInt(str) > 18446744073709551615n) throw new ProtocolError('invalid uint64 offset')
  return BigInt(str)
}
// Cursors are opaque. One rule in both SDKs and both directions: non-empty,
// bounded, and no C0 control character or DEL; offset continuity comes from
// the event's public byte-offset fields.
function cursorOk(str: string): boolean {
  // The 2048 bound counts code points, as Python's len() does (not UTF-16 units).
  return str.length > 0 && codePoints(str) <= 2048 && !/[\x00-\x1f\x7f]/.test(str)
}
function codePoints(str: string): number {
  let count = 0
  for (const _ of str) count++
  return count
}
function cursor(str: string): string {
  if (!cursorOk(str)) throw new ProtocolError('invalid cursor')
  return str
}
/** A caller's resume cursor: BadRequestError naming it, before any request, as in Python; it is sent as a query value. */
function callerCursor(value: unknown): string {
  if (typeof value !== 'string' || !cursorOk(value)) throw badRequest('cursor must be a non-empty string of at most 2048 characters with no control characters')
  return queryText(value, 'cursor')
}
function bytes(str: string): Uint8Array {
  if (str.length > 87384 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(str)) throw new ProtocolError('invalid base64')
  const raw = atob(str)
  if (raw.length > 65536 || btoa(raw) !== str) throw new ProtocolError('invalid log chunk')
  return Uint8Array.from(raw, char => char.charCodeAt(0))
}

type Message = { id?: string } & (
  | { event: 'state'; data: api.BuildLogState }
  | { event: 'log'; data: api.BuildLogChunk }
  | { event: 'gap'; data: api.BuildLogGap }
  | { event: 'unavailable'; data: api.BuildLogUnavailable }
  | { event: 'source_changed'; data: api.BuildLogSourceChanged }
  | { event: 'done'; data: api.BuildLogDone }
  | { event: 'error'; data: api.BuildLogError }
)

/** Construct generated wire DTOs at the JSON boundary; tolerate extra fields. */
function parseMessage(event: string, id: string | undefined, json: string): Message {
  const data = jsonObject(json)
  switch (event) {
    case 'state': return { event, id, data: {
      status: text(data.status),
      error: nullableText(data.error), deadline_at: nullableText(data.deadline_at),
    } }
    case 'log': return { event, id, data: {
      data_base64: text(data.data_base64), start_offset: text(data.start_offset), next_offset: text(data.next_offset),
    } }
    case 'gap': {
      return { event, id, data: {
        reason: text(data.reason), requested_offset: text(data.requested_offset),
        first_available_offset: text(data.first_available_offset), dropped_bytes: text(data.dropped_bytes),
      } }
    }
    case 'unavailable': return { event, id, data: { reason: text(data.reason) } }
    case 'source_changed': return { event, id, data: { reason: text(data.reason) } }
    case 'done': return { event, id, data: {
      status: text(data.status), reason: text(data.reason),
      output_complete: boolean(data.output_complete), cursor: nullableText(data.cursor),
    } }
    case 'error': return { event, id, data: {
      error: text(data.error), code: text(data.code), retryable: boolean(data.retryable),
      trace_id: nullableText(data.trace_id),
    } }
    default: throw new ProtocolError('unknown build log event')
  }
}

function parseDiscovery(json: string): api.BuildLogsUrl {
  const data = jsonObject(json)
  return { url: text(data.url) }
}

function requiredId(id: string | undefined): string {
  if (id === undefined) throw new ProtocolError('log event requires a cursor')
  return id
}
async function closeReader(reader?: ReadableStreamDefaultReader<Uint8Array>, cancel = true): Promise<void> {
  try {
    if (cancel) await reader?.cancel()
  } catch {
    // An errored stream rejects cancel too. Cleanup must not replace the
    // protocol, transport, or callback exception that selected this exit.
  } finally { reader?.releaseLock() }
}
async function cancelResponse(response?: Response): Promise<void> {
  try {
    if (response?.body && !response.body.locked) await response.body.cancel()
  } catch {
    // Preserve the primary failure on an already errored response.
  }
}
/**
 * Byte framing bounds both complete and unfinished events before decoding.
 * @internal
 */
export async function* buildLogMessages(response: Response): AsyncIterable<Message> {
  if (!response.body) return
  const reader = response.body.getReader()
  const line = new Uint8Array(MAX_EVENT)
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
  let used = 0, size = 0, afterCR = false, event = 'message'
  let firstLine = true
  let id: string | undefined
  let data: string[] = []
  let closed = false
  try {
    for (;;) {
      let next: ReadableStreamReadResult<Uint8Array>
      try { next = await reader.read() } catch (error) {
        throw new ConnectionError(error instanceof Error ? error.message : String(error))
      }
      if (next.done) { closed = true; return } // Never dispatch an incomplete tail.
      for (const byte of next.value) {
        if (afterCR && byte === 10 && size === 0) { afterCR = false; continue }
        // Reserve the possible LF before dispatching a blank CR; it can arrive
        // in the next chunk and must not count toward the following event.
        if (++size > MAX_EVENT || (byte === 13 && size === MAX_EVENT)) throw new ProtocolError('SSE event exceeds 128 KiB')
        if (afterCR) {
          afterCR = false
          if (byte === 10) continue
        }
        if (byte !== 10 && byte !== 13) { line[used++] = byte; continue }
        afterCR = byte === 13
        let value: string
        try { value = decoder.decode(line.subarray(0, used)) } catch { throw new ProtocolError('invalid SSE UTF-8') }
        if (firstLine) { value = value.replace(/^\uFEFF/, ''); firstLine = false }
        used = 0
        if (value === '') {
          if (data.length) {
            let message: Message
            try { message = parseMessage(event, id, data.join('\n')) } catch (error) {
              if (error instanceof ProtocolError) throw error
              throw new ProtocolError('invalid event JSON')
            }
            yield message
          } else if (id !== undefined) throw new ProtocolError('event ID without data')
          event = 'message'; id = undefined; data = []; size = 0
          continue
        }
        const colon = value.indexOf(':')
        const field = colon < 0 ? value : value.slice(0, colon)
        value = colon < 0 ? '' : value.slice(colon + 1).replace(/^ /, '')
        if (field === 'event') event = value
        else if (field === 'id') {
          if (id !== undefined || colon < 0) throw new ProtocolError('duplicate or missing SSE ID')
          id = value
        } else if (field === 'data') data.push(value)
      }
    }
  } finally {
    await closeReader(reader, !closed)
  }
}

class Observer {
  cursor?: string
  private offset?: bigint
  private decoder = new TextDecoder('utf-8', { ignoreBOM: true })
  private gap = false

  constructor(
    private readonly options: BuildLogFollowOptions,
    private readonly signal: AbortSignal,
  ) {
    this.cursor = options.cursor === undefined ? undefined : cursor(options.cursor)
    this.offset = this.cursor === undefined ? 0n : undefined
  }
  private async emit(event: BuildLogEvent, output = ''): Promise<void> {
    // Frozen before any callback sees it: a callback cannot rewrite the event,
    // nor the `done` result the call returns.
    deepFrozen(event)
    try {
      if (output) await interruptible(Promise.resolve(this.options.onLogs(output)), this.signal)
      if (this.options.onEvent) await interruptible(Promise.resolve(this.options.onEvent(event)), this.signal)
      else if (['state', 'gap', 'unavailable', 'source_changed', 'error'].includes(event.type)) {
        await interruptible(Promise.resolve(this.options.onLogs(`[build logs: ${event.type}] ${JSON.stringify(event)}\n`)), this.signal)
      }
    } catch (cause) { throw new CallbackError(cause) }
  }
  private checkpoint(id: string): string {
    const current = cursor(id)
    if (current === this.cursor) throw new ProtocolError('cursor did not advance with bytes')
    return current
  }
  async accept({ event, id, data }: Message): Promise<BuildLogFollowResult | undefined> {
    if (!['log', 'gap', 'done'].includes(event) && id !== undefined) throw new ProtocolError('control event must not advance cursor')
    switch (event) {
      case 'state': {
        const deadlineAt = data.deadline_at ?? undefined, error = data.error ?? undefined
        // Execution deadlines are metadata, not client observation deadlines.
        if (deadlineAt !== undefined) {
          const expires = Date.parse(deadlineAt)
          if (!Number.isFinite(expires) || !/(Z|[+-]\d\d:\d\d)$/.test(deadlineAt)) throw new ProtocolError('invalid build deadline')
        }
        await this.emit({ type: 'state', status: data.status, deadlineAt, error })
        break
      }
      case 'log': {
        const raw = bytes(data.data_base64), start = offset(data.start_offset), end = offset(data.next_offset)
        if (!raw.length || end - start !== BigInt(raw.length) || (this.offset !== undefined && start !== this.offset)) throw new ProtocolError('noncontiguous log offsets')
        const checkpoint = this.checkpoint(requiredId(id))
        await this.emit({ type: 'log', dataBase64: data.data_base64, startOffset: data.start_offset, nextOffset: data.next_offset, cursor: checkpoint }, this.decoder.decode(raw, { stream: true }))
        this.cursor = checkpoint; this.offset = end
        break
      }
      case 'gap': {
        const requested = offset(data.requested_offset), first = offset(data.first_available_offset), dropped = offset(data.dropped_bytes)
        if (first <= requested || first - requested !== dropped || (this.offset !== undefined && requested !== this.offset)) throw new ProtocolError('invalid log gap')
        const checkpoint = this.checkpoint(requiredId(id))
        await this.emit({
          type: 'gap',
          reason: knownOrUnknown(data.reason, BUILD_LOG_GAP_REASONS),
          rawReason: data.reason,
          requestedOffset: data.requested_offset,
          firstAvailableOffset: data.first_available_offset,
          droppedBytes: data.dropped_bytes,
          cursor: checkpoint,
        }, this.decoder.decode())
        this.decoder = new TextDecoder('utf-8', { ignoreBOM: true })
        this.cursor = checkpoint; this.offset = first; this.gap = true
        break
      }
      case 'unavailable': {
        await this.emit({ type: 'unavailable', reason: data.reason })
        break
      }
      case 'source_changed': {
        await this.emit({ type: 'source_changed', reason: data.reason })
        throw new BuildLogInterruptedError(data.reason, { cursor: this.cursor, code: 'BUILD_LOG_SOURCE_CHANGED' })
      }
      case 'error': {
        await this.emit({ type: 'error', error: data.error, code: data.code, retryable: data.retryable, traceId: data.trace_id ?? undefined })
        if (data.retryable) throw new Retry(data.error)
        throw new BuildLogInterruptedError(data.error, { cursor: this.cursor, code: data.code })
      }
      case 'done': {
        const final = data.cursor ?? undefined
        let checkpointCursor = this.cursor
        for (const checkpoint of [id, final]) {
          if (checkpoint !== undefined && checkpoint !== checkpointCursor) {
            if (checkpointCursor !== undefined) throw new ProtocolError('done advanced cursor without bytes')
            checkpointCursor = cursor(checkpoint) // An empty source has no log event.
          }
        }
        const result: BuildLogFollowResult = {
          status: data.status,
          reason: knownOrUnknown(data.reason, BUILD_LOG_DONE_REASONS),
          rawReason: data.reason,
          cursor: checkpointCursor,
          outputComplete: data.output_complete && !this.gap,
        }
        await this.emit({ type: 'done', result }, this.decoder.decode())
        this.cursor = checkpointCursor
        return result
      }
      default: throw new ProtocolError('unknown build log event')
    }
    return undefined
  }
}

function destination(api: HttpTransport, payload: api.BuildLogsUrl, discoveryPath: string): HttpTransport {
  const { url: raw } = payload
  if (!raw || raw.length > 4096 || /[\x00-\x20\x7f\\?#]/.test(raw)) throw new ProtocolError('invalid build log URL')
  let url: URL
  try {
    if (!/^https?:\/\//i.test(raw) || /^https?:\/\/[^/]*@/i.test(raw)) throw new ProtocolError('build log URL must be absolute without userinfo')
    url = new URL(raw)
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) throw new ProtocolError('invalid build log URL')
    // The destination's collection is opaque; validate the resource id using
    // the discovery request's resource type, not the URL's collection name.
    const requested = /^\/(images|sandboxes)\/([^/]+)\/build-logs-url$/.exec(discoveryPath)
    const actual = /\/build-logs\/[^/]+\/([^/%]+)$/.exec(url.pathname)
    const requestedCollection = requested?.[1]
    if (!requested || !actual || (requestedCollection !== 'images' && requestedCollection !== 'sandboxes') ||
        !sameBuildLogResource(requestedCollection, decodeURIComponent(requested[2]!), actual[1]!)) {
      throw new ProtocolError('unexpected build log route')
    }
  } catch { throw new ProtocolError('invalid build log URL') }
  return api.withBaseUrl(url.toString())
}
function requestDeadline(timeout: number): number | undefined {
  const delay = boundedTimerDelay(timeout)
  return delay > 0 ? performance.now() + delay : undefined
}

async function finiteBody(response: Response, deadline: number | undefined, kind: 'discovery' | 'error'): Promise<string> {
  // Only finite control/diagnostic bodies use this timer. Healthy SSE bodies
  // remain open until explicit done or caller cancellation.
  const reader = response.body?.getReader()
  let body = '', size = 0
  let expired = false
  let timer: ReturnType<typeof setTimeout> | undefined
  // A discovery body is decoded strictly (invalid UTF-8 and a leading BOM are
  // not JSON, as in Python); an error body stays lenient for error mapping.
  const decoder = kind === 'discovery' ? new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }) : new TextDecoder()
  const decode = (bytes?: Uint8Array): string => {
    try {
      return bytes === undefined ? decoder.decode() : decoder.decode(bytes, { stream: true })
    } catch {
      throw new ProtocolError(`invalid build log ${kind} JSON`)
    }
  }
  const expire = (): void => {
    expired = true
    // Cancel this response, not the follower's controller: HTTP/2 peers and
    // later retry attempts must remain independent. Cancel wakes pending read.
    void reader?.cancel().catch(() => {})
  }
  try {
    if (deadline !== undefined) {
      const budget = deadline - performance.now()
      if (budget <= 0) expire()
      else timer = setTimeout(expire, Math.min(budget, MAX_TIMER_DELAY_MS))
    }
    if (reader) for (;;) {
      if (expired) throw new ClientTimeoutError(`build log ${kind} response timed out`)
      const next = await reader.read()
      if (expired) throw new ClientTimeoutError(`build log ${kind} response timed out`)
      if (next.done) break
      size += next.value.byteLength
      if (size > MAX_EVENT) throw new ProtocolError(`oversized build log ${kind} response`)
      body += decode(next.value)
    }
    if (expired) throw new ClientTimeoutError(`build log ${kind} response timed out`)
    return body + decode()
  } catch (error) {
    if (expired) throw new ClientTimeoutError(`build log ${kind} response timed out`)
    if (error instanceof DaytonaError || error instanceof ProtocolError) throw error
    throw new ConnectionError(error instanceof Error ? error.message : String(error))
  } finally {
    clearTimeout(timer)
    await closeReader(reader)
  }
}

async function httpError(response: Response, deadline: number | undefined): Promise<DaytonaError> {
  try {
    const body = await finiteBody(response, deadline, 'error')
    return errorFromEnvelopeBody(body, response.headers.get('content-type'), response.status, `build log HTTP ${response.status}`, true)
  } catch (error) {
    // A denied request stays denied even if its explanatory JSON stalls. Do
    // not turn a known 401/403/etc into an automatically retried timeout.
    if (error instanceof ClientTimeoutError || error instanceof ConnectionError) return createDaytonaError(`build log HTTP ${response.status}`, response.status)
    throw error
  }
}
function retryAfter(response: Response): number {
  const value = response.headers.get('retry-after') ?? ''
  if (/^\d+$/.test(value)) return Number(value) * 1000
  const date = Date.parse(value)
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0
}
async function discovery(api: HttpTransport, path: string, signal?: AbortSignal, retry = true): Promise<api.BuildLogsUrl> {
  const deadline = requestDeadline(api.defaultTimeout)
  const response = await api.fetch(path, { signal, redirect: 'manual', credentials: 'omit' }, false)
  try {
    if (retry && (response.status === 429 || response.status >= 500)) throw new Retry(`build log discovery HTTP ${response.status}`, retryAfter(response))
    if (!response.ok) throw await httpError(response, deadline)
    const body = await finiteBody(response, deadline, 'discovery')
    try { return parseDiscovery(body) } catch (error) {
      if (error instanceof ProtocolError) throw error
      throw new ProtocolError('invalid build log discovery JSON')
    }
  } finally {
    await cancelResponse(response)
  }
}
async function readOneShotBody(response: Response): Promise<string> {
  const reader = response.body?.getReader()
  // Preserve a leading BOM as text, like Python's UTF-8 decoder and the live
  // follower. Streaming decode also preserves characters split across chunks.
  const decoder = new TextDecoder('utf-8', { ignoreBOM: true })
  let body = '', size = 0
  try {
    if (reader) for (;;) {
      const next = await reader.read()
      if (next.done) break
      if (next.value.byteLength > MAX_ONE_SHOT_BYTES - size) throw new ProtocolError('one-shot build log response exceeds 2 MiB')
      size += next.value.byteLength
      body += decoder.decode(next.value, { stream: true })
    }
    return body + decoder.decode()
  } finally {
    // Cancel unread bytes on overflow/error, then release our reader lock.
    await closeReader(reader)
  }
}

/** @internal */
export async function readBuildLogs(api: HttpTransport, path: string): Promise<string> {
  const controller = new AbortController()
  const readTimeout = boundedTimerDelay(api.defaultTimeout)
  const timer = readTimeout > 0 ? setTimeout(() => controller.abort(), readTimeout) : undefined
  try {
    const payload = await discovery(api, path, controller.signal, false)
    const target = destination(api, payload, path)
    const deadline = requestDeadline(target.defaultTimeout)
    const response = await target.fetch('?follow=false&format=text', { redirect: 'manual', credentials: 'omit', signal: controller.signal }, false)
    if (!response.ok) throw await httpError(response, deadline)
    return await readOneShotBody(response)
  } catch (error) {
    // httpError has already preserved the known HTTP response, even when its
    // diagnostic body was interrupted by our abort timer.
    if (error instanceof DaytonaError && error.statusCode !== undefined && !(error instanceof ClientTimeoutError || error instanceof ConnectionError)) throw error
    if (controller.signal.aborted) throw new ClientTimeoutError('build log read timed out')
    if (error instanceof ProtocolError) throw createDaytonaError(error.message, undefined, 'BUILD_LOG_PROTOCOL_ERROR', 'SDK')
    throw error
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
}

/** @internal */
export async function followBuildLogs(api: HttpTransport, path: string, options: BuildLogFollowOptions): Promise<BuildLogFollowResult> {
  assertOptionsObject('BuildLogFollowOptions', options)
  assertKnownOptions('BuildLogFollowOptions', options, BUILD_LOG_FOLLOW_OPTION_KEYS)
  const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
  // Invalid arguments are BadRequestError before any request, as in Python.
  if (typeof options.onLogs !== 'function') throw badRequest('onLogs must be a function')
  const onEvent: unknown = options.onEvent
  if (onEvent !== undefined && typeof onEvent !== 'function') throw badRequest('onEvent must be a function when given')
  if (options.cursor !== undefined) callerCursor(options.cursor)
  const controller = new AbortController()
  const relay = (): void => controller.abort(options.signal?.reason)
  if (options.signal?.aborted) relay()
  else options.signal?.addEventListener('abort', relay, { once: true })
  // Omitted, 0, or above the timer maximum (about 24.8 days): no deadline.
  const callerDeadline = waitDeadline(timeoutSeconds, performance.now())
  let timer: ReturnType<typeof setTimeout> | undefined
  let observer: Observer | undefined
  const remaining = (): number | undefined => {
    if (controller.signal.aborted) throw new BuildLogInterruptedError('build log observation canceled or timed out', { cursor: observer?.cursor ?? options.cursor, code: options.signal?.aborted ? 'BUILD_LOG_CANCELED' : 'REQUEST_TIMEOUT' })
    // Only the caller's observation timeout aborts before explicit completion.
    const budget = callerDeadline === undefined ? undefined : callerDeadline - performance.now()
    if (budget !== undefined && budget <= 0) {
      controller.abort()
      throw new BuildLogInterruptedError('build log observation timed out', { cursor: observer?.cursor ?? options.cursor, code: 'REQUEST_TIMEOUT' })
    }
    return budget
  }
  const arm = (): void => {
    clearTimeout(timer)
    const budget = remaining()
    if (budget !== undefined) timer = setTimeout(() => controller.abort(), budget)
  }
  try {
    observer = new Observer(options, controller.signal)
    arm()
    let target: HttpTransport | undefined
    let failures = 0
    for (;;) {
      remaining()
      let started = performance.now()
      let healthy = false
      let response: Response | undefined
      let delay: number
      try {
      if (!target) target = destination(api, await discovery(api, path, controller.signal), path)
        const query = new URLSearchParams({ follow: 'true', format: 'sse' })
        if (observer.cursor !== undefined) query.set('cursor', observer.cursor)
        const deadline = requestDeadline(target.defaultTimeout)
        response = await target.fetch(`?${query}`, { signal: controller.signal, redirect: 'manual', credentials: 'omit', headers: { Accept: 'text/event-stream' } }, false)
        if (!response.ok) {
          if (response.status === 429 || response.status >= 500) throw new Retry(`build log HTTP ${response.status}`, retryAfter(response))
          throw await httpError(response, deadline)
        }
        if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'text/event-stream') throw new ProtocolError('expected text/event-stream')
        started = performance.now()
        healthy = true
        for await (const message of buildLogMessages(response)) {
          remaining()
          const previous = observer.cursor
          const result = await observer.accept(message)
          remaining()
          if (observer.cursor !== previous || performance.now() - started >= 10000) failures = 0
          if (result !== undefined) return result
        }
        throw new Retry('unexpected EOF before build log done')
      } catch (error) {
        remaining()
        if (!(error instanceof Retry || error instanceof ConnectionError || error instanceof ClientTimeoutError)) throw error
        if (healthy && performance.now() - started >= 10000) failures = 0
        if (failures >= 5) throw new BuildLogInterruptedError('build log reconnect budget exhausted', { cursor: observer.cursor })
        delay = [250, 500, 1000, 2000, 5000][failures++] * (0.8 + Math.random() * 0.2)
        if (error instanceof Retry) delay = Math.max(delay, error.retryAfter)
      } finally {
        await cancelResponse(response)
      }
      const budget = remaining()
      await pause(budget === undefined ? delay : Math.min(delay, budget), controller.signal)
    }
  } catch (cause) {
    if (cause instanceof BuildLogInterruptedError) throw cause
    let code = cause instanceof CallbackError ? 'BUILD_LOG_CALLBACK_ERROR' : cause instanceof ProtocolError ? 'BUILD_LOG_PROTOCOL_ERROR' : cause instanceof DaytonaError ? cause.code : undefined
    if (controller.signal.aborted) code = options.signal?.aborted ? 'BUILD_LOG_CANCELED' : 'REQUEST_TIMEOUT'
    throw new BuildLogInterruptedError(cause instanceof Error ? cause.message : String(cause), {
      cursor: observer?.cursor ?? options.cursor,
      code,
      statusCode: cause instanceof DaytonaError ? cause.statusCode : undefined,
      cause,
    })
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', relay)
    controller.abort()
  }
}
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = (): void => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
    const timer = setTimeout(finish, Math.min(ms, MAX_TIMER_DELAY_MS))
    signal.addEventListener('abort', finish, { once: true })
    if (signal.aborted) finish()
  })
}

function interruptible(pending: Promise<void>, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = (): void => {
      signal.removeEventListener('abort', abort)
      reject(new DOMException('build log observation canceled', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
    // Keep rejection handlers on user work even after cancellation. We stop
    // awaiting it, not its application-side effects, and never acknowledge it.
    pending.then(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, error => {
      signal.removeEventListener('abort', abort)
      reject(error)
    })
    if (signal.aborted) abort()
  })
}

registerV2ErrorClass('BuildLogInterruptedError', BuildLogInterruptedError)
