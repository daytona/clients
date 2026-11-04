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
import { strictUriEncode } from './http.js'
import type * as api from './gen/api-types.js'
import { BadRequestError, ConnectionError, DaytonaError, NotSupportedError, ServiceUnavailableError } from './errors/DaytonaError.js'

/** A build-context tar: its bytes, a `Blob`, or (Node.js) the path of a `.tar` file. */
export type BuildContext = Uint8Array | Blob | string

export const MAX_CONTEXT_BYTES = 1024 * 1024 * 1024
const HASH_CHUNK = 8 * 1024 * 1024
const PART_ATTEMPTS = 3

/** Test seams: the storage client and the pause between part attempts. */
export const contextUploadRuntime = {
  fetch: (input: string, init: RequestInit): Promise<Response> => globalThis.fetch(input, init),
  sleep: (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms)),
}

function badRequest(message: string): BadRequestError {
  return new BadRequestError(message, 400, 'BAD_REQUEST', 'SDK')
}

function isNode(): boolean {
  // SAFETY: every member is optional, and the result is only compared to a string.
  return typeof (globalThis as { process?: { versions?: { node?: unknown } } }).process?.versions?.node === 'string'
}

interface NodeFileHandle {
  stat(): Promise<{ isFile(): boolean; size: number }>
  read(buffer: Uint8Array, offset: number, length: number, position: number): Promise<{ bytesRead: number }>
  close(): Promise<void>
}

/** The archive, read by offset: a `Blob` slice, or (Node.js) a file handle. */
interface ContextSource {
  readonly size: number
  read(offset: number, length: number): Promise<Uint8Array>
  close(): Promise<void>
}

function blobSource(blob: Blob): ContextSource {
  return {
    size: blob.size,
    read: async (offset, length) => new Uint8Array(await blob.slice(offset, offset + length).arrayBuffer()),
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

async function sourceOf(context: BuildContext): Promise<ContextSource> {
  if (typeof context !== 'string') {
    if (context instanceof Uint8Array) {
      return { size: context.byteLength, read: async (offset, length) => context.subarray(offset, offset + length), close: async () => undefined }
    }
    if (typeof Blob !== 'undefined' && context instanceof Blob) return blobSource(context)
    throw new TypeError('context must be the bytes of a tar archive, a Blob, or the path of a tar file')
  }
  if (!isNode()) throw new NotSupportedError('a build context path is only supported in Node.js; pass its bytes or a Blob', 501, 'NOT_IMPLEMENTED', 'SDK')
  return fileSource(context)
}

interface NodeHash {
  update(data: Uint8Array): void
  digest(encoding: 'hex'): string
}

async function sha256Hex(source: ContextSource): Promise<string> {
  if (isNode()) {
    const cryptoSpecifier = 'node:crypto'
    // SAFETY: the shape is node:crypto's documented createHash signature.
    const crypto = await (import(/* @vite-ignore */ /* webpackIgnore: true */ cryptoSpecifier) as Promise<{ createHash(algorithm: 'sha256'): NodeHash }>)
    const hash = crypto.createHash('sha256')
    for (let offset = 0; offset < source.size; offset += HASH_CHUNK) {
      hash.update(await source.read(offset, Math.min(HASH_CHUNK, source.size - offset)))
    }
    return hash.digest('hex')
  }
  // SAFETY: read() returns a view over a plain ArrayBuffer, never a shared one.
  const whole = (await source.read(0, source.size)) as Uint8Array<ArrayBuffer>
  const digest = await globalThis.crypto.subtle.digest('SHA-256', whole)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function putPart(source: ContextSource, offset: number, part: api.BuildContextUploadPart): Promise<void> {
  // SAFETY: read() returns a view over a plain ArrayBuffer, never a shared one.
  const body = (await source.read(offset, part.bytes)) as Uint8Array<ArrayBuffer>
  if (body.byteLength !== part.bytes) throw badRequest('build context changed while it was being uploaded')
  let status: number | undefined
  for (let attempt = 0; attempt < PART_ATTEMPTS; attempt++) {
    try {
      const response = await contextUploadRuntime.fetch(part.url, { method: 'PUT', body, redirect: 'error' })
      status = response.status
      await response.body?.cancel().catch(() => undefined)
    } catch {
      // The failure names the URL, so it is not kept as a cause.
      status = undefined
    }
    if (status !== undefined && status >= 200 && status < 300) return
    if (status !== undefined && status < 500 && status !== 408 && status !== 429) {
      throw badRequest(`storage refused build context part ${part.part_number} (HTTP ${status}); its upload URL may have expired, start the build again`)
    }
    if (attempt + 1 < PART_ATTEMPTS) await contextUploadRuntime.sleep(500 * 2 ** attempt)
  }
  if (status === undefined) throw new ConnectionError(`build context part ${part.part_number} could not reach storage`, undefined, undefined, 'SDK')
  throw new ServiceUnavailableError(`storage did not accept build context part ${part.part_number} (HTTP ${status})`, 503, 'SERVICE_UNAVAILABLE', 'SDK')
}

/** Upload `context`; resolve to the ID and region of its verified upload. */
export async function uploadBuildContext(
  http: AxiosInstance,
  context: BuildContext,
  options: { readonly dockerfile: string; readonly region?: string },
): Promise<{ readonly id: string; readonly regionId: string }> {
  if (options.dockerfile.trim() === '') throw badRequest('dockerfile must be a non-empty path')
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
    const { data: upload } = await http.post<api.BuildContextUpload>('/build-contexts', request)
    if (upload.state === 'ready') return { id: upload.id, regionId: upload.region_id }
    let offset = 0
    for (const part of upload.parts) {
      await putPart(source, offset, part)
      offset += part.bytes
    }
    if (offset !== source.size) throw new DaytonaError('build context upload parts do not cover the archive', undefined, undefined, 'SDK')
    const complete: api.CompleteBuildContextUploadRequest = { region_id: upload.region_id }
    await http.post<api.BuildContextUpload>(`/build-contexts/${strictUriEncode(upload.id)}/complete`, complete)
    return { id: upload.id, regionId: upload.region_id }
  } finally {
    await source.close().catch(() => undefined)
  }
}
