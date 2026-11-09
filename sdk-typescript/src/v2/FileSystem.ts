/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as toolbox from './gen/toolbox-types.js'
import type {
  BulkReadError,
  BulkReadResult,
  BulkWriteFile,
  BulkWriteResult,
  FileInfo,
  FileMatch,
  FilePermissionsOptions,
  ListResult,
  ReplaceResult,
} from './types.js'
import { bulkReadHttpError, copyArrayBuffer, parseBulkReadResult, toUint8Array } from './filesystemWire.js'
import { downloadFile, type DownloadFileOptions, type TransferProgressCallback, uploadFile, type UploadFileOptions } from './filesystemTransfers.js'
import { encodeQuery, HttpTransport, isAbortError, maxContentLengthFor, sseMessages, strictUriEncode, transferRequest } from './http.js'
import { maxResponseBytesOption } from './bodyLimit.js'
import { assertKnownOptions, optionKeys } from './options.js'
import type { OptionKeys } from './options.js'
import { BadRequestError, DaytonaError, createDaytonaError } from './errors/DaytonaError.js'

export type Match = FileMatch
export type SearchFilesResponse = ListResult<string>
export type FilePermissionsParams = FilePermissionsOptions
export type { BulkReadError, BulkReadResult, BulkWriteFile, BulkWriteResult }
export type { DownloadFileOptions, TransferProgressCallback, UploadFileOptions }

export enum FilesystemEventType {
  CREATE = 'create',
  WRITE = 'write',
  REMOVE = 'remove',
  RENAME = 'rename',
  CHMOD = 'chmod',
}

export interface FilesystemEvent {
  type: FilesystemEventType
  name: string
  isDir: boolean
  timestamp: number
}

export interface WatchHandle {
  stop(): Promise<void>
  waitForExit(): Promise<Error | undefined>
  readonly exitError: Error | undefined
}

export interface WriteFileOptions {
  mode?: string
  atomic?: boolean
}

/**
 * Options of the in-memory file reads (`readBytes`, `readText`).
 *
 * `maxResponseBytes` replaces the client's cap on the response body for this
 * call (`undefined` or `null` keep the client's cap, `0` means no limit, and any other value (negative, fractional, boolean, string, above `Number.MAX_SAFE_INTEGER`) is a `BadRequestError`).
 * A body over it is refused with `PayloadTooLargeError` before it is read in
 * full. For large files, or content from a sandbox you don't trust, use
 * `downloadFile()` (Node.js), which streams to disk.
 */
export interface ReadFileOptions {
  readonly maxResponseBytes?: number | null
}

export interface WriteManyOptions {
  atomic?: boolean
}

/**
 * Options of `readMany`: `maxResponseBytes` caps the whole multipart body, as
 * in {@link ReadFileOptions}.
 */
export interface ReadManyOptions {
  readonly maxResponseBytes?: number | null
}

const READ_FILE_OPTION_KEYS = optionKeys<ReadFileOptions>()({ maxResponseBytes: true })
const READ_MANY_OPTION_KEYS = optionKeys<ReadManyOptions>()({ maxResponseBytes: true })

/** The validated per-call cap of an in-memory read, as axios config. */
function readLimit(typeName: string, options: ReadFileOptions | ReadManyOptions | undefined, keys: OptionKeys<ReadFileOptions | ReadManyOptions>): { readonly maxContentLength?: number } {
  assertKnownOptions(typeName, options, keys)
  return maxContentLengthFor(maxResponseBytesOption('maxResponseBytes', options?.maxResponseBytes))
}

export interface WatchDirOptions {
  recursive?: boolean
  onExit?: (err?: Error) => void
  onOverflow?: () => void
}

/** The `Headers`-shaped part of a fetch response we actually depend on. */
interface HeaderLookup {
  get(name: string): unknown
}

function hasHeaderLookup(value: unknown): value is HeaderLookup {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Partial<HeaderLookup>).get === 'function'
  )
}

function contentTypeHeader(headers: unknown): string | undefined {
  if (typeof headers !== 'object' || headers === null) {
    return undefined
  }
  if (hasHeaderLookup(headers)) {
    const value = headers.get('content-type')
    if (typeof value === 'string') {
      return value
    }
  }
  const lower = Reflect.get(headers, 'content-type')
  if (typeof lower === 'string') {
    return lower
  }
  const upper = Reflect.get(headers, 'Content-Type')
  return typeof upper === 'string' ? upper : undefined
}

function filesystemEventType(value: unknown): FilesystemEventType {
  switch (value) {
    case FilesystemEventType.CREATE:
    case FilesystemEventType.WRITE:
    case FilesystemEventType.REMOVE:
    case FilesystemEventType.RENAME:
    case FilesystemEventType.CHMOD:
      return value
    default:
      throw new DaytonaError(`invalid filesystem event type: ${String(value)}`, { source: 'SDK' })
  }
}

function parseFilesystemEvent(data: string): FilesystemEvent {
  let parsed: unknown
  try {
    parsed = data ? JSON.parse(data) : {}
  } catch (error) {
    throw new DaytonaError(error instanceof Error ? error.message : String(error), { source: 'SDK' })
  }
  if (typeof parsed !== 'object' || parsed === null) {
    throw new DaytonaError('filesystem watch event must be an object', { source: 'SDK' })
  }
  const raw = parsed as Record<string, unknown>
  if (typeof raw.name !== 'string') throw new DaytonaError('filesystem watch event name must be a string', { source: 'SDK' })
  if (typeof raw.isDir !== 'boolean') throw new DaytonaError('filesystem watch event isDir must be a boolean', { source: 'SDK' })
  if (typeof raw.timestamp !== 'number' || !Number.isFinite(raw.timestamp)) {
    throw new DaytonaError('filesystem watch event timestamp must be a finite number', { source: 'SDK' })
  }
  return {
    type: filesystemEventType(raw.type),
    name: raw.name,
    isDir: raw.isDir,
    timestamp: raw.timestamp,
  }
}


export interface PresignedUrlOptions {
  expiresInSeconds?: number
}

/**
 * Server ceiling on a pre-signed file URL's lifetime (7 days). The API rejects
 * an `expires` further ahead than this, so the SDK refuses it before the
 * request; never-expiring links do not exist.
 */
export const SIGNED_FILE_MAX_TTL_SECONDS = 604_800

/** The API's answer to `POST …/files/sign`: the fields the SDK composes a URL from. */
export interface SignedFileUrl {
  readonly signature: string
  readonly expiresAt: string
}

/**
 * `(method, path, expiresAt) => signed` against the API's
 * `POST …/files/sign`: `method` is `GET` for a download link and `POST` for an
 * upload link, `expiresAt` an RFC 3339 timestamp.
 */
export type FileUrlSigner = (method: 'GET' | 'POST', path: string, expiresAt: string) => Promise<SignedFileUrl>

function assertExpiresInSeconds(expiresInSeconds: number): void {
  if (!Number.isInteger(expiresInSeconds) || expiresInSeconds < 1 || expiresInSeconds > SIGNED_FILE_MAX_TTL_SECONDS) {
    throw new BadRequestError(
      `expiresInSeconds must be an integer between 1 and ${SIGNED_FILE_MAX_TTL_SECONDS} (7 days)`,
      { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' },
    )
  }
}

/** The documented default only for an OMITTED option: a JavaScript caller
 * passing `null` is rejected by `assertExpiresInSeconds` exactly like Python
 * rejects `None` (parity). */
function expiresInSecondsOrDefault(options: PresignedUrlOptions): number {
  return options.expiresInSeconds === undefined ? 3600 : options.expiresInSeconds
}

function expiresAt(expiresInSeconds: number): string {
  assertExpiresInSeconds(expiresInSeconds)
  return new Date((Math.floor(Date.now() / 1000) + expiresInSeconds) * 1000).toISOString()
}


export { strictUriEncode } from './http.js'

export class FileSystemService {
  constructor(
    private readonly transport: HttpTransport,
    private readonly fileUrlSigner?: FileUrlSigner,
  ) {}

  /**
   * Read a file into memory. A file transfer: no total deadline, but it fails
   * with `ClientTimeoutError` once no bytes move for the request timeout (a
   * request timeout of `0` leaves it unbounded). The
   * body is capped by the client's `maxResponseBytes` or this call's (see
   * {@link ReadFileOptions}).
   */
  public async readBytes(path: string, options?: ReadFileOptions): Promise<Uint8Array> {
    const limit = readLimit('ReadFileOptions', options, READ_FILE_OPTION_KEYS)
    // Through the transport's wrapped method, never the raw axios instance:
    // the wrapper carries the per-sandbox token (minting/refreshing it) and
    // retries once on a 401 — the raw instance only has the static
    // construction-time credential.
    const data = await this.transport.get<ArrayBuffer>('/files/download', transferRequest({
      params: { path },
      responseType: 'arraybuffer',
      ...limit,
    }))
    return new Uint8Array(data)
  }

  /** Read a UTF-8 text file into memory; the bounds of {@link readBytes}. */
  public async readText(path: string, options?: ReadFileOptions): Promise<string> {
    return new TextDecoder().decode(await this.readBytes(path, options))
  }

  public async writeBytes(path: string, data: Uint8Array, options: WriteFileOptions = {}): Promise<void> {
    // JavaScript callers migrating from the old write(path, string) get a named error, not a RangeError from TypedArray.set.
    if (typeof data === 'string') throw new BadRequestError('writeBytes() takes a Uint8Array; use writeText() for strings', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    const form = new FormData()
    form.append('path', path)
    if (options.mode) form.append('mode', options.mode)
    const ab = new ArrayBuffer(data.byteLength)
    new Uint8Array(ab).set(data)
    form.append('file', new Blob([ab]), path.split('/').pop() || 'file')
    await this.transport.post('/files/upload', form, transferRequest({ params: uploadQuery(path, options.atomic) }))
    if (options.mode) await this.chmod(path, { mode: options.mode })
  }

  public async writeText(path: string, text: string, options: WriteFileOptions = {}): Promise<void> {
    await this.writeBytes(path, new TextEncoder().encode(text), options)
  }

  /** Stream a local file to the sandbox without buffering the full body in memory. Progress callback errors are swallowed. Node.js only. */
  public async uploadFile(localPath: string, remotePath: string, options: UploadFileOptions = {}): Promise<void> {
    await uploadFile(this.transport, localPath, remotePath, options)
  }

  /** Stream a sandbox file to disk via an exclusive random temp file in the destination directory, renaming on success. Progress callback errors are swallowed. Node.js only. */
  public async downloadFile(remotePath: string, localPath: string, options: DownloadFileOptions = {}): Promise<void> {
    await downloadFile(this.transport, remotePath, localPath, options)
  }

  /**
   * Pre-signed download URL for a sandbox file. The signature is minted by the
   * API; `expiresInSeconds` must be between 1 and
   * {@link SIGNED_FILE_MAX_TTL_SECONDS} (7 days).
   */
  public async presignedDownloadUrl(path: string, options: PresignedUrlOptions = {}): Promise<string> {
    return this.presignedUrl('GET', '/files/download', path, expiresInSecondsOrDefault(options))
  }

  /**
   * Pre-signed upload URL for a sandbox file. The signature is minted by the
   * API; `expiresInSeconds` must be between 1 and
   * {@link SIGNED_FILE_MAX_TTL_SECONDS} (7 days).
   */
  public async presignedUploadUrl(path: string, options: PresignedUrlOptions = {}): Promise<string> {
    return this.presignedUrl('POST', '/files/upload', path, expiresInSecondsOrDefault(options))
  }

  public async writeMany(files: Record<string, Uint8Array>, options: WriteManyOptions = {}): Promise<BulkWriteResult> {
    const form = new FormData()
    Object.entries(files).forEach(([path, data], index) => {
      form.append(`files[${index}].path`, path)
      form.append(`files[${index}].file`, new Blob([copyArrayBuffer(data)]), path.split('/').pop() || 'file')
    })
    const atomicParams = uploadManyQuery(options.atomic)
    const data = await this.transport.post<BulkWriteResult>('/files/bulk-upload', form, transferRequest(atomicParams !== undefined ? { params: atomicParams } : {}))
    return {
      files: Array.isArray(data.files) ? data.files : [],
      errors: Array.isArray(data.errors) ? data.errors.filter((item): item is string => typeof item === 'string') : [],
    }
  }

  /**
   * Read several files into memory, in one multipart response. The bounds of
   * {@link readBytes}; `maxResponseBytes` caps the whole multipart body.
   */
  public async readMany(paths: readonly string[], options?: ReadManyOptions): Promise<BulkReadResult> {
    const limit = readLimit('ReadManyOptions', options, READ_MANY_OPTION_KEYS)
    // postRaw: the body carries per-path errors on non-2xx statuses, which
    // the parser interprets — but a 401 still throws into the transport's
    // one-retry mint path instead of being swallowed as a payload.
    const response = await this.transport.postRaw<ArrayBuffer | Uint8Array>('/files/bulk-download', { paths }, transferRequest({
      responseType: 'arraybuffer',
      ...limit,
    }))
    const body = toUint8Array(response.data)
    if (response.status !== 200) throw bulkReadHttpError(response.status, body)
    return parseBulkReadResult(body, contentTypeHeader(response.headers))
  }

  /** List a directory; response always has `{items,nextCursor}`. */
  public async list(path: string, options: { depth?: number } = {}): Promise<ListResult<FileInfo>> {
    const params: { path: string; depth?: number } = { path }
    if (options.depth !== undefined) params.depth = options.depth
    const response = await this.transport.get<FileInfo[] | ListResult<FileInfo>>('/files', {
      params,
    })
    return Array.isArray(response) ? { items: response } : response
  }
  public async stat(path: string): Promise<FileInfo> {
    return this.transport.get<toolbox.FileInfo>('/files/info', { params: { path } })
  }

  public async mkdir(path: string, options: { mode?: string } = {}): Promise<void> {
    await this.transport.post<void>('/files/folder', undefined, { params: { path, mode: options.mode ?? '0755' } })
  }

  public async remove(path: string, options: { recursive?: boolean } = {}): Promise<void> {
    await this.transport.delete<void>('/files', { params: { path, recursive: options.recursive } })
  }

  public async move(source: string, destination: string): Promise<void> {
    await this.transport.post<void>('/files/move', undefined, { params: { source, destination } })
  }

  /** Search file contents under `path` for the literal substring `pattern`; returns matches with one-based line numbers. */
  public async grep(path: string, pattern: string): Promise<ListResult<FileMatch>> {
    const data = await this.transport.get<toolbox.Match[] | ListResult<toolbox.Match>>('/files/find', { params: { path, pattern } })
    return Array.isArray(data) ? { items: data } : data
  }

  public async watchDir(
    path: string,
    onEvent: (event: FilesystemEvent) => void | Promise<void>,
    options: WatchDirOptions = {},
  ): Promise<WatchHandle> {
    const controller = new AbortController()
    let stopRequested = false
    let exitNotified = false
    let exitError: Error | undefined
    let started = false
    let resolveStarted!: () => void
    let rejectStarted!: (error: Error) => void
    const startedPromise = new Promise<void>((resolve, reject) => {
      resolveStarted = resolve
      rejectStarted = reject
    })
    let resolveExit!: (error: Error | undefined) => void
    const exitPromise = new Promise<Error | undefined>((resolve) => {
      resolveExit = resolve
    })
    let resolveStopRequested!: () => void
    const stopRequestedPromise = new Promise<void>((resolve) => {
      resolveStopRequested = resolve
    })
    const notifyExit = (error?: Error): void => {
      if (exitNotified) return
      exitNotified = true
      exitError = error
      resolveExit(error)
      options.onExit?.(error)
    }
    const streamTask = (async () => {
      try {
        const response = await this.transport.fetch(`/files/watch${encodeQuery({ path, recursive: options.recursive })}`, {
          headers: { Accept: 'text/event-stream' },
          signal: controller.signal,
        })
        let stoppedMidCallback = false
        for await (const message of sseMessages(response)) {
          switch (message.event) {
            case 'start':
              if (!started) {
                started = true
                resolveStarted()
              }
              break
            case 'fs': {
              // Race the callback against stop(): a re-entrant stop() from
              // inside the callback must not deadlock (the stream task would await
              // a callback that awaits the stream task), while an external stop()
              // still awaits full stream closure because stop() always awaits
              // the stream task below.
              const callbackResult = Promise.resolve().then(() => onEvent(parseFilesystemEvent(message.data)))
              const winner = await Promise.race([
                callbackResult.then(
                  () => 'done' as const,
                  (error: unknown) => ({ error }),
                ),
                stopRequestedPromise.then(() => 'stopped' as const),
              ])
              if (winner === 'stopped') {
                callbackResult.catch(() => undefined)
                stoppedMidCallback = true
                break
              }
              if (winner !== 'done') throw winner.error instanceof Error ? winner.error : new DaytonaError(String(winner.error), { source: 'SDK' })
              break
            }
            case 'overflow':
              options.onOverflow?.()
              break
            default:
              break
          }
          if (stoppedMidCallback) break
        }
        if (started) {
          if (!stopRequested) notifyExit(new DaytonaError('filesystem watch stream ended unexpectedly', { source: 'SDK' }))
          else notifyExit()
          return
        }
        const error = new DaytonaError('filesystem watch stream ended before start', { source: 'SDK' })
        rejectStarted(error)
        notifyExit(error)
      } catch (error) {
        if (isAbortError(error) && stopRequested) {
          if (!started) rejectStarted(new DaytonaError('filesystem watch stopped before start', { source: 'SDK' }))
          notifyExit()
          return
        }
        const normalized = error instanceof Error ? error : new DaytonaError(String(error))
        if (!started) rejectStarted(normalized)
        notifyExit(normalized)
      }
    })()
    const handle: WatchHandle = {
      get exitError() {
        return exitError
      },
      stop: async () => {
        stopRequested = true
        resolveStopRequested()
        controller.abort()
      await streamTask
      },
      waitForExit: async () => exitPromise,
    }
    await startedPromise
    return handle
  }

  /** Return the paths of files under `path` whose base name matches the glob `pattern`. */
  public async glob(path: string, pattern: string): Promise<ListResult<string>> {
    const data = await this.transport.get<toolbox.SearchResponse | string[] | ListResult<string>>('/files/search', { params: { path, pattern } })
    if (Array.isArray(data)) return { items: data }
    if ('items' in data) return data
    return { items: data.files ?? [] }
  }

  public async replace(files: string[], pattern: string, newValue: string): Promise<ListResult<ReplaceResult>> {
    const body: toolbox.ReplaceRequest = { files, pattern, newValue }
    const data = await this.transport.post<toolbox.ReplaceResult[] | ListResult<toolbox.ReplaceResult>>('/files/replace', body)
    return Array.isArray(data) ? { items: data } : data
  }

  public async chmod(path: string, permissions: FilePermissionsOptions): Promise<void> {
    await this.transport.post<void>('/files/permissions', undefined, {
      params: { path, mode: permissions.mode, owner: permissions.owner, group: permissions.group },
    })
  }

  private async presignedUrl(method: 'GET' | 'POST', route: '/files/download' | '/files/upload', path: string, expiresInSeconds: number): Promise<string> {
    // Validate before any network: the server would answer 400 for an
    // out-of-range `expires` anyway, but a bad argument is the caller's bug
    // and should not cost a round trip or depend on clock skew.
    const signed = await this.requireFileUrlSigner()(method, path, expiresAt(expiresInSeconds))
    const signedExpires = Math.floor(Date.parse(signed.expiresAt) / 1000)
    // The URL is composed from the handle's OWN toolbox base, not the server's
    // `url` field: a client configured with an explicit `toolboxUrl` keeps
    // receiving links on that base. The signature covers the server's
    // `expires`, so that value is echoed, not ours.
    return `${this.transport.baseUrl}${route}?path=${strictUriEncode(path)}&expires=${signedExpires}&signature=${signed.signature}`
  }

  private requireFileUrlSigner(): FileUrlSigner {
    if (this.fileUrlSigner === undefined) {
      throw createDaytonaError('filesystem handle has no pre-signed URL signer', 400, 'BAD_REQUEST', 'SDK')
    }
    return this.fileUrlSigner
  }
}

function uploadQuery(path: string, atomic: boolean | undefined): { readonly path: string; readonly atomic?: true } {
  return atomic === true ? { path, atomic: true } : { path }
}

function uploadManyQuery(atomic: boolean | undefined): { readonly atomic?: true } | undefined {
  return atomic === true ? { atomic: true } : undefined
}
