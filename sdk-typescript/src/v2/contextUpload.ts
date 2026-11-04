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

interface NodeFs {
  readonly stat: (path: string) => Promise<{ isFile(): boolean }>
  readonly openAsBlob: (path: string) => Promise<Blob>
}

async function blobOf(context: BuildContext): Promise<Blob> {
  if (typeof context !== 'string') {
    // SAFETY: Blob copies the view's bytes; it never keeps or resizes the buffer.
    if (context instanceof Uint8Array) return new Blob([context as Uint8Array<ArrayBuffer>])
    if (typeof Blob !== 'undefined' && context instanceof Blob) return context
    throw new TypeError('context must be the bytes of a tar archive, a Blob, or the path of a tar file')
  }
  if (!isNode()) throw new NotSupportedError('a build context path is only supported in Node.js; pass its bytes or a Blob', 501, 'NOT_IMPLEMENTED', 'SDK')
  // Runtime specifiers keep browser bundlers from resolving the Node builtins.
  const [fsSpecifier, fsPromisesSpecifier] = ['node:fs', 'node:fs/promises']
  // SAFETY: the shapes are the Node.js builtins' documented signatures.
  const [fs, fsPromises] = await Promise.all([
    import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier) as Promise<Pick<NodeFs, 'openAsBlob'>>,
    import(/* @vite-ignore */ /* webpackIgnore: true */ fsPromisesSpecifier) as Promise<Pick<NodeFs, 'stat'>>,
  ])
  const info = await fsPromises.stat(context).catch(() => undefined)
  if (info === undefined || !info.isFile()) throw badRequest(`build context ${JSON.stringify(context)} is not a file; pass a tar archive`)
  return fs.openAsBlob(context)
}

interface NodeHash {
  update(data: Uint8Array): void
  digest(encoding: 'hex'): string
}

async function sha256Hex(blob: Blob): Promise<string> {
  if (isNode()) {
    const cryptoSpecifier = 'node:crypto'
    // SAFETY: the shape is node:crypto's documented createHash signature.
    const crypto = await (import(/* @vite-ignore */ /* webpackIgnore: true */ cryptoSpecifier) as Promise<{ createHash(algorithm: 'sha256'): NodeHash }>)
    const hash = crypto.createHash('sha256')
    for (let offset = 0; offset < blob.size; offset += HASH_CHUNK) {
      hash.update(new Uint8Array(await blob.slice(offset, offset + HASH_CHUNK).arrayBuffer()))
    }
    return hash.digest('hex')
  }
  const digest = await globalThis.crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function putPart(blob: Blob, offset: number, part: api.BuildContextUploadPart): Promise<void> {
  const body = new Uint8Array(await blob.slice(offset, offset + part.bytes).arrayBuffer())
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
  const blob = await blobOf(context)
  if (blob.size === 0) throw badRequest('build context is empty')
  if (blob.size > MAX_CONTEXT_BYTES) throw badRequest(`build context exceeds the 1 GiB limit (${blob.size} bytes)`)
  const request: api.CreateBuildContextUploadRequest = {
    dockerfile: options.dockerfile,
    bytes: blob.size,
    sha256: await sha256Hex(blob),
    ...(options.region === undefined ? {} : { region_id: options.region }),
  }
  const { data: upload } = await http.post<api.BuildContextUpload>('/build-contexts', request)
  if (upload.state === 'ready') return { id: upload.id, regionId: upload.region_id }
  let offset = 0
  for (const part of upload.parts) {
    await putPart(blob, offset, part)
    offset += part.bytes
  }
  if (offset !== blob.size) throw new DaytonaError('build context upload parts do not cover the archive', undefined, undefined, 'SDK')
  const complete: api.CompleteBuildContextUploadRequest = { region_id: upload.region_id }
  await http.post<api.BuildContextUpload>(`/build-contexts/${strictUriEncode(upload.id)}/complete`, complete)
  return { id: upload.id, regionId: upload.region_id }
}
