/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ClientTimeoutError, ConnectionError, DaytonaError, RequestTimeoutError } from './errors/DaytonaError.js'
import { sleep } from './http.js'
import { MAX_TIMER_DELAY_MS } from './options.js'
import { retryAfterHeaderMs } from './retryAfterHint.js'

// Retries of a wait's failed observation reads. A wait (sandbox state, Docker
// status, image or snapshot readiness) reads the server until a target or
// terminal state, within one deadline. A read that fails transiently (no
// response, the client's own read timer, HTTP 429 other than LIMIT_EXCEEDED,
// a 5xx other than 501) is
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
  for (const hint of [bodyHint(error.retryAfterMs, 1), bodyHint(error.retryAfterSeconds, 1000)]) {
    if (hint !== undefined) return hint
  }
  return retryAfterHeaderMs(error) ?? 0
}

/** A body hint in ms; a negative or non-finite one is none. */
function bodyHint(value: number | undefined, unitMs: number): number | undefined {
  if (value === undefined || value < 0) return undefined
  const ms = value * unitMs
  return Number.isFinite(ms) ? ms : undefined
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

  /** `closed`: the owning client's closed signal, which ends every pause (the next read then throws `ClientClosedError`). */
  constructor(private readonly closed?: AbortSignal) {}

  /** Record a failed read; `false` when it must be thrown instead. */
  note(error: unknown): boolean {
    if (!isRetryableReadError(error)) return false
    this.lastError = error
    this.delayMs = Math.min(Math.max(READ_RETRY_MIN_DELAY_MS, this.delayMs * 2), READ_RETRY_MAX_DELAY_MS)
    this.nextReadAt = Date.now() + Math.max(this.delayMs, retryAfterMs(error))
    return true
  }

  /**
   * Record a failed events-hub probe. A transient failure schedules the next
   * read as `note` does; any other failure the hub keeps waiting through (its
   * probes stop only on 401, 403 and 404) is kept as the timeout's cause too.
   * The wait's own timeout is not a read failure.
   */
  probeFailed(error: unknown): void {
    if (this.note(error) || !(error instanceof DaytonaError)) return
    if (!(error instanceof RequestTimeoutError && error.source === 'SDK')) this.lastError = error
  }

  succeeded(): void {
    this.delayMs = 0
    this.nextReadAt = undefined
  }

  /**
   * Sleep until the next read is due (and not before `notBefore`, a wait's
   * own poll spacing), never past `deadline`. Sleeps in timer-sized steps
   * until the clock reads that time: a timer may fire a little early, and a
   * hint may be longer than one timer. `signal` (an events-hub probe whose
   * wait has settled) and the client's closed signal end the pause early and
   * clear its timer.
   */
  async pause(deadline: number | undefined, notBefore?: number, signal?: AbortSignal): Promise<void> {
    if (this.nextReadAt === undefined) return
    const due = Math.max(this.nextReadAt, notBefore ?? -Infinity)
    const until = deadline === undefined ? due : Math.min(due, deadline)
    const signals = [signal, this.closed].filter((each): each is AbortSignal => each !== undefined)
    for (let left = until - Date.now(); left > 0 && !signals.some((each) => each.aborted); left = until - Date.now()) {
      const step = Math.min(Math.ceil(left), MAX_TIMER_DELAY_MS)
      await (signals.length === 0 ? sleep(step) : abortableSleep(step, signals))
    }
  }
}

/** A sleep that any of `signals` ends early, clearing its timer. */
function abortableSleep(ms: number, signals: readonly AbortSignal[]): Promise<void> {
  return new Promise((resolve) => {
    const done = (): void => {
      clearTimeout(timer)
      for (const signal of signals) signal.removeEventListener('abort', done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    for (const signal of signals) signal.addEventListener('abort', done)
  })
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
