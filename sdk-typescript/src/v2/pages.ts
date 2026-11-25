/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ConnectionError, DaytonaError, LimitExceededError } from './errors/DaytonaError.js'
import { retryAfterHeaderMs } from './retryAfterHint.js'
import type { ListResult } from './types.js'

// The page walk behind every `iter()`: bounded retries of a page read and the
// failed page's cursor on the error, the same policy as the Python and Go
// SDKs. Unlike a wait's reads, a walk has no overall deadline, so it retries
// only responses that say "try again" (no response, HTTP 429 other than
// LIMIT_EXCEEDED, 502, 503, 504), at most three reads of a page: a 500 is not
// a transient gateway status, and retrying the client's own request timeout
// would multiply the bound the caller configured for each read.

/** Every read of one page, the first included. @internal */
export const PAGE_READ_ATTEMPTS = 3
/** The pause before the second and the third read (each 80-100 % of it). @internal */
export const PAGE_READ_DELAYS_MS: readonly number[] = [500, 1000]
/** A server retry hint longer than this is not waited for: the error surfaces. @internal */
export const PAGE_READ_MAX_DELAY_MS = 10_000
const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([429, 502, 503, 504])

/** The page-retry pause; a seam so tests never fake the global timers. @internal */
export const pageRetryTimer = {
  sleep: (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms)),
}

/** The server's retry hint in ms: the body's `retryAfterMs`, else its `retryAfterSeconds`, else the `Retry-After` header; 0 without one. */
function retryHintMs(error: DaytonaError): number {
  for (const hint of [bodyHintMs(error.retryAfterMs, 1), bodyHintMs(error.retryAfterSeconds, 1000)]) {
    if (hint !== undefined) return hint
  }
  return retryAfterHeaderMs(error) ?? 0
}

/** A body hint in ms; a negative or non-finite one is none. */
function bodyHintMs(value: number | undefined, unitMs: number): number | undefined {
  if (value === undefined || value < 0) return undefined
  const ms = value * unitMs
  return Number.isFinite(ms) ? ms : undefined
}

/**
 * The pause before read `retry + 2` of a page after `error`, or `undefined`
 * when the error is not retried: only a dropped connection and HTTP 429 /
 * 502 / 503 / 504 are (not an organization limit, and not the client's own
 * request timeout, which already bounds each read). A server retry hint (body
 * or `Retry-After` header) above `PAGE_READ_MAX_DELAY_MS` ends the walk
 * without a retry.
 * @internal
 */
export function pageRetryDelayMs(error: unknown, retry: number): number | undefined {
  if (retry + 1 >= PAGE_READ_ATTEMPTS) return undefined
  if (!(error instanceof DaytonaError) || error instanceof LimitExceededError) return undefined
  if (!(error instanceof ConnectionError) && (error.statusCode === undefined || !RETRYABLE_STATUSES.has(error.statusCode))) {
    return undefined
  }
  const hint = retryHintMs(error)
  if (hint > PAGE_READ_MAX_DELAY_MS) return undefined
  return Math.max((PAGE_READ_DELAYS_MS[retry] ?? 0) * (0.8 + Math.random() * 0.2), hint)
}

/**
 * A copy of `error` (same class, same fields) carrying `pageCursor`. The
 * caught error itself is never changed: concurrent walks can receive one
 * shared instance (a failed token exchange is shared by its waiters).
 */
function withPageCursor(error: DaytonaError, cursor: string | undefined): DaytonaError {
  // SAFETY: the copy has error's prototype and every own property of it.
  const copy = Object.create(Object.getPrototypeOf(error) as object) as DaytonaError
  Object.defineProperties(copy, Object.getOwnPropertyDescriptors(error))
  // `stack` is an accessor reading the original's internal trace (V8) or a
  // prototype property (other runtimes): keep the original trace as a value.
  Object.defineProperty(copy, 'stack', { value: error.stack, enumerable: false, configurable: true, writable: true })
  Object.defineProperty(copy, 'pageCursor', { value: cursor, enumerable: true, configurable: true, writable: false })
  return copy
}

/**
 * One page, read again after a transient failure (see `pageRetryDelayMs`);
 * the `DaytonaError` that ends it is a copy carrying `pageCursor: cursor`, the
 * cursor to resume the walk with.
 * @internal
 */
export async function readPage<T>(fetchPage: (cursor: string | undefined) => Promise<ListResult<T>>, cursor: string | undefined): Promise<ListResult<T>> {
  for (let retry = 0; ; retry += 1) {
    try {
      return await fetchPage(cursor)
    } catch (error) {
      const delay = pageRetryDelayMs(error, retry)
      if (delay === undefined) {
        throw error instanceof DaytonaError ? withPageCursor(error, cursor) : error
      }
      await pageRetryTimer.sleep(delay)
    }
  }
}

/**
 * Every item of every page from `cursor` on, guarding against a cursor
 * cycle. An empty or repeated next cursor ends the walk.
 * @internal
 */
export async function* iterPages<T>(fetchPage: (cursor: string | undefined) => Promise<ListResult<T>>, cursor: string | undefined): AsyncIterable<T> {
  const seen = new Set<string>()
  if (cursor !== undefined) seen.add(cursor)
  for (;;) {
    const page = await readPage(fetchPage, cursor)
    yield* page.items
    if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
    seen.add(page.nextCursor)
    cursor = page.nextCursor
  }
}
