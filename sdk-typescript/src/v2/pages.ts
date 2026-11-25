/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ConnectionError, DaytonaError, LimitExceededError } from './errors/DaytonaError.js'
import type { ListResult } from './types.js'

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

function retryHintMs(error: DaytonaError): number | undefined {
  if (error.retryAfterMs !== undefined) return error.retryAfterMs
  if (error.retryAfterSeconds !== undefined) return error.retryAfterSeconds * 1000
  return undefined
}

/**
 * The pause before read `retry + 2` of a page after `error`, or `undefined`
 * when the error is not retried: only a dropped connection and HTTP 429 /
 * 502 / 503 / 504 are (not an organization limit, and not the client's own
 * request timeout, which already bounds each read).
 * @internal
 */
export function pageRetryDelayMs(error: unknown, retry: number): number | undefined {
  if (retry + 1 >= PAGE_READ_ATTEMPTS) return undefined
  if (!(error instanceof DaytonaError) || error instanceof LimitExceededError) return undefined
  if (!(error instanceof ConnectionError) && (error.statusCode === undefined || !RETRYABLE_STATUSES.has(error.statusCode))) {
    return undefined
  }
  let delay = (PAGE_READ_DELAYS_MS[retry] ?? 0) * (0.8 + Math.random() * 0.2)
  const hint = retryHintMs(error)
  if (hint !== undefined) {
    if (hint > PAGE_READ_MAX_DELAY_MS) return undefined
    delay = Math.max(delay, hint)
  }
  return delay
}

/**
 * One page, read again after a transient failure (see `pageRetryDelayMs`);
 * the `DaytonaError` that ends it carries `pageCursor: cursor`, the cursor to
 * resume the walk with.
 * @internal
 */
export async function readPage<T>(fetchPage: (cursor: string | undefined) => Promise<ListResult<T>>, cursor: string | undefined): Promise<ListResult<T>> {
  for (let retry = 0; ; retry += 1) {
    try {
      return await fetchPage(cursor)
    } catch (error) {
      const delay = pageRetryDelayMs(error, retry)
      if (delay === undefined) {
        if (error instanceof DaytonaError) {
          Object.defineProperty(error, 'pageCursor', { value: cursor, enumerable: true, configurable: true, writable: false })
        }
        throw error
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
