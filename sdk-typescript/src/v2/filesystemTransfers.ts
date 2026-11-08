/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { NotSupportedError, TransferCancelledError, createDaytonaError } from './errors/DaytonaError.js'
import { encodeQuery, HttpTransport, isAbortError, runtimeUniqueId, type FetchRequestInit } from './http.js'

export interface TransferProgressCallback {
  (bytes: number, total?: number): void
}

export interface UploadFileOptions {
  readonly onProgress?: TransferProgressCallback
  readonly signal?: AbortSignal
  readonly atomic?: boolean
}

export interface DownloadFileOptions {
  readonly onProgress?: TransferProgressCallback
  readonly signal?: AbortSignal
}

interface NodeReadableFile {
  [Symbol.asyncIterator](): AsyncIterator<Uint8Array>
  destroy(error?: Error): void
}

interface NodeFsModule {
  createReadStream(path: string): NodeReadableFile
  readonly constants: {
    readonly S_IRUSR: number
    readonly S_IWUSR: number
    readonly S_IRGRP: number
    readonly S_IWGRP: number
    readonly S_IROTH: number
    readonly S_IWOTH: number
  }
}

interface NodeFileHandle {
  write(data: Uint8Array): Promise<number | { readonly bytesWritten: number }>
  chmod(mode: number): Promise<void>
  close(): Promise<void>
}

interface NodeFsPromisesModule {
  stat(path: string): Promise<{ readonly size: number; readonly mode: number }>
  open(path: string, flags: string, mode?: number): Promise<NodeFileHandle>
  rename(oldPath: string, newPath: string): Promise<void>
  unlink(path: string): Promise<void>
}

interface NodeTransferModules {
  readonly fs: NodeFsModule
  readonly fsPromises: NodeFsPromisesModule
}

type ResponseBodyLike = Pick<ReadableStream<Uint8Array>, 'cancel'>

interface RequestInitWithDuplex extends RequestInit {
  duplex?: 'half'
}

const TEXT_ENCODER = new TextEncoder()

function inBrowser(): boolean {
  return typeof globalThis !== 'undefined' &&
    (typeof (globalThis as { window?: unknown }).window !== 'undefined'
      || typeof (globalThis as { importScripts?: unknown }).importScripts === 'function')
}

async function nodeTransfers(methodName: 'downloadFile' | 'uploadFile'): Promise<NodeTransferModules> {
  if (inBrowser()) {
    throw new NotSupportedError(`${methodName}() is only supported in Node.js environments`, { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK' })
  }
  // Runtime specifiers: a literal `import('node:fs')` makes bundlers emit a
  // shared chunk for the builtin shim even though this branch is Node-only.
  const [fsSpecifier, fsPromisesSpecifier] = ['node:fs', 'node:fs/promises']
  const [fs, fsPromises] = await Promise.all([
    import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier) as Promise<NodeFsModule>,
    import(/* @vite-ignore */ /* webpackIgnore: true */ fsPromisesSpecifier) as Promise<NodeFsPromisesModule>,
  ])
  return {
    fs: { createReadStream: fs.createReadStream, constants: fs.constants },
    fsPromises: {
      stat: fsPromises.stat,
      open: fsPromises.open,
      rename: fsPromises.rename,
      unlink: fsPromises.unlink,
    },
  }
}

function assertNotCancelled(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new TransferCancelledError('file transfer cancelled')
  }
}

function normalizeTransferError(error: unknown, signal: AbortSignal | undefined): Error {
  if (error instanceof TransferCancelledError) {
    return error
  }
  if (signal?.aborted) {
    return new TransferCancelledError('file transfer cancelled')
  }
  if (isAbortError(error)) {
    return new TransferCancelledError('file transfer cancelled')
  }
  if (error instanceof Error) {
    return error
  }
  return createDaytonaError(String(error), undefined, undefined, 'SDK')
}

function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, '/')
  const segments = normalized.split('/')
  const candidate = segments.at(-1)
  return candidate && candidate.length > 0 ? candidate : 'file'
}

function escapeMultipartFilename(name: string): string {
  return name.replace(/["\r\n\\]/g, '_')
}

function contentLength(headers: Headers): number | undefined {
  const raw = headers.get('content-length')
  if (raw === null) return undefined
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined
}

function declaredContentLength(headers: Headers): number | undefined {
  const transferEncoding = headers.get('transfer-encoding')
  if (transferEncoding?.split(',').some((value) => value.trim().toLowerCase() === 'chunked')) {
    return undefined
  }
  return contentLength(headers)
}

function ignoreMissingFile(error: unknown): void {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const code = error.code
    if (code === 'ENOENT') {
      return
    }
  }
  throw error
}

/**
 * Create the staging file exclusively (`wx`), retrying with a fresh random name
 * on `EEXIST`. Returns the path only for a file this call created.
 */
/** @internal exported for tests */
export async function openExclusiveStagingFile(
  fsPromises: Pick<NodeFsPromisesModule, 'open'>,
  localPath: string,
): Promise<{ handle: NodeFileHandle; partPath: string }> {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const partPath = downloadTempPath(localPath)
    try {
      return { handle: await fsPromises.open(partPath, 'wx', 0o600), partPath }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EEXIST') continue
      throw error
    }
  }
  throw createDaytonaError('downloadFile() could not allocate a staging file next to the destination', undefined, undefined, 'SDK')
}

function downloadTempPath(localPath: string): string {
  const separatorIndex = Math.max(localPath.lastIndexOf('/'), localPath.lastIndexOf('\\'))
  const directory = separatorIndex >= 0 ? localPath.slice(0, separatorIndex + 1) : ''
  return `${directory}.daytona-dl-${runtimeUniqueId()}`
}

async function removePartialFile(fsPromises: NodeFsPromisesModule, partPath: string): Promise<void> {
  try {
    await fsPromises.unlink(partPath)
  } catch (error) {
    ignoreMissingFile(error)
  }
}

async function existingDestinationMode(fsPromises: NodeFsPromisesModule, path: string): Promise<number | undefined> {
  try {
    return (await fsPromises.stat(path)).mode & 0o777
  } catch (error) {
    if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') {
      return undefined
    }
    throw error
  }
}

function defaultCreatedFileMode(constants: NodeFsModule['constants']): number {
  const readWriteAll = constants.S_IRUSR
    | constants.S_IWUSR
    | constants.S_IRGRP
    | constants.S_IWGRP
    | constants.S_IROTH
    | constants.S_IWOTH
  const currentUmask = typeof process !== 'undefined' && typeof process.umask === 'function'
    ? process.umask()
    : 0
  return readWriteAll & ~currentUmask
}

function reportProgress(
  callback: TransferProgressCallback | undefined,
  bytes: number,
  total: number | undefined,
  lastReported: { value: number },
): void {
  if (callback === undefined) return
  if (lastReported.value === bytes) return
  lastReported.value = bytes
  try {
    callback(bytes, total)
  } catch {
    return
  }
}

function bytesWrittenByCall(result: number | { readonly bytesWritten: number }, attempted: number): number {
  if (typeof result === 'number') return result
  if (typeof result.bytesWritten === 'number') return result.bytesWritten
  return attempted
}

function uploadBody(
  localPath: string,
  size: number,
  createReadStream: NodeFsModule['createReadStream'],
  options: UploadFileOptions,
  boundary: string,
): { readonly boundary: string; readonly body: ReadableStream<Uint8Array> } {
  const fileName = escapeMultipartFilename(fileNameFromPath(localPath))
  const prefixChunks = [
    TEXT_ENCODER.encode(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${fileName}"\r\nContent-Type: application/octet-stream\r\n\r\n`,
    ),
  ]
  const suffixChunks = [TEXT_ENCODER.encode(`\r\n--${boundary}--\r\n`)]
  const stream = createReadStream(localPath)
  const iterator = stream[Symbol.asyncIterator]()
  const lastReported = { value: -1 }
  let bytesSent = 0
  let prefixIndex = 0
  let suffixIndex = 0
  let readingFile = false
  let closed = false

  const close = (): void => {
    if (closed) return
    closed = true
    stream.destroy()
  }

  const fail = (controller: ReadableStreamDefaultController<Uint8Array>, error: unknown): void => {
    close()
    controller.error(normalizeTransferError(error, options.signal))
  }

  return {
    boundary,
    body: new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          assertNotCancelled(options.signal)
          if (prefixIndex < prefixChunks.length) {
            controller.enqueue(prefixChunks[prefixIndex])
            prefixIndex += 1
            if (prefixIndex >= prefixChunks.length) {
              readingFile = true
            }
            return
          }
          if (readingFile) {
            if (bytesSent === size) {
              readingFile = false
              close()
              reportProgress(options.onProgress, bytesSent, size, lastReported)
              return
            }
            const next = await iterator.next()
            if (!next.done) {
              const remaining = size - bytesSent
              const chunk = next.value.byteLength > remaining
                ? next.value.subarray(0, remaining)
                : next.value
              bytesSent += chunk.byteLength
              reportProgress(options.onProgress, bytesSent, size, lastReported)
              controller.enqueue(chunk)
              if (bytesSent === size) {
                readingFile = false
                close()
              }
              return
            }
            if (bytesSent !== size) {
              throw createDaytonaError('source file changed size during upload', undefined, undefined, 'SDK')
            }
            readingFile = false
            reportProgress(options.onProgress, bytesSent, size, lastReported)
          }
          if (suffixIndex < suffixChunks.length) {
            controller.enqueue(suffixChunks[suffixIndex])
            suffixIndex += 1
            return
          }
          close()
          controller.close()
        } catch (error) {
          fail(controller, error)
        }
      },
      cancel(reason) {
        close()
        if (reason instanceof Error) {
          stream.destroy(reason)
        }
      },
    }),
  }
}

export async function uploadFile(
  transport: HttpTransport,
  localPath: string,
  remotePath: string,
  options: UploadFileOptions = {},
): Promise<void> {
  assertNotCancelled(options.signal)
  const modules = await nodeTransfers('uploadFile')
  const stat = await modules.fsPromises.stat(localPath)
  const boundary = `daytona-${runtimeUniqueId('upload')}`
  let furthestReported = -1
  const bodyFactory = (): ReadableStream<Uint8Array> => {
    assertNotCancelled(options.signal)
    return uploadBody(localPath, stat.size, modules.fs.createReadStream, {
      ...options,
      onProgress: options.onProgress === undefined
        ? undefined
        : (bytes, total) => {
          if (bytes <= furthestReported) return
          furthestReported = bytes
          options.onProgress?.(bytes, total)
        },
    }, boundary).body
  }
  const init: FetchRequestInit & RequestInitWithDuplex = {
    method: 'POST',
    headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
    bodyFactory,
    signal: options.signal,
    duplex: 'half',
    transfer: true,
  }
  try {
    await transport.fetch(`/files/upload${encodeQuery({ path: remotePath, atomic: options.atomic === true ? true : undefined })}`, init)
  } catch (error) {
    throw normalizeTransferError(error, options.signal)
  }
}

export async function downloadFile(
  transport: HttpTransport,
  remotePath: string,
  localPath: string,
  options: DownloadFileOptions = {},
): Promise<void> {
  assertNotCancelled(options.signal)
  const modules = await nodeTransfers('downloadFile')
  const destinationMode = await existingDestinationMode(modules.fsPromises, localPath)
  let handle: NodeFileHandle | undefined
  // Only set once the exclusive create succeeded: cleanup must never unlink a
  // path this call did not create (a colliding pre-existing file stays put).
  let partPath: string | undefined
  let responseBody: ResponseBodyLike | null | undefined
  let responseBodyFinished = false
  try {
    const response = await transport.fetch(`/files/download${encodeQuery({ path: remotePath })}`, { signal: options.signal, transfer: true })
    if (response.body === null) {
      throw createDaytonaError('downloadFile() expected a response body', undefined, undefined, 'SDK')
    }
    responseBody = response.body
    const staging = await openExclusiveStagingFile(modules.fsPromises, localPath)
    handle = staging.handle
    partPath = staging.partPath
    const stagedPath = staging.partPath
    const total = declaredContentLength(response.headers)
    const lastReported = { value: -1 }
    let bytesWritten = 0
    const reader = response.body.getReader()
    try {
      for (;;) {
        assertNotCancelled(options.signal)
        const next = await reader.read()
        if (next.done) {
          break
        }
        let chunkOffset = 0
        while (chunkOffset < next.value.byteLength) {
          const chunk = next.value.subarray(chunkOffset)
          const written = bytesWrittenByCall(await handle.write(chunk), chunk.byteLength)
          if (!Number.isInteger(written) || written <= 0 || written > chunk.byteLength) {
            throw createDaytonaError('downloadFile() received an invalid write result from the local filesystem', undefined, undefined, 'SDK')
          }
          chunkOffset += written
          bytesWritten += written
          reportProgress(options.onProgress, bytesWritten, total, lastReported)
        }
      }
      responseBodyFinished = true
    } catch (error) {
      responseBodyFinished = true
      await reader.cancel(error).catch(() => undefined)
      throw error
    } finally {
      reader.releaseLock()
    }
    if (total !== undefined) {
      if (bytesWritten !== total) {
        throw createDaytonaError('downloadFile() ended before the declared Content-Length was received', undefined, undefined, 'SDK')
      }
      reportProgress(options.onProgress, bytesWritten, total, lastReported)
    }
    await handle.chmod(destinationMode ?? defaultCreatedFileMode(modules.fs.constants))
    await handle.close()
    handle = undefined
    assertNotCancelled(options.signal)
    await modules.fsPromises.rename(stagedPath, localPath)
    partPath = undefined
  } catch (error) {
    if (!responseBodyFinished) {
      await responseBody?.cancel().catch(() => undefined)
    }
    if (handle !== undefined) {
      await handle.close().catch(() => undefined)
    }
    if (partPath !== undefined) await removePartialFile(modules.fsPromises, partPath)
    throw normalizeTransferError(error, options.signal)
  }
}
