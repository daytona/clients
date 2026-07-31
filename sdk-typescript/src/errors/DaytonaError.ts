/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @module Errors
 */

import { AxiosHeaders } from 'axios'
import type { AxiosError } from 'axios'

export type ResponseHeaders = InstanceType<typeof AxiosHeaders>

/**
 * Wire-format `source` identifiers set by the translation layer when a
 * Daytona service stamps them on the wire envelope. `source = undefined`
 * means the response did not carry a structured envelope (treat as opaque).
 */
export const SOURCE_API = 'DAYTONA_API'
export const SOURCE_DAEMON = 'DAYTONA_DAEMON'
export const SOURCE_PROXY = 'DAYTONA_PROXY'

/**
 * Base error for Daytona SDK. `statusCode` and `code` are populated only
 * for errors translated from a server response. `source` is `undefined`
 * unless the caller (or the translation layer) sets it.
 */
export class DaytonaError extends Error {
  public statusCode?: number
  public code?: string
  public readonly source?: string
  public headers?: ResponseHeaders

  constructor(message: string, statusCode?: number, headers?: ResponseHeaders, code?: string, source?: string) {
    super(message)
    this.name = new.target.name
    this.statusCode = statusCode
    this.headers = headers
    this.code = code
    this.source = source
  }

  /**
   * @deprecated Use {@link DaytonaError.code} instead. Kept so existing
   * `err.errorCode` reads keep returning the machine-readable code.
   * @returns the machine-readable error code, or `undefined` when the
   * response did not carry one (same as {@link DaytonaError.code})
   */
  get errorCode(): string | undefined {
    return this.code
  }
}

// HTTP-status classes — one per status code Daytona services emit.
// Names follow HTTP terminology (BadRequest=400, Forbidden=403).

/** The request was malformed or invalid (HTTP 400). */
export class DaytonaBadRequestError extends DaytonaError {}
/** Authentication failed — missing or invalid credentials (HTTP 401). */
export class DaytonaAuthenticationError extends DaytonaError {}
/** The authenticated caller lacks permission for the operation (HTTP 403). */
export class DaytonaForbiddenError extends DaytonaError {}
/** The requested resource does not exist (HTTP 404). */
export class DaytonaNotFoundError extends DaytonaError {}
/**
 * The operation timed out (HTTP 408, or 504 when a gateway timed out).
 *
 * Also matches {@link DaytonaConnectionTimeoutError} via `instanceof`, even
 * though that class sits under {@link DaytonaConnectionError} in the prototype
 * chain. Transport timeouts were raised as `DaytonaTimeoutError` before
 * `DaytonaConnectionTimeoutError` existed, so this keeps pre-existing
 * `catch (err) { if (err instanceof DaytonaTimeoutError) ... }` blocks working
 * — the same compatibility the Python SDK gets from inheriting both classes.
 */
export class DaytonaTimeoutError extends DaytonaError {
  static [Symbol.hasInstance](value: unknown): boolean {
    // Only the base class widens; subclasses (e.g. ProcessExecutionTimeout)
    // must keep exact prototype-chain semantics.
    if (this === DaytonaTimeoutError && value instanceof DaytonaConnectionTimeoutError) {
      return true
    }
    return Function.prototype[Symbol.hasInstance].call(this, value)
  }
}
/** The request conflicts with the current state of the resource (HTTP 409). */
export class DaytonaConflictError extends DaytonaError {}
/** The resource existed but is permanently gone (HTTP 410). */
export class DaytonaGoneError extends DaytonaError {}
/** The request was well-formed but semantically invalid (HTTP 422). */
export class DaytonaUnprocessableEntityError extends DaytonaError {}
/** The caller exceeded a rate limit (HTTP 429). */
export class DaytonaRateLimitError extends DaytonaError {}
/** A Daytona service failed unexpectedly (HTTP 500). */
export class DaytonaInternalServerError extends DaytonaError {}
/** An upstream gateway returned an invalid response (HTTP 502). */
export class DaytonaBadGatewayError extends DaytonaError {}
/** The service is temporarily unable to handle the request (HTTP 503). */
export class DaytonaServiceUnavailableError extends DaytonaError {}

// ============================================================================
// Backward-compatibility subclasses. New functionality belongs elsewhere in
// this file, not here. These are what the 400/403 entries of
// STATUS_CODE_TO_ERROR throw so pre-rename `instanceof` checks keep matching.
// They must stay declared BEFORE that map (TDZ) and in this file (import
// cycle), and can be removed together with those two map entries in a future
// major release. The deprecated `errorCode` getter on DaytonaError belongs to
// the same removal set.
// ============================================================================

/**
 * Legacy umbrella for validation failures. Kept so existing
 * `catch (err) { if (err instanceof DaytonaValidationError) ... }` blocks keep
 * matching both server-returned HTTP 400s and locally rejected arguments.
 *
 * @deprecated Do not throw or catch this directly in new code. Branch on the
 * precise class instead:
 * - {@link DaytonaInvalidArgumentError} — the SDK rejected your arguments
 *   locally, before any request was sent.
 * - {@link DaytonaBadRequestError} — a Daytona service returned HTTP 400.
 * - {@link DaytonaUnprocessableEntityError} — a Daytona service returned
 *   HTTP 422 (well-formed but semantically invalid).
 */
export class DaytonaValidationError extends DaytonaBadRequestError {}

/**
 * @deprecated Use {@link DaytonaForbiddenError} instead.
 */
export class DaytonaAuthorizationError extends DaytonaForbiddenError {}

// Not part of the deprecated set above. It extends DaytonaValidationError only
// so pre-existing `instanceof DaytonaValidationError` / `DaytonaBadRequestError`
// catches keep matching local argument rejections; reparent it directly onto
// DaytonaError in the next major.

/**
 * The SDK rejected the caller's arguments locally, before any request was
 * sent. `statusCode`, `code` and `source` are always `undefined` — no Daytona
 * service was contacted, so there is no HTTP status to report.
 *
 * Distinct from {@link DaytonaBadRequestError} (a service returned HTTP 400)
 * and {@link DaytonaUnprocessableEntityError} (a service returned HTTP 422).
 * This one always means: fix the arguments at the call site.
 *
 * @example
 * ```ts
 * try {
 *   await sandbox.setAutoStopInterval(-1)
 * } catch (err) {
 *   if (err instanceof DaytonaInvalidArgumentError) {
 *     // never reached the API — the value itself is invalid
 *   }
 * }
 * ```
 */
export class DaytonaInvalidArgumentError extends DaytonaValidationError {}

/** Network connection failure (can't connect or mid-request drop). */
export class DaytonaConnectionError extends DaytonaError {}
/** Transport-layer timeout (connect / read). Subclass of DaytonaConnectionError. */
export class DaytonaConnectionTimeoutError extends DaytonaConnectionError {}

// Domain-specific subclasses. Each inherits from the HTTP-status class that
// matches its server-side status, so callers can catch either level.

// --- Git (daemon) ---
/** Git authentication against the remote failed (code `GIT_AUTH_FAILED`). */
export class DaytonaGitAuthFailedError extends DaytonaAuthenticationError {}
/** The git remote repository was not found (code `GIT_REPO_NOT_FOUND`). */
export class DaytonaGitRepoNotFoundError extends DaytonaNotFoundError {}
/** The git branch does not exist (code `GIT_BRANCH_NOT_FOUND`). */
export class DaytonaGitBranchNotFoundError extends DaytonaNotFoundError {}
/** The git branch already exists (code `GIT_BRANCH_EXISTS`). */
export class DaytonaGitBranchExistsError extends DaytonaConflictError {}
/** The git push was rejected by the remote (code `GIT_PUSH_REJECTED`). */
export class DaytonaGitPushRejectedError extends DaytonaConflictError {}
/** The operation requires a clean worktree (code `GIT_DIRTY_WORKTREE`). */
export class DaytonaGitDirtyWorktreeError extends DaytonaConflictError {}
/** A git merge produced conflicts (code `GIT_MERGE_CONFLICT`). */
export class DaytonaGitMergeConflictError extends DaytonaConflictError {}

// --- Filesystem (daemon) ---
/** The file does not exist in the sandbox (code `FILE_NOT_FOUND`). */
export class DaytonaFileNotFoundError extends DaytonaNotFoundError {}
/** Access to the sandbox file was denied (code `FILE_ACCESS_DENIED`). */
export class DaytonaFileAccessDeniedError extends DaytonaForbiddenError {}
/** The supplied file path was rejected by the daemon (code `INVALID_FILE_PATH`). */
export class DaytonaInvalidFilePathError extends DaytonaBadRequestError {}
/** The daemon could not read the sandbox file (code `FILE_READ_FAILED`). */
export class DaytonaFileReadFailedError extends DaytonaInternalServerError {}

// --- LSP (daemon) ---
/** The LSP server must be initialized first (code `LSP_SERVER_NOT_INITIALIZED`). */
export class DaytonaLspServerNotInitializedError extends DaytonaBadRequestError {}

// --- Process / session (daemon) ---
/** Command execution exceeded its timeout (code `PROCESS_EXECUTION_TIMEOUT`). */
export class DaytonaProcessExecutionTimeoutError extends DaytonaTimeoutError {}
/** The sandbox process does not exist (code `PROCESS_NOT_FOUND`). */
export class DaytonaProcessNotFoundError extends DaytonaNotFoundError {}
/** The session has already ended (code `SESSION_ENDED`). */
export class DaytonaSessionEndedError extends DaytonaGoneError {}
/** The session command already finished (code `COMMAND_ALREADY_COMPLETED`). */
export class DaytonaCommandAlreadyCompletedError extends DaytonaGoneError {}

// --- Computer-use (daemon) ---
/** The accessibility service is unavailable (code `A11Y_UNAVAILABLE`). */
export class DaytonaA11yUnavailableError extends DaytonaServiceUnavailableError {}
/** A screen recording is still active (code `RECORDING_STILL_ACTIVE`). */
export class DaytonaRecordingStillActiveError extends DaytonaConflictError {}
/** ffmpeg is not available for recording (code `RECORDING_FFMPEG_NOT_FOUND`). */
export class DaytonaRecordingFfmpegNotFoundError extends DaytonaServiceUnavailableError {}

/**
 * (source, code) → precise DaytonaError subclass. Lookup runs before the
 * HTTP status code fallback, so a domain code wins over the status default.
 *
 * Code strings are kept inline (not imported from the generated clients) so
 * tests that virtual-mock the API client modules don't break module init.
 *
 * There is currently NO automated drift check against the daemon's
 * `DaemonErrorCode` enum — keep this map in sync by hand when codes are added.
 */
const CODE_TO_ERROR_CLASS: Record<string, typeof DaytonaError> = {
  // Daemon
  'DAYTONA_DAEMON|GIT_AUTH_FAILED': DaytonaGitAuthFailedError,
  'DAYTONA_DAEMON|GIT_REPO_NOT_FOUND': DaytonaGitRepoNotFoundError,
  'DAYTONA_DAEMON|GIT_BRANCH_NOT_FOUND': DaytonaGitBranchNotFoundError,
  'DAYTONA_DAEMON|GIT_BRANCH_EXISTS': DaytonaGitBranchExistsError,
  'DAYTONA_DAEMON|GIT_PUSH_REJECTED': DaytonaGitPushRejectedError,
  'DAYTONA_DAEMON|GIT_DIRTY_WORKTREE': DaytonaGitDirtyWorktreeError,
  'DAYTONA_DAEMON|GIT_MERGE_CONFLICT': DaytonaGitMergeConflictError,
  'DAYTONA_DAEMON|FILE_NOT_FOUND': DaytonaFileNotFoundError,
  'DAYTONA_DAEMON|FILE_ACCESS_DENIED': DaytonaFileAccessDeniedError,
  'DAYTONA_DAEMON|INVALID_FILE_PATH': DaytonaInvalidFilePathError,
  'DAYTONA_DAEMON|FILE_READ_FAILED': DaytonaFileReadFailedError,
  'DAYTONA_DAEMON|LSP_SERVER_NOT_INITIALIZED': DaytonaLspServerNotInitializedError,
  'DAYTONA_DAEMON|PROCESS_EXECUTION_TIMEOUT': DaytonaProcessExecutionTimeoutError,
  'DAYTONA_DAEMON|PROCESS_NOT_FOUND': DaytonaProcessNotFoundError,
  'DAYTONA_DAEMON|SESSION_ENDED': DaytonaSessionEndedError,
  'DAYTONA_DAEMON|COMMAND_ALREADY_COMPLETED': DaytonaCommandAlreadyCompletedError,
  'DAYTONA_DAEMON|A11Y_UNAVAILABLE': DaytonaA11yUnavailableError,
  'DAYTONA_DAEMON|RECORDING_STILL_ACTIVE': DaytonaRecordingStillActiveError,
  'DAYTONA_DAEMON|RECORDING_FFMPEG_NOT_FOUND': DaytonaRecordingFfmpegNotFoundError,
}

function lookupErrorClass(source: string | undefined, code: string | undefined): typeof DaytonaError | undefined {
  if (!code || !source) return undefined
  return CODE_TO_ERROR_CLASS[`${source}|${code}`]
}

const STATUS_CODE_TO_ERROR: Record<number, typeof DaytonaError> = {
  400: DaytonaValidationError,
  401: DaytonaAuthenticationError,
  403: DaytonaAuthorizationError,
  404: DaytonaNotFoundError,
  408: DaytonaTimeoutError,
  409: DaytonaConflictError,
  410: DaytonaGoneError,
  422: DaytonaUnprocessableEntityError,
  429: DaytonaRateLimitError,
  500: DaytonaInternalServerError,
  502: DaytonaBadGatewayError,
  503: DaytonaServiceUnavailableError,
  504: DaytonaTimeoutError,
}

/** Maps an HTTP status code to the corresponding Daytona error class. */
export function errorClassFromStatusCode(statusCode?: number): typeof DaytonaError {
  if (statusCode === undefined) {
    return DaytonaError
  }

  return STATUS_CODE_TO_ERROR[statusCode] || DaytonaError
}

/**
 * Creates the appropriate Daytona error subclass from structured error metadata.
 *
 * Resolution order: (source, code) override -> HTTP status code -> DaytonaError.
 */
export function createDaytonaError(
  message: string,
  statusCode?: number,
  headers?: ResponseHeaders,
  code?: string,
  source?: string,
): DaytonaError {
  const ErrorClass = lookupErrorClass(source, code) || errorClassFromStatusCode(statusCode)
  return new ErrorClass(message, statusCode, headers, code, source)
}

function isAxiosTimeoutError(error: AxiosError): boolean {
  return error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT' || error.message.includes('timeout of')
}

function getAxiosResponseDataObject(error: AxiosError): Record<string, unknown> | undefined {
  if (!error.response?.data || typeof error.response.data !== 'object') {
    return undefined
  }

  return error.response.data as Record<string, unknown>
}

function extractAxiosErrorCode(responseData?: Record<string, unknown>): string | undefined {
  const code = responseData?.code ?? responseData?.error_code
  return typeof code === 'string' ? code : undefined
}

function extractAxiosErrorSource(responseData?: Record<string, unknown>): string | undefined {
  return typeof responseData?.source === 'string' ? responseData.source : undefined
}

function extractAxiosErrorMessage(error: AxiosError): string {
  if (isAxiosTimeoutError(error)) {
    const deadline =
      typeof error.config?.timeout === 'number' && error.config.timeout > 0 ? ` after ${error.config.timeout}ms` : ''
    return (
      `HTTP request timed out${deadline} waiting for a response. This is a client-side deadline` +
      ' (DaytonaConfig.requestTimeoutMs, or the per-call operation/execution timeout); any operation' +
      ' already started on the server may still be running.'
    )
  }

  const responseData = getAxiosResponseDataObject(error)
  const responseMessage: unknown =
    (typeof responseData?.message === 'string' ? responseData.message : undefined) ??
    (typeof responseData?.error === 'string' ? responseData.error : undefined) ??
    error.response?.data
  const message: unknown = responseMessage || error.message || String(error)

  if (typeof message === 'object') {
    try {
      return JSON.stringify(message)
    } catch {
      return String(message)
    }
  }

  return String(message)
}

/**
 * Creates the appropriate Daytona error subclass from an Axios error. Maps
 * client-side timeouts to DaytonaConnectionTimeoutError, networking failures
 * (no response received) to DaytonaConnectionError, and HTTP responses to
 * the most specific subclass via `createDaytonaError`.
 */
export function createAxiosDaytonaError(error: AxiosError): DaytonaError {
  const message = extractAxiosErrorMessage(error)
  const statusCode = error.response?.status
  const headers = error.response?.headers as ResponseHeaders | undefined
  const responseData = getAxiosResponseDataObject(error)
  const code = extractAxiosErrorCode(responseData)
  const source = extractAxiosErrorSource(responseData)

  if (isAxiosTimeoutError(error)) {
    return new DaytonaConnectionTimeoutError(message, statusCode, headers, code, source)
  }

  if (!error.response && (error.request || error.code)) {
    return new DaytonaConnectionError(message, statusCode, headers, code, source)
  }

  return createDaytonaError(message, statusCode, headers, code, source)
}
