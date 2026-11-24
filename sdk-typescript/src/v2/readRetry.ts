/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ClientTimeoutError, ConnectionError, DaytonaError } from './errors/DaytonaError.js'
import { sleep } from './http.js'
import { MAX_TIMER_DELAY_MS } from './options.js'
import { retryAfterHeaderMs } from './retryAfterHint.js'

// Retries of a wait's failed observation reads. A wait (sandbox state, Docker
// status, image or snapshot readiness) reads the server until a target or
// terminal state, within one deadline. A read that fails transiently (no
// response, the client's own read timer, HTTP 429, a 5xx other than 501) is
// retried after a back-off of 200 ms doubling to 5 s, or the server's retry
// hint when longer, never past the wait's deadline. A spent deadline throws
// the wait's own timeout with the last failed read as its cause. Any other
// failure (a 4xx, a 501, `LIMIT_EXCEEDED`) is thrown at once. Only reads are
// retried, never a request that changes something. The Go and Python SDKs
// apply the same policy.

export const READ_RETRY_MIN_DELAY_MS = 200
export const READ_RETRY_MAX_DELAY_MS = 5_000

/** The server's retry hint in ms: the body's `retryAfterMs`, else its `retry_after_seconds`, else the `Retry-After` header; 0 without one. */
export function retryAfterMs(error: DaytonaError): number {
  if (error.retryAfterMs !== undefined) return error.retryAfterMs
  if (error.retryAfterSeconds !== undefined) return error.retryAfterSeconds * 1000
  return retryAfterHeaderMs(error) ?? 0
}

/** Whether a failed observation read may be retried within the wait. */
export function isRetryableReadError(error: unknown): error is DaytonaError {
  if (error instanceof ClientTimeoutError || error instanceof ConnectionError) return true
  if (!(error instanceof DaytonaError) || error.statusCode === undefined) return false
  if (error.statusCode === 429) return error.code !== 'LIMIT_EXCEEDED'
  return error.statusCode >= 500 && error.statusCode !== 501
}

/**
 * The back-off and the last failure of one wait's reads. A successful read
 * resets the back-off but keeps the last failure, which becomes the cause of
 * the wait's timeout.
 * @internal
 */
export class ReadRetry {
  lastError: DaytonaError | undefined
  private delayMs = 0
  private nextReadAt: number | undefined

  /** Record a failed read; `false` when it must be thrown instead. */
  note(error: unknown): boolean {
    if (!isRetryableReadError(error)) return false
    this.lastError = error
    this.delayMs = Math.min(Math.max(READ_RETRY_MIN_DELAY_MS, this.delayMs * 2), READ_RETRY_MAX_DELAY_MS)
    this.nextReadAt = Date.now() + Math.max(this.delayMs, retryAfterMs(error))
    return true
  }

  /** Keep a retryable failure as the timeout cause without scheduling a read (an events-hub probe, which the hub reschedules itself). */
  remember(error: unknown): void {
    if (isRetryableReadError(error)) this.lastError = error
  }

  succeeded(): void {
    this.delayMs = 0
    this.nextReadAt = undefined
  }

  /** Sleep until the next read is due, never past `deadline`. */
  async pause(deadline: number | undefined): Promise<void> {
    if (this.nextReadAt === undefined) return
    const pause = Math.min(this.nextReadAt - Date.now(), deadline === undefined ? Infinity : deadline - Date.now())
    if (pause > 0) await sleep(Math.min(pause, MAX_TIMER_DELAY_MS))
  }
}

/**
 * `error` with `cause` set, for an error class whose constructor takes no
 * cause (`DockerWaitTimeoutError`). Unchanged without a cause.
 * @internal
 */
export function withCause<T extends Error>(error: T, cause: unknown): T {
  if (cause !== undefined) Object.defineProperty(error, 'cause', { value: cause, writable: true, enumerable: true, configurable: true })
  return error
}
