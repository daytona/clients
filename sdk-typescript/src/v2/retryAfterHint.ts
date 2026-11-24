/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The `Retry-After` header of an error response, kept beside the error: the
// public fields carry only what the body says. Read by the wait retries
// (readRetry.ts).
const HEADER_HINTS = new WeakMap<Error, number>()
const IMF_FIXDATE = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/

/** Keep the milliseconds a `Retry-After` header asks for (delta seconds or an HTTP date). */
export function rememberRetryAfter(error: Error, header: unknown): void {
  if (typeof header !== 'string') return
  const value = header.trim()
  if (value === '') return
  let ms: number
  if (/^\d+$/.test(value)) ms = Number(value) * 1000
  else {
    // Only an HTTP date (IMF-fixdate): Date.parse reads many other strings.
    if (!IMF_FIXDATE.test(value)) return
    const when = Date.parse(value)
    if (Number.isNaN(when)) return
    ms = Math.max(0, when - Date.now())
  }
  if (Number.isFinite(ms)) HEADER_HINTS.set(error, ms)
}

/** The milliseconds a `Retry-After` header on `error`'s response asked for, if any. */
export function retryAfterHeaderMs(error: Error): number | undefined {
  return HEADER_HINTS.get(error)
}
