/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The size cap on response bodies the SDK buffers whole, and the idle bound on
 * file transfers (internal).
 *
 * Every response the transports read into memory (JSON, the in-memory file
 * reads, multipart bulk reads, error bodies) is read through
 * {@link readCappedBody}: a declared `Content-Length` above the limit is
 * refused before the body is read, and otherwise reading stops as soon as the
 * bytes read pass it; the body (and so the connection) is then cancelled.
 * `0` means no limit. Streamed bodies (`downloadFile`, log, event and
 * build-log streams, watches) are never held whole and are not capped.
 */

import { badRequest } from './options.js'

/** The default cap on a buffered response body: 1 GiB. */
export const DEFAULT_MAX_RESPONSE_BYTES = 1_073_741_824

/**
 * `value` as a body limit: an integer from 0 to `Number.MAX_SAFE_INTEGER`;
 * `undefined` / `null` mean "not set". Anything else is a `BadRequestError`.
 */
export function maxResponseBytesOption(name: string, value: unknown): number | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw badRequest(`${name} must be an integer from 0 (no limit) to ${Number.MAX_SAFE_INTEGER}`)
  }
  return value
}

/** The message of a refused success response (`PayloadTooLargeError`). */
export function tooLargeMessage(limit: number): string {
  return `the response body is larger than the ${limit}-byte maxResponseBytes limit; pass a higher maxResponseBytes `
    + 'for this call (0 = no limit), or use downloadFile() for large files'
}

/** The message of a refused error response, which keeps its status's error. */
export function tooLargeErrorStatusMessage(status: number, limit: number): string {
  return `HTTP ${status}: the response body was too large to read (over the ${limit}-byte maxResponseBytes limit)`
}

/** Thrown by {@link readCappedBody} when a body passes its limit. */
export class BodyTooLarge extends Error {
  constructor(readonly limit: number) {
    super(`response body over ${limit} bytes`)
  }
}

interface NodeReadable {
  on(event: string, listener: (...args: unknown[]) => void): unknown
  destroy(error?: Error): unknown
  [Symbol.asyncIterator](): AsyncIterator<unknown>
}

function isNodeReadable(value: unknown): value is NodeReadable {
  return typeof value === 'object' && value !== null
    && typeof (value as { on?: unknown }).on === 'function'
    && typeof (value as { destroy?: unknown }).destroy === 'function'
    && typeof (value as { [Symbol.asyncIterator]?: unknown })[Symbol.asyncIterator] === 'function'
}

function isWebStream(value: unknown): value is ReadableStream<Uint8Array> {
  return typeof value === 'object' && value !== null && typeof (value as { getReader?: unknown }).getReader === 'function'
}

function chunkBytes(chunk: unknown): Uint8Array {
  if (chunk instanceof Uint8Array) return chunk
  if (chunk instanceof ArrayBuffer) return new Uint8Array(chunk)
  if (ArrayBuffer.isView(chunk)) return new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength)
  if (typeof chunk === 'string') return new TextEncoder().encode(chunk)
  return new Uint8Array(0)
}

function concat(chunks: readonly Uint8Array[], size: number): Uint8Array {
  if (chunks.length === 1) return chunks[0]
  const out = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) {
    out.set(chunk, offset)
    offset += chunk.byteLength
  }
  return out
}

/** A declared `Content-Length` (header value) above `limit`. */
export function declaresMoreThan(contentLength: unknown, limit: number): boolean {
  if (limit <= 0) return false
  const declared = typeof contentLength === 'string' ? contentLength.trim() : typeof contentLength === 'number' ? String(contentLength) : ''
  return /^\d+$/.test(declared) && Number(declared) > limit
}

/** Hooks a reader calls around each read (the transfer idle watchdog). */
export interface ReadHooks {
  /** Before waiting for the next chunk. */
  readonly waiting?: () => void
  /** After a chunk (or the end) arrived. */
  readonly received?: () => void
}

/**
 * The whole body of `data` (a Node.js readable, a web `ReadableStream`, or an
 * already materialized string / buffer) as bytes, at most `limit` bytes
 * (`0` = no limit). Past the limit it cancels the body (closing the
 * connection) and throws {@link BodyTooLarge}; on any other failure it
 * cancels it too and rethrows.
 */
export async function readCappedBody(data: unknown, limit: number, hooks: ReadHooks = {}, release?: () => void): Promise<Uint8Array> {
  const chunks: Uint8Array[] = []
  let size = 0
  const add = (chunk: Uint8Array): void => {
    size += chunk.byteLength
    if (limit > 0 && size > limit) throw new BodyTooLarge(limit)
    chunks.push(chunk)
  }
  if (isNodeReadable(data)) {
    const iterator = data[Symbol.asyncIterator]()
    try {
      for (;;) {
        hooks.waiting?.()
        const next = await iterator.next()
        hooks.received?.()
        if (next.done) break
        add(chunkBytes(next.value))
      }
    } catch (error) {
      data.destroy()
      release?.()
      throw error
    }
    return concat(chunks, size)
  }
  if (isWebStream(data)) {
    const reader = data.getReader()
    try {
      for (;;) {
        hooks.waiting?.()
        const next = await reader.read()
        hooks.received?.()
        if (next.done) break
        add(chunkBytes(next.value))
      }
    } catch (error) {
      await reader.cancel(error).catch(() => undefined)
      release?.()
      throw error
    } finally {
      reader.releaseLock()
    }
    return concat(chunks, size)
  }
  if (data === undefined || data === null || data === '') return new Uint8Array(0)
  add(chunkBytes(data))
  return concat(chunks, size)
}

/**
 * A request body small enough (64 KiB) that the runtime hands it to the
 * socket at once, so a wait after sending it is a wait for the response, not
 * an upload still in progress. A transfer's idle bound starts at once for
 * one; for a larger body the runtime sends without progress events, it starts
 * at the response headers, so a long upload that is still moving is never
 * cut. A body of unknown size (a stream, an unknown object) is not small.
 */
export const SMALL_REQUEST_BODY_BYTES = 64 * 1024

export function isSmallRequestBody(method: string | undefined, data: unknown): boolean {
  if (data === undefined || data === null || ['get', 'head'].includes((method ?? 'get').toLowerCase())) return true
  const size = (value: unknown): number | undefined => {
    if (typeof value === 'string') return value.length * 3
    if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return value.byteLength
    if (typeof Blob !== 'undefined' && value instanceof Blob) return value.size
    if (typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) return value.toString().length
    return undefined
  }
  if (typeof FormData !== 'undefined' && data instanceof FormData) {
    let total: number | undefined = 0
    data.forEach((value) => {
      const entry = size(value)
      total = total === undefined || entry === undefined ? undefined : total + entry
    })
    return total !== undefined && total <= SMALL_REQUEST_BODY_BYTES
  }
  const bytes = size(data)
  return bytes !== undefined && bytes <= SMALL_REQUEST_BODY_BYTES
}

/** Cancel a body without reading it (closing its connection). */
export async function discardBody(data: unknown, release?: () => void): Promise<void> {
  if (isNodeReadable(data)) data.destroy()
  else if (isWebStream(data)) await data.cancel().catch(() => undefined)
  release?.()
}

/**
 * Bytes as the value axios would have produced for `responseType`: an
 * `ArrayBuffer`, a `Blob`, or (text / json / unset) a UTF-8 string without a
 * byte-order mark, which axios' `transformResponse` then parses as before.
 */
export function bytesAs(bytes: Uint8Array, responseType: string | undefined): unknown {
  if (responseType === 'arraybuffer') {
    return bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
      ? bytes.buffer
      : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
  }
  if (responseType === 'blob' && typeof Blob !== 'undefined') return new Blob([bytes.slice()])
  return new TextDecoder().decode(bytes)
}

/**
 * The idle bound of a file transfer: `signal` aborts (with a reason the
 * transport reads as its timeout) once `idleMs` pass with no progress while
 * the timer runs. `0` disables it. The caller's own signal is relayed.
 */
export class IdleWatchdog {
  private readonly controller = new AbortController()
  private timer: ReturnType<typeof setTimeout> | undefined
  private expired = false
  private readonly relay: () => void

  constructor(private readonly idleMs: number, private readonly caller: AbortSignal | undefined, private readonly reason: () => unknown) {
    this.relay = () => this.controller.abort(caller?.reason)
    if (caller?.aborted) this.relay()
    else caller?.addEventListener('abort', this.relay, { once: true })
  }

  public get signal(): AbortSignal {
    return this.controller.signal
  }

  /** Whether the idle bound (not the caller) aborted the transfer. */
  public fired(): boolean {
    return this.expired
  }

  /** (Re)start the countdown: progress was seen, or a wait begins. */
  public kick(): void {
    if (this.idleMs <= 0 || this.controller.signal.aborted) return
    if (this.timer !== undefined) clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      this.expired = true
      this.controller.abort(this.reason())
    }, this.idleMs)
    const unref = (this.timer as { unref?: () => void }).unref
    if (typeof unref === 'function') unref.call(this.timer)
  }

  /** Stop the countdown (nothing is being waited for). */
  public pause(): void {
    if (this.timer !== undefined) clearTimeout(this.timer)
    this.timer = undefined
  }

  /** Stop for good and drop the caller relay. */
  public dispose(): void {
    this.pause()
    this.caller?.removeEventListener('abort', this.relay)
  }
}
