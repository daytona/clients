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
  FileSearchResult,
  FilePermissionsOptions,
  ListResult,
  ReplaceResult,
} from './types.js'
import { bulkReadHttpError, copyArrayBuffer, parseBulkReadResult, toUint8Array } from './filesystemWire.js'
import { downloadFile, type DownloadFileOptions, type TransferProgressCallback, uploadFile, type UploadFileOptions } from './filesystemTransfers.js'
import { encodeQuery, HttpTransport, isAbortError, maxContentLengthFor, sseMessages, streamEventTooLarge, strictUriEncode, transferRequest } from './http.js'
import { maxResponseBytesOption } from './bodyLimit.js'
import { BadRequestError, ConnectionError, DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import { binaryBytes, pathSequence, sandboxPath } from './filesystemInputs.js'
import type { OptionKeys } from './options.js'
import { bool, bulkWriteResultFromWire, envelopeList, fileInfoFromWire, fileMatchFromWire, int, listItems, malformedResponse, record, replaceResultFromWire, searchSkipped, str, strList } from './toolboxModels.js'
import { deepFrozen } from './frozen.js'
import { nestedDeeperThan } from './jsonDepth.js'

export type { BulkReadError, BulkReadResult, BulkWriteFile, BulkWriteResult }
export type { DownloadFileOptions, TransferProgressCallback, UploadFileOptions }

/**
 * What changed in a watched directory. An open vocabulary: a kind a later
 * sandbox reports is passed through as its string.
 */
export type FilesystemEventType = 'create' | 'write' | 'remove' | 'rename' | 'chmod' | (string & {})

export interface FilesystemEvent {
  readonly type: FilesystemEventType
  readonly name: string
  readonly isDir: boolean
  /** When the change happened, in Unix milliseconds. */
  readonly timestampMs: number
}

/**
 * A running directory watch, returned by `fs.watchDir()`. Callbacks run one
 * at a time, in order, and are awaited. A failing `onEvent` or `onOverflow`
 * ends the watch: that error becomes `exitError` and is passed to `onExit`.
 */
export interface WatchHandle {
  /**
   * Stop the watch. Resolves once a callback already running, and `onExit`,
   * have finished; a callback (or `onExit`) may call `stop()` on its own
   * watch without waiting for itself. In runtimes without `AsyncLocalStorage`
   * (browsers), a callback or `onExit` that stops its own watch after an
   * `await` must not await that `stop()`.
   */
  stop(): Promise<void>
  /** Wait until the watch ends; resolves with `exitError`. */
  waitForExit(): Promise<Error | undefined>
  /** Why the watch ended: `undefined` while running or after `stop()`. */
  readonly exitError: Error | undefined
}

export interface WriteFileOptions {
  /** Write to a temporary file and rename it into place. */
  readonly atomic?: boolean
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
  /** Write each file to a temporary file and rename it into place. */
  readonly atomic?: boolean
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

export interface ListFilesOptions {
  /** How many directory levels to list; the sandbox default is 1. */
  readonly depth?: number
}

export interface MkdirOptions {
  /** Octal permission bits; default `'0755'`. */
  readonly mode?: string
}

export interface RemoveOptions {
  /** Remove a directory and everything in it. */
  readonly recursive?: boolean
}

export interface WatchDirOptions {
  readonly recursive?: boolean
  /**
   * Runs once when a watch `watchDir()` returned ends: no error after `stop()`,
   * else the error that ended it. Its own failure is ignored.
   */
  readonly onExit?: (error?: Error) => void | Promise<void>
  /** The sandbox dropped events. */
  readonly onOverflow?: () => void | Promise<void>
}

/**
 * The most fs / overflow events a watch holds before its stream's `start`
 * (delivered after it); one more is a malformed stream. Raising it is additive.
 */
const WATCH_MAX_EVENTS_BEFORE_START = 4096
/**
 * The most bytes of events a watch's stream may send before `start` (every
 * byte of each event, its framing included, counted as it arrives, so one
 * never-ending event is bounded too): 4096 events of 256 bytes, about twice a
 * typical event. One byte more is a malformed stream, like the count cap.
 */
const WATCH_MAX_BYTES_BEFORE_START = 1_048_576
const WRITE_FILE_OPTION_KEYS = optionKeys<WriteFileOptions>()({ atomic: true })
const WRITE_MANY_OPTION_KEYS = optionKeys<WriteManyOptions>()({ atomic: true })
const LIST_FILES_OPTION_KEYS = optionKeys<ListFilesOptions>()({ depth: true })
const MKDIR_OPTION_KEYS = optionKeys<MkdirOptions>()({ mode: true })
const REMOVE_OPTION_KEYS = optionKeys<RemoveOptions>()({ recursive: true })
const WATCH_DIR_OPTION_KEYS = optionKeys<WatchDirOptions>()({ recursive: true, onExit: true, onOverflow: true })
const FILE_PERMISSIONS_OPTION_KEYS = optionKeys<FilePermissionsOptions>()({ mode: true, owner: true, group: true })
const UPLOAD_FILE_OPTION_KEYS = optionKeys<UploadFileOptions>()({ onProgress: true, signal: true, atomic: true })
const DOWNLOAD_FILE_OPTION_KEYS = optionKeys<DownloadFileOptions>()({ onProgress: true, signal: true })

interface AsyncLocalStorageLike<T> {
  getStore(): T | undefined
  run<R>(store: T, callback: () => R): R
}

/**
 * Where the runtime has one (Node, Bun, Deno, workerd with Node
 * compatibility), an AsyncLocalStorage tells a `stop()` / `waitForExit()`
 * made by a watch's own running callback from an external one; looked up at
 * run time so bundlers do not pull Node modules into a browser build.
 */
function deliveryScope(): AsyncLocalStorageLike<object> | undefined {
  const processRef = (globalThis as { readonly process?: { readonly getBuiltinModule?: (id: string) => unknown } }).process
  const loaded = processRef?.getBuiltinModule?.('node:async_hooks') as
    | { readonly AsyncLocalStorage?: new () => AsyncLocalStorageLike<object> }
    | undefined
  return loaded?.AsyncLocalStorage === undefined ? undefined : new loaded.AsyncLocalStorage()
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

const WATCH_EVENT = 'filesystem watch event'

function parseFilesystemEvent(data: string): FilesystemEvent {
  let parsed: unknown
  try {
    parsed = data ? JSON.parse(data) : {}
  } catch {
    throw malformedResponse(`${WATCH_EVENT}: the data is not JSON`)
  }
  if (nestedDeeperThan(parsed)) throw malformedResponse(`${WATCH_EVENT}: the JSON is nested too deeply`)
  const raw = record(parsed, WATCH_EVENT)
  return deepFrozen({
    type: str(raw, 'type', WATCH_EVENT),
    name: str(raw, 'name', WATCH_EVENT),
    isDir: bool(raw, 'isDir', WATCH_EVENT),
    timestampMs: int(raw, 'timestamp', WATCH_EVENT),
  })
}

const OVERFLOW_EVENT = 'filesystem watch overflow event'

/**
 * An `overflow` event carries `{ "dropped": <boolean> }`; anything else is the
 * malformed-response error, whether or not a callback handles it (as the
 * Python SDK's `_check_overflow_event`).
 */
function checkOverflowEvent(data: string): void {
  let parsed: unknown
  try {
    parsed = data ? JSON.parse(data) : {}
  } catch {
    throw malformedResponse(`${OVERFLOW_EVENT}: the data is not JSON`)
  }
  if (nestedDeeperThan(parsed)) throw malformedResponse(`${OVERFLOW_EVENT}: the JSON is nested too deeply`)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw malformedResponse(`${OVERFLOW_EVENT}: the data is not a JSON object`)
  bool(record(parsed, OVERFLOW_EVENT), 'dropped', OVERFLOW_EVENT)
}

export interface PresignedUrlOptions {
  /** Whole seconds, 1 to {@link SIGNED_FILE_MAX_TTL_SECONDS}; default 3600. */
  readonly expiresInSeconds?: number
}

const PRESIGNED_URL_OPTION_KEYS = optionKeys<PresignedUrlOptions>()({ expiresInSeconds: true })

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

/** Sandbox filesystem namespace (`sandbox.fs`). All sandbox paths must be
 * absolute strings starting with `/`; relative paths throw `BadRequestError`.
 * Local upload/download paths are exempt.
 */
export class FileSystemService {
  private readonly transport: HttpTransport
  private readonly fileUrlSigner?: FileUrlSigner

  /** @internal */
  constructor(token: Internal, transport: HttpTransport, fileUrlSigner?: FileUrlSigner)
  /** Built by the SDK: get it from `sandbox.fs`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: HttpTransport, fileUrlSigner?: FileUrlSigner) {
    assertInternal(token, 'FileSystemService')
    this.transport = transport
    if (fileUrlSigner !== undefined) this.fileUrlSigner = fileUrlSigner
  }

  /**
   * Read a file as bytes, holding the whole file in memory. A file transfer:
   * no total deadline, but it fails with `ClientTimeoutError` once no bytes
   * move for the request timeout (a request timeout of `0` leaves it
   * unbounded). The body is capped by the client's `maxResponseBytes` or this
   * call's (see {@link ReadFileOptions}). For large files, or content from a
   * sandbox you do not trust, use `downloadFile()`, which streams to disk with
   * progress and cancellation; it is Node.js-only, so in a browser the
   * in-memory reads and `presignedDownloadUrl()` are the options.
   */
  public async readBytes(path: string, options?: ReadFileOptions): Promise<Uint8Array> {
    sandboxPath('path', path)
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

  /**
   * Read a UTF-8 text file into memory, with the bounds of {@link readBytes}.
   * Decoding is strict (invalid UTF-8 throws a `TypeError`) and a leading
   * byte-order mark is kept as `\uFEFF`.
   */
  public async readText(path: string, options?: ReadFileOptions): Promise<string> {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await this.readBytes(path, options))
  }

  /** Write a buffer or view's exact bytes to an absolute path, optionally atomically. */
  public async writeBytes(path: string, data: Uint8Array | ArrayBuffer | ArrayBufferView, options: WriteFileOptions = {}): Promise<void> {
    sandboxPath('path', path)
    assertKnownOptions('WriteFileOptions', options, WRITE_FILE_OPTION_KEYS)
    // JavaScript callers migrating from the old write(path, string) get a pointer to writeText().
    if (typeof data === 'string') throw badRequest('data must be an ArrayBuffer or an ArrayBuffer view; use writeText() for strings')
    const bytes = binaryBytes('data', data)
    const form = new FormData()
    form.append('path', path)
    const ab = copyArrayBuffer(bytes)
    form.append('file', new Blob([ab]), path.split('/').pop() || 'file')
    await this.transport.post('/files/upload', form, transferRequest({ params: uploadQuery(path, options.atomic) }))
  }

  /** Write a UTF-8 text file. */
  public async writeText(path: string, text: string, options: WriteFileOptions = {}): Promise<void> {
    if (typeof text !== 'string') throw badRequest('text must be a string')
    await this.writeBytes(path, new TextEncoder().encode(text), options)
  }

  /** Stream a local file to the sandbox without buffering the full body in memory. Progress callback errors are swallowed. Node.js only. */
  public async uploadFile(localPath: string, remotePath: string, options: UploadFileOptions = {}): Promise<void> {
    if (typeof localPath !== 'string') throw badRequest('localPath must be a path to a local file; use writeBytes() or writeText() for in-memory content')
    sandboxPath('remotePath', remotePath)
    assertKnownOptions('UploadFileOptions', options, UPLOAD_FILE_OPTION_KEYS)
    await uploadFile(this.transport, localPath, remotePath, options)
  }

  /** Stream a sandbox file to disk via an exclusive random temp file in the destination directory, renaming on success. Progress callback errors are swallowed. Node.js only. */
  public async downloadFile(remotePath: string, localPath: string, options: DownloadFileOptions = {}): Promise<void> {
    sandboxPath('remotePath', remotePath)
    assertKnownOptions('DownloadFileOptions', options, DOWNLOAD_FILE_OPTION_KEYS)
    await downloadFile(this.transport, remotePath, localPath, options)
  }

  /**
   * Pre-signed download URL for a sandbox file. The signature is minted by the
   * API; `expiresInSeconds` must be between 1 and
   * {@link SIGNED_FILE_MAX_TTL_SECONDS} (7 days).
   */
  public async presignedDownloadUrl(path: string, options: PresignedUrlOptions = {}): Promise<string> {
    assertKnownOptions('PresignedUrlOptions', options, PRESIGNED_URL_OPTION_KEYS)
    return this.presignedUrl('GET', '/files/download', path, expiresInSecondsOrDefault(options))
  }

  /**
   * Pre-signed upload URL for a sandbox file. The signature is minted by the
   * API; `expiresInSeconds` must be between 1 and
   * {@link SIGNED_FILE_MAX_TTL_SECONDS} (7 days).
   */
  public async presignedUploadUrl(path: string, options: PresignedUrlOptions = {}): Promise<string> {
    assertKnownOptions('PresignedUrlOptions', options, PRESIGNED_URL_OPTION_KEYS)
    return this.presignedUrl('POST', '/files/upload', path, expiresInSecondsOrDefault(options))
  }

  /** Write absolute paths mapped to buffers or views in one request. */
  public async writeMany(files: Readonly<Record<string, Uint8Array | ArrayBuffer | ArrayBufferView>>, options: WriteManyOptions = {}): Promise<BulkWriteResult> {
    assertKnownOptions('WriteManyOptions', options, WRITE_MANY_OPTION_KEYS)
    if (typeof files !== 'object' || files === null || (Object.getPrototypeOf(files) !== Object.prototype && Object.getPrototypeOf(files) !== null)) {
      throw badRequest('files must be a record of paths to buffers or views')
    }
    // Object.entries skips symbol keys: one would silently drop a file.
    if (Object.getOwnPropertySymbols(files).some((key) => Object.prototype.propertyIsEnumerable.call(files, key))) {
      throw badRequest('files must be a record of paths to buffers or views')
    }
    const form = new FormData()
    Object.entries(files).forEach(([path, data], index) => {
      sandboxPath('files key', path)
      form.append(`files[${index}].path`, path)
      form.append(`files[${index}].file`, new Blob([copyArrayBuffer(binaryBytes(`files[${path}]`, data))]), path.split('/').pop() || 'file')
    })
    const atomicParams = uploadManyQuery(options.atomic)
    const data = await this.transport.post<toolbox.BulkUploadResponse>('/files/bulk-upload', form, transferRequest(atomicParams !== undefined ? { params: atomicParams } : {}))
    return bulkWriteResultFromWire(data)
  }

  /**
   * Read several files in one multipart response; a missing file is an entry
   * in `errors`, not a failure. The whole response is held in memory, with the
   * bounds of {@link readBytes}; `maxResponseBytes` caps the whole multipart
   * body. For large files, or content from a sandbox you do not trust, use
   * `downloadFile()`, which streams to disk with progress and cancellation;
   * it is Node.js-only, so in a browser the in-memory reads and
   * `presignedDownloadUrl()` are the options.
   */
  public async readMany(paths: readonly string[], options?: ReadManyOptions): Promise<BulkReadResult> {
    const checkedPaths = pathSequence('paths', paths).map((path, index) => sandboxPath(`paths[${index}]`, path))
    const limit = readLimit('ReadManyOptions', options, READ_MANY_OPTION_KEYS)
    // postRaw: the body carries per-path errors on non-2xx statuses, which
    // the parser interprets — but a 401 still throws into the transport's
    // one-retry mint path instead of being swallowed as a payload.
    const response = await this.transport.postRaw<ArrayBuffer | Uint8Array>('/files/bulk-download', { paths: checkedPaths }, transferRequest({
      responseType: 'arraybuffer',
      ...limit,
    }))
    const body = toUint8Array(response.data)
    if (response.status !== 200) throw bulkReadHttpError(response.status, body, contentTypeHeader(response.headers))
    return parseBulkReadResult(body, contentTypeHeader(response.headers))
  }

  /** List a directory: one page of `{ items, nextCursor }`. */
  public async list(path: string, options: ListFilesOptions = {}): Promise<ListResult<FileInfo>> {
    sandboxPath('path', path)
    assertKnownOptions('ListFilesOptions', options, LIST_FILES_OPTION_KEYS)
    if (options.depth !== undefined && !Number.isInteger(options.depth)) throw badRequest('depth must be an integer')
    const params: { path: string; depth?: number } = { path }
    if (options.depth !== undefined) params.depth = options.depth
    const response = await this.transport.get<toolbox.FileInfo[] | ListResult<toolbox.FileInfo>>('/files', {
      params,
    })
    return listItems(response, 'items', 'file list response', fileInfoFromWire)
  }

  /** Return file metadata. */
  public async stat(path: string): Promise<FileInfo> {
    sandboxPath('path', path)
    return fileInfoFromWire(await this.transport.get<toolbox.FileInfo>('/files/info', { params: { path } }))
  }

  public async mkdir(path: string, options: MkdirOptions = {}): Promise<void> {
    sandboxPath('path', path)
    assertKnownOptions('MkdirOptions', options, MKDIR_OPTION_KEYS)
    await this.transport.post<void>('/files/folder', undefined, { params: { path, mode: options.mode ?? '0755' } })
  }

  public async remove(path: string, options: RemoveOptions = {}): Promise<void> {
    sandboxPath('path', path)
    assertKnownOptions('RemoveOptions', options, REMOVE_OPTION_KEYS)
    await this.transport.delete<void>('/files', { params: { path, recursive: options.recursive } })
  }

  public async move(source: string, destination: string): Promise<void> {
    sandboxPath('source', source)
    sandboxPath('destination', destination)
    await this.transport.post<void>('/files/move', undefined, { params: { source, destination } })
  }

  /**
   * Search file contents under `path` for the literal substring `pattern`;
   * returns matches with one-based line numbers, as the sandbox user sees
   * them. Directories and files below `path` that the sandbox user cannot read
   * are not searched; `skipped` lists their absolute paths (at most 1000). Only
   * an unreadable `path` itself throws `FileAccessDeniedError`.
   */
  public async grep(path: string, pattern: string): Promise<FileSearchResult<FileMatch>> {
    sandboxPath('path', path)
    // Daemons that predate `skipped` answer a bare array of matches.
    const data = await this.transport.get<toolbox.Match[] | { items: toolbox.Match[]; skipped?: string[] }>('/files/find', { params: { path, pattern } })
    if (Array.isArray(data)) return deepFrozen({ items: data.map((item) => fileMatchFromWire(item)), skipped: [] })
    const response = record(data, 'grep response')
    return deepFrozen({ items: envelopeList(response, 'items', 'grep response', fileMatchFromWire), skipped: searchSkipped(response, 'grep response') })
  }

  /**
   * Watch `path` for changes, calling `onEvent` for each one. Resolves once
   * the watch is established. Callbacks run one at a time, in order, and are
   * awaited; a failing `onEvent` or `onOverflow` ends the watch with that
   * error. `onExit` runs once when the watch ends; its own failure is ignored.
   * A failure before the watch starts rejects this call and does not call
   * `onExit`. Callbacks are held until this call has returned its handle, so
   * after a direct `await watchDir(...)` a callback can call `stop()` on it;
   * when the promise is not awaited directly (inside `Promise.all`, or awaited
   * later), signal the code that holds the handle and call `stop()` there. A
   * stream that sends more than 4096 events, or more than 1 MiB, before
   * `start` rejects this call with `BadRequestError`. After `start` an event
   * larger than the client's `maxResponseBytes` ends the watch with
   * `PayloadTooLargeError`.
   */
  public async watchDir(
    path: string,
    onEvent: (event: FilesystemEvent) => void | Promise<void>,
    options: WatchDirOptions = {},
  ): Promise<WatchHandle> {
    sandboxPath('path', path)
    assertKnownOptions('WatchDirOptions', options, WATCH_DIR_OPTION_KEYS)
    if (options.recursive !== undefined && typeof options.recursive !== 'boolean') throw badRequest('recursive must be a boolean')
    if (typeof onEvent !== 'function') throw badRequest('onEvent must be callable')
    const controller = new AbortController()
    const scope = deliveryScope()
    let stopRequested = false
    let exitNotified = false
    let exitError: Error | undefined
    let started = false
    // The token of the callback delivery in progress: set for exactly that
    // delivery and cleared when it settles, so a call the callback schedules
    // for later (a timer) no longer counts as its own.
    let activeDelivery: object | undefined
    // True while a callback runs its synchronous part: a call made then is the
    // callback's own, even where no AsyncLocalStorage can tell.
    let deliveringSync = false
    let resolveStarted!: () => void
    let rejectStarted!: (error: Error) => void
    const startedPromise = new Promise<void>((resolve, reject) => {
      resolveStarted = resolve
      rejectStarted = reject
    })
    // Released once watchDir() has returned the handle (or the watch is
    // stopped): no callback, onExit included, runs before the caller holds it.
    let releaseDeliveries!: () => void
    const published = new Promise<void>((resolve) => {
      releaseDeliveries = resolve
    })
    // Events read before `start` (none from a well-behaved toolbox), delivered
    // after it: the stream must keep being read until `start` arrives, but at
    // most WATCH_MAX_EVENTS_BEFORE_START of them are held.
    const held: (() => void | Promise<void>)[] = []
    let earlyEvents = 0
    // The stream's bytes are counted as they arrive: before start the whole
    // stream (every frame, data-less and comment-only ones too) may not pass
    // WATCH_MAX_BYTES_BEFORE_START, after it each event is bounded by the
    // client's maxResponseBytes.
    const clientLimit = this.transport.maxResponseBytes
    const remainingBeforeStart = (completed: number): number => WATCH_MAX_BYTES_BEFORE_START - completed
    const eventBound = {
      limit: (completed: number): number | undefined => {
        if (started) return clientLimit > 0 ? clientLimit : undefined
        return clientLimit > 0 ? Math.min(remainingBeforeStart(completed), clientLimit) : remainingBeforeStart(completed)
      },
      tooLarge: (completed: number): Error => started || (clientLimit > 0 && clientLimit < remainingBeforeStart(completed))
        ? streamEventTooLarge('filesystem watch stream', clientLimit)
        : malformedResponse(`filesystem watch stream: it sent more than ${WATCH_MAX_BYTES_BEFORE_START} bytes before start`),
    }
    let resolveExit!: (error: Error | undefined) => void
    const exitPromise = new Promise<Error | undefined>((resolve) => {
      resolveExit = resolve
    })
    const calledFromOwnCallback = (): boolean =>
      deliveringSync || (activeDelivery !== undefined && scope !== undefined && scope.getStore() === activeDelivery)
    const notifyExit = async (error?: Error): Promise<void> => {
      if (exitNotified) return
      exitNotified = true
      exitError = error
      // Resolved before onExit runs, so onExit can await waitForExit().
      resolveExit(error)
      // The exit callback belongs to a watch watchDir() returned: a failure
      // before start is thrown from watchDir() instead (as in Python).
      const onExit = started ? options.onExit : undefined
      if (onExit === undefined) return
      await published
      // onExit runs as a delivery, so its own stop() / waitForExit() calls do
      // not wait for it; its failure is contained: the watch has already ended.
      try {
        await deliver(() => onExit(error))
      } catch {
        // ignored
      }
    }
    const deliver = async (callback: () => void | Promise<void>): Promise<void> => {
      const token = {}
      activeDelivery = token
      let result: void | Promise<void>
      deliveringSync = true
      try {
        result = scope === undefined ? callback() : scope.run(token, callback)
      } finally {
        deliveringSync = false
      }
      try {
        await result
      } finally {
        activeDelivery = undefined
      }
    }
    // Runs the held events and then `callback` once the handle is published;
    // a watch stopped meanwhile delivers nothing more.
    const deliverEvents = async (callback?: () => void | Promise<void>): Promise<void> => {
      await published
      const pending = callback === undefined ? held.splice(0) : [...held.splice(0), callback]
      for (const next of pending) {
        if (stopRequested) return
        await deliver(next)
      }
    }
    const streamTask = (async () => {
      try {
        const response = await this.transport.fetch(`/files/watch${encodeQuery({ path, recursive: options.recursive })}`, {
          headers: { Accept: 'text/event-stream' },
          signal: controller.signal,
        })
        for await (const message of sseMessages(response, eventBound)) {
          if (stopRequested) break
          try {
            if (message.event === 'start') {
              if (!started) {
                started = true
                resolveStarted()
                if (held.length > 0) await deliverEvents()
              }
              continue
            }
            if (!started && (message.event === 'fs' || message.event === 'overflow')) {
              // Every listed event before start counts toward the cap, whether
              // or not a callback handles it.
              earlyEvents++
              if (earlyEvents > WATCH_MAX_EVENTS_BEFORE_START) {
                throw malformedResponse(`filesystem watch stream: it sent more than ${WATCH_MAX_EVENTS_BEFORE_START} events before start`)
              }
            }
            let callback: (() => void | Promise<void>) | undefined
            if (message.event === 'fs') {
              const event = parseFilesystemEvent(message.data)
              callback = () => onEvent(event)
            } else if (message.event === 'overflow') {
              checkOverflowEvent(message.data)
              const onOverflow = options.onOverflow
              if (onOverflow !== undefined) callback = () => onOverflow()
            }
            if (callback === undefined) continue
            if (started) await deliverEvents(callback)
            else held.push(callback)
          } catch (error) {
            // A failing callback (or a malformed event) ends the watch with
            // its error, even when stop() was called while it ran.
            controller.abort()
            const normalized = error instanceof Error ? error : createDaytonaError(String(error), undefined, undefined, 'SDK')
            if (!started) rejectStarted(normalized)
            await notifyExit(normalized)
            return
          }
        }
        // A stream that ends on its own is a dropped connection, as a
        // truncated body is: ConnectionError, as in the Python SDK.
        if (started) {
          await notifyExit(stopRequested ? undefined : new ConnectionError('filesystem watch stream ended unexpectedly'))
          return
        }
        const error = stopRequested
          ? createDaytonaError('filesystem watch stopped before start', undefined, undefined, 'SDK')
          : new ConnectionError('filesystem watch stream ended before start')
        rejectStarted(error)
        await notifyExit(stopRequested ? undefined : error)
      } catch (error) {
        if (isAbortError(error) && stopRequested) {
          if (!started) rejectStarted(createDaytonaError('filesystem watch stopped before start', undefined, undefined, 'SDK'))
          await notifyExit()
          return
        }
        // The SDK's own errors pass through; anything else from opening or
        // reading the stream (undici's "terminated" on a truncated body) is a
        // dropped connection, as the Python transport reports it.
        const normalized = error instanceof DaytonaError
          ? error
          : new ConnectionError(error instanceof Error ? error.message : String(error))
        if (!started) rejectStarted(normalized)
        await notifyExit(normalized)
      }
    })()
    const handle: WatchHandle = {
      get exitError() {
        return exitError
      },
      stop: async () => {
        const ownCallback = calledFromOwnCallback()
        stopRequested = true
        releaseDeliveries()
        controller.abort()
        // The watch's own callback (or onExit) must not wait for itself; any
        // other stop() waits for the stream task, onExit included, in every
        // runtime. Without AsyncLocalStorage only a callback's (or onExit's)
        // synchronous part is recognised as its own.
        if (ownCallback) return
        await streamTask
      },
      waitForExit: async () => (calledFromOwnCallback() ? exitError : exitPromise),
    }
    await startedPromise
    // Released one promise job from now: returning queues the caller's
    // `await watchDir()` continuation first, so the caller has stored the
    // handle before any callback runs. A promise job, not a timer, so fake
    // timers in the caller's tests cannot hold the watch back.
    void Promise.resolve().then(releaseDeliveries)
    return handle
  }

  /**
   * Return the paths of files under `path` whose base name matches the glob
   * `pattern`. Directories below `path` that the sandbox user cannot read are
   * not searched; `skipped` lists their absolute paths (at most 1000). Only an
   * unreadable `path` itself throws `FileAccessDeniedError`.
   */
  public async glob(path: string, pattern: string): Promise<FileSearchResult<string>> {
    sandboxPath('path', path)
    const data = record(await this.transport.get<toolbox.SearchResponse & { skipped?: string[] }>('/files/search', { params: { path, pattern } }), 'glob response')
    return deepFrozen({ items: strList(data, 'files', 'glob response'), skipped: searchSkipped(data, 'glob response') })
  }

  /** Replace the literal substring `pattern` with `newValue` in `files`. */
  public async replace(files: readonly string[], pattern: string, newValue: string): Promise<ListResult<ReplaceResult>> {
    const checkedFiles = pathSequence('files', files).map((path, index) => sandboxPath(`files[${index}]`, path))
    const body: toolbox.ReplaceRequest = { files: checkedFiles, pattern, newValue }
    const data = await this.transport.post<toolbox.ReplaceResult[] | ListResult<toolbox.ReplaceResult>>('/files/replace', body)
    return listItems(data, 'items', 'replace response', replaceResultFromWire)
  }

  /** Set file permissions and ownership. */
  public async chmod(path: string, permissions: FilePermissionsOptions): Promise<void> {
    sandboxPath('path', path)
    assertKnownOptions('FilePermissionsOptions', permissions, FILE_PERMISSIONS_OPTION_KEYS)
    await this.transport.post<void>('/files/permissions', undefined, {
      params: { path, mode: permissions.mode, owner: permissions.owner, group: permissions.group },
    })
  }

  private async presignedUrl(method: 'GET' | 'POST', route: '/files/download' | '/files/upload', path: string, expiresInSeconds: number): Promise<string> {
    sandboxPath('path', path)
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
      throw new BadRequestError('filesystem handle has no pre-signed URL signer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
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
