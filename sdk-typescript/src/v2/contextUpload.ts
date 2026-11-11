/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Direct build-context uploads (`POST /build-contexts`). The API declares one
// upload, hands back presigned part URLs for an object key it chose, and
// verifies the stored archive when the upload is completed. The part URLs are
// bearer credentials: they never reach a log, an error message or a thrown
// value's cause.

import type { AxiosInstance } from 'axios'
import { pathSegment } from './pathSegment.js'
import { badRequest, boundedTimerDelay } from './options.js'
import { IdleWatchdog } from './bodyLimit.js'
import { DEFAULT_REQUEST_TIMEOUT_MS } from './http.js'
import { binaryBytes, isArrayBuffer } from './filesystemInputs.js'
import { Sha256 } from './sha256.js'
import type * as api from './gen/api-types.js'
import { BadRequestError, ClientTimeoutError, ConnectionError, DaytonaError, isRedirectStatus, NotSupportedError, redirectError, ServiceUnavailableError, TransferCancelledError } from './errors/DaytonaError.js'

/** A build-context tar: its bytes (an `ArrayBuffer` or any view), a `Blob`, or (Node.js) the path of a `.tar` file. */
export type BuildContext = Uint8Array | ArrayBuffer | ArrayBufferView | Blob | string

export const MAX_CONTEXT_BYTES = 1024 * 1024 * 1024
const HASH_CHUNK = 8 * 1024 * 1024
// A Node.js part body is streamed in pieces this size, so each piece sent is
// progress the no-progress limit sees, even on a slow link.
const PROGRESS_CHUNK = 64 * 1024
const PART_ATTEMPTS = 3
// Parts in flight at once. Each streams its bytes, so memory stays a few
// chunks whatever the part size.
const PART_CONCURRENCY = 4
const COMPLETE_ATTEMPTS = 3

/**
 * Test seams: the storage client, the pause between attempts, the complete
 * timeout, and the runtime checks.
 * @internal
 */
export const contextUploadRuntime = {
  isNode,
  streamsParts,
  fetch: (input: string, init: RequestInit): Promise<Response> => globalThis.fetch(input, init),
  sleep: (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms)),
  // The API reads the whole stored archive back (up to 1 GiB) before it answers.
  completeTimeoutMs: 10 * 60 * 1000,
}

/**
 * Whether a part is sent as a stream of exactly its length: Node.js itself.
 * Bun and Deno, which also report `process.versions.node`, may send a stream
 * without its length, which a presigned PUT refuses, so an in-memory part
 * goes whole there, as in a browser.
 */
function streamsParts(): boolean {
  // SAFETY: every member is optional, and the result is only compared to a string.
  return (globalThis as { process?: { release?: { name?: unknown } } }).process?.release?.name === 'node'
}

function isNode(): boolean {
  // SAFETY: every member is optional, and the result is only compared to a string.
  return typeof (globalThis as { process?: { versions?: { node?: unknown } } }).process?.versions?.node === 'string'
}

interface NodeFileHandle {
  stat(): Promise<{ isFile(): boolean; size: number; mtimeMs: number }>
  read(buffer: Uint8Array, offset: number, length: number, position: number): Promise<{ bytesRead: number }>
  close(): Promise<void>
}

/**
 * The archive, read by offset: a `Blob` slice, or (Node.js) a file handle.
 * A part body is created per attempt and never holds a whole part in memory:
 * in Node.js a stream over `read`, elsewhere (no streamed request bodies) the
 * in-memory source's `part`, a view or a lazy `Blob` slice.
 */
interface ContextSource {
  readonly size: number
  read(offset: number, length: number): Promise<Uint8Array>
  part?(offset: number, length: number): Uint8Array<ArrayBuffer> | Blob
  changed(): Promise<boolean>
  close(): Promise<void>
}

/** A stream of exactly `length` bytes from `offset`; `progress` runs before each piece is read. */
function partStream(source: ContextSource, offset: number, length: number, progress: () => void): ReadableStream<Uint8Array> {
  let sent = 0
  return new ReadableStream<Uint8Array>({
    pull: async (controller) => {
      progress()
      const chunk = await source.read(offset + sent, Math.min(PROGRESS_CHUNK, length - sent))
      // A short read means the file shrank; the request fails and putPart
      // reports it as a changed context.
      if (chunk.byteLength === 0) return controller.error(badRequest('build context changed while it was being uploaded'))
      controller.enqueue(chunk)
      sent += chunk.byteLength
      if (sent === length) controller.close()
    },
  })
}

function blobSource(blob: Blob): ContextSource {
  return {
    size: blob.size,
    read: async (offset, length) => new Uint8Array(await blob.slice(offset, offset + length).arrayBuffer()),
    part: (offset, length) => blob.slice(offset, offset + length),
    changed: async () => false,
    close: async () => undefined,
  }
}

async function fileSource(path: string): Promise<ContextSource> {
  // A runtime specifier keeps browser bundlers from resolving the Node builtin.
  const fsPromisesSpecifier = 'node:fs/promises'
  // SAFETY: the shape is node:fs/promises' documented open and FileHandle signatures.
  const fs = await (import(/* @vite-ignore */ /* webpackIgnore: true */ fsPromisesSpecifier) as Promise<{ open(path: string, flags: 'r'): Promise<NodeFileHandle> }>)
  const notAFile = badRequest(`build context ${JSON.stringify(path)} is not a file; pass a tar archive`)
  const handle = await fs.open(path, 'r').catch(() => undefined)
  if (handle === undefined) throw notAFile
  const info = await handle.stat().catch(() => undefined)
  if (info === undefined || !info.isFile()) {
    await handle.close().catch(() => undefined)
    throw notAFile
  }
  return {
    size: info.size,
    changed: async () => {
      const now = await handle.stat().catch(() => undefined)
      return now === undefined || now.size !== info.size || now.mtimeMs !== info.mtimeMs
    },
    read: async (offset, length) => {
      const buffer = new Uint8Array(length)
      let filled = 0
      while (filled < length) {
        const { bytesRead } = await handle.read(buffer, filled, length - filled, offset + filled)
        if (bytesRead === 0) break
        filled += bytesRead
      }
      return buffer.subarray(0, filled)
    },
    close: () => handle.close(),
  }
}

function copyBytes(value: Uint8Array): Uint8Array {
  const copy = new Uint8Array(value.byteLength)
  copy.set(value)
  return copy
}

async function sourceOf(context: BuildContext): Promise<ContextSource> {
  if (typeof context !== 'string') {
    if (ArrayBuffer.isView(context) || isArrayBuffer(context)) {
      // Every bytes-like value, as the filesystem writes take it: its exact byte
      // range. Shared memory is copied once: fetch refuses it as a body.
      const given = binaryBytes('context', context)
      // Checked before shared memory is copied (the later check covers every source).
      if (given.byteLength > MAX_CONTEXT_BYTES) throw badRequest(`build context exceeds the 1 GiB limit (${given.byteLength} bytes)`)
      const bytes = isArrayBuffer(given.buffer) ? given : copyBytes(given)
      return {
        size: bytes.byteLength,
        read: async (offset, length) => bytes.subarray(offset, offset + length),
        // SAFETY: a view of the caller's bytes; fetch only reads it.
        part: (offset, length) => bytes.subarray(offset, offset + length) as Uint8Array<ArrayBuffer>,
        changed: async () => false,
        close: async () => undefined,
      }
    }
    if (typeof Blob !== 'undefined' && context instanceof Blob) return blobSource(context)
    throw badRequest('context must be the bytes of a tar archive, a Blob, or the path of a tar file')
  }
  if (!contextUploadRuntime.isNode()) throw new NotSupportedError('a build context path is only supported in Node.js; pass its bytes or a Blob', { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK' })
  return fileSource(context)
}

interface NodeHash {
  update(data: Uint8Array): void
  digest(encoding: 'hex'): string
}

async function sha256Hex(source: ContextSource): Promise<string> {
  let hash: NodeHash
  if (contextUploadRuntime.isNode()) {
    const cryptoSpecifier = 'node:crypto'
    // SAFETY: the shape is node:crypto's documented createHash signature.
    const crypto = await (import(/* @vite-ignore */ /* webpackIgnore: true */ cryptoSpecifier) as Promise<{ createHash(algorithm: 'sha256'): NodeHash }>)
    hash = crypto.createHash('sha256')
  } else {
    const sha = new Sha256()
    hash = { update: (data) => void sha.update(data), digest: () => sha.digestHex() }
  }
  for (let offset = 0; offset < source.size; offset += HASH_CHUNK) {
    hash.update(await source.read(offset, Math.min(HASH_CHUNK, source.size - offset)))
  }
  return hash.digest('hex')
}

/**
 * PUT one part, up to `PART_ATTEMPTS` times. An attempt fails once no bytes
 * move for `idleMs` (`0`: never): connecting, every piece of a streamed body,
 * and the wait for storage's answer each restart the countdown, so a large
 * part on a slow link is never cut while it moves. An in-memory part outside
 * Node.js (a browser, Bun, Deno) is sent whole and its progress cannot be
 * seen, so that attempt is not bounded.
 */
async function putPart(source: ContextSource, offset: number, part: api.BuildContextUploadPart, signal: AbortSignal, idleMs: number): Promise<void> {
  const changed = (): BadRequestError => badRequest('build context changed while it was being uploaded')
  const cancelled = (): TransferCancelledError => new TransferCancelledError('build context upload cancelled')
  let status: number | undefined
  let stalled = false
  for (let attempt = 0; attempt < PART_ATTEMPTS; attempt++) {
    if (signal.aborted) throw cancelled()
    const inMemory = contextUploadRuntime.streamsParts() ? undefined : source.part?.(offset, part.bytes)
    const watchdog = new IdleWatchdog(inMemory === undefined ? idleMs : 0, signal, () => new DOMException('no bytes moved', 'AbortError'))
    const body = inMemory ?? partStream(source, offset, part.bytes, () => watchdog.kick())
    if (!(body instanceof ReadableStream) && (body instanceof Blob ? body.size : body.byteLength) !== part.bytes) throw changed()
    // A presigned PUT needs a fixed length, so a streamed body is not chunked.
    const init: RequestInit & { duplex?: 'half' } =
      body instanceof ReadableStream
        ? { method: 'PUT', body, redirect: 'manual', signal: watchdog.signal, headers: { 'content-length': String(part.bytes) }, duplex: 'half' }
        : { method: 'PUT', body, redirect: 'manual', signal: watchdog.signal }
    stalled = false
    try {
      watchdog.kick()
      const response = await contextUploadRuntime.fetch(part.url, init)
      watchdog.dispose()
      status = response.status
      await response.body?.cancel().catch(() => undefined)
    } catch {
      if (signal.aborted) throw cancelled()
      stalled = watchdog.fired()
      // A streamed part fails to read once its file shrinks.
      if (await source.changed()) throw changed()
      // The failure names the URL, so it is not kept as a cause.
      status = undefined
    } finally {
      watchdog.dispose()
    }
    if (status !== undefined && status >= 200 && status < 300) return
    // The SDK follows no redirect, a storage URL's included (as Python).
    if (isRedirectStatus(status)) throw redirectError(status)
    if (status !== undefined && status < 500 && status !== 408 && status !== 429) {
      throw badRequest(`storage refused build context part ${part.part_number} (HTTP ${status}); its upload URL may have expired, start the build again`)
    }
    if (attempt + 1 < PART_ATTEMPTS) await contextUploadRuntime.sleep(500 * 2 ** attempt)
  }
  if (stalled) {
    throw new ClientTimeoutError(`build context part ${part.part_number} made no progress for ${idleMs / 1000} s`, { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
  }
  if (status === undefined) throw new ConnectionError(`build context part ${part.part_number} could not reach storage`)
  throw new ServiceUnavailableError(`storage did not accept build context part ${part.part_number} (HTTP ${status})`, { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'SDK' })
}

/** Upload every part, at most `PART_CONCURRENCY` at a time; the first failure aborts the rest and is thrown. */
async function putParts(source: ContextSource, parts: readonly api.BuildContextUploadPart[], idleMs: number): Promise<void> {
  const offsets: number[] = []
  let total = 0
  let ordered = true
  parts.forEach((part, index) => {
    if (part.part_number !== index + 1 || part.bytes <= 0) ordered = false
    offsets.push(total)
    total += part.bytes
  })
  if (!ordered || total !== source.size) throw badRequest('build context upload parts do not cover the archive')
  const abort = new AbortController()
  let failure: { readonly error: unknown } | undefined
  let next = 0
  const worker = async (): Promise<void> => {
    while (failure === undefined && next < parts.length) {
      const index = next++
      try {
        await putPart(source, offsets[index], parts[index], abort.signal, idleMs)
      } catch (error) {
        if (failure === undefined) {
          failure = { error }
          abort.abort()
        }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(PART_CONCURRENCY, parts.length) }, worker))
  if (failure !== undefined) throw failure.error
}

function retryableComplete(error: DaytonaError): boolean {
  if (error instanceof ClientTimeoutError || error instanceof ConnectionError) return true
  return error.statusCode !== undefined && error.statusCode >= 500 && error.statusCode !== 501
}

/** Verify the upload. A repeated complete of a verified upload answers with it, so failures are retried. */
async function complete(http: AxiosInstance, uploadId: string, regionId: string): Promise<void> {
  const configured = http.defaults.timeout
  const timeout = configured === 0 ? 0 : Math.max(configured ?? 0, contextUploadRuntime.completeTimeoutMs)
  const request: api.CompleteBuildContextUploadRequest = { region_id: regionId }
  for (let attempt = 0; ; attempt++) {
    try {
      await http.post<api.BuildContextUpload>(`/build-contexts/${pathSegment(uploadId, 'build context upload id')}/complete`, request, { timeout })
      return
    } catch (error) {
      if (attempt + 1 >= COMPLETE_ATTEMPTS || !(error instanceof DaytonaError) || !retryableComplete(error)) throw error
    }
    await contextUploadRuntime.sleep(1000 * 2 ** attempt)
  }
}

/**
 * The `/build-contexts` response, checked field by field before any field is
 * used, with Python's messages: an id, region or state that is not a non-empty
 * string, `parts` that is not an array (a ready upload's included), or a part that is not `{ part_number, bytes, url }` integers and a
 * string, is the malformed-response error.
 */
function checkedUpload(data: unknown): { readonly id: string; readonly regionId: string; readonly ready: boolean; readonly parts: readonly api.BuildContextUploadPart[] } {
  if (typeof data !== 'object' || data === null || Array.isArray(data)) throw badRequest('build context upload response is not an object')
  const record = data as Record<string, unknown>
  const text = (key: string): string => {
    const value = record[key]
    if (typeof value !== 'string' || value === '') throw badRequest(`build context upload response has no ${key}`)
    return value
  }
  const id = text('id')
  const regionId = text('region_id')
  // Every field read is checked first, whatever the state (as Python).
  const state = text('state')
  const raw = record.parts
  if (!Array.isArray(raw)) throw badRequest('build context upload response has no parts')
  const parts = raw.map((item: unknown): api.BuildContextUploadPart => {
    const part = typeof item === 'object' && item !== null && !Array.isArray(item) ? item as Record<string, unknown> : undefined
    const number = part?.part_number, bytes = part?.bytes, url = part?.url
    if (!Number.isSafeInteger(number) || !Number.isSafeInteger(bytes) || typeof url !== 'string') throw badRequest('build context upload response has a malformed part')
    return { part_number: number as number, bytes: bytes as number, url }
  })
  return { id, regionId, ready: state === 'ready', parts }
}

/**
 * Upload `context`; resolve to the ID and region of its verified upload.
 * @internal
 */
export async function uploadBuildContext(
  http: AxiosInstance,
  context: BuildContext,
  options: { readonly dockerfile: string; readonly region?: string },
): Promise<{ readonly id: string; readonly regionId: string }> {
  if (typeof options.dockerfile !== 'string' || options.dockerfile.trim() === '') throw badRequest('dockerfile must be a non-empty path')
  const source = await sourceOf(context)
  try {
    if (source.size === 0) throw badRequest('build context is empty')
    if (source.size > MAX_CONTEXT_BYTES) throw badRequest(`build context exceeds the 1 GiB limit (${source.size} bytes)`)
    const request: api.CreateBuildContextUploadRequest = {
      dockerfile: options.dockerfile,
      bytes: source.size,
      sha256: await sha256Hex(source),
      ...(options.region === undefined ? {} : { region_id: options.region }),
    }
    const { data } = await http.post<unknown>('/build-contexts', request)
    const upload = checkedUpload(data)
    if (upload.ready) return { id: upload.id, regionId: upload.regionId }
    // Checked before any part is sent: the id becomes a path segment of complete.
    pathSegment(upload.id, 'build context upload id')
    // A part fails once no bytes move for the client's request timeout (as
    // the file transfers); it is never bounded by a total deadline.
    await putParts(source, upload.parts, boundedTimerDelay(http.defaults.timeout ?? DEFAULT_REQUEST_TIMEOUT_MS))
    await complete(http, upload.id, upload.regionId)
    return { id: upload.id, regionId: upload.regionId }
  } finally {
    await source.close().catch(() => undefined)
  }
}
