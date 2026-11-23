/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError, DaytonaRateLimitError } from '../errors/DaytonaError'

// State events drive waits; polling is only a safety net, so keep it sparse while events flow.
export const EVENT_BACKED_POLL_INTERVAL_MS = 5000
// Subscribed but no events arrive (connection down, or the subscription rejected by the server, including
// past its per-socket limit): start at 1 s and back off, so thousands of such waits do not exhaust the request budget.
export const FALLBACK_POLL_INTERVAL_MS = 1000
export const FALLBACK_POLL_GROWTH = 1.5
export const FALLBACK_POLL_MAX_INTERVAL_MS = 10_000
// Spreads the polls of concurrent waits apart.
export const POLL_JITTER = 0.2
// The longest Retry-After a rate-limited poll honours; also keeps the delay within setTimeout's range.
export const MAX_RETRY_AFTER_MS = 60_000

/** @internal The poll cadence of one wait; it keeps the fallback backoff between polls. */
export class WaitPollCadence {
  private fallbackIntervalMs = FALLBACK_POLL_INTERVAL_MS

  constructor(private readonly random: () => number = () => Math.random()) {}

  /**
   * Delay before the next poll. Without a subscription the fast cadence applies unchanged. A stretch
   * of being event-backed restarts the fallback backoff at its first interval.
   */
  next(subscribed: boolean, eventBacked: boolean, fastIntervalMs: number): number {
    if (!subscribed) return fastIntervalMs
    if (eventBacked) {
      this.fallbackIntervalMs = FALLBACK_POLL_INTERVAL_MS
      return this.jitter(EVENT_BACKED_POLL_INTERVAL_MS)
    }
    const intervalMs = this.fallbackIntervalMs
    this.advance()
    return this.jitter(intervalMs)
  }

  /**
   * Delay after a rate-limited poll, in any mode: the longest of the mode's interval, the fallback
   * backoff and Retry-After, then one backoff step, so repeated 429s slow every wait down. When
   * Retry-After decides, it gets upward jitter only, so waits told the same never retry together nor early.
   */
  rateLimited(subscribed: boolean, eventBacked: boolean, fastIntervalMs: number, retryAfterMs?: number): number {
    const factor = 1 + POLL_JITTER * (2 * this.random() - 1)
    const modeMs = !subscribed ? fastIntervalMs : eventBacked ? EVENT_BACKED_POLL_INTERVAL_MS * factor : 0
    let delayMs = Math.max(modeMs, this.fallbackIntervalMs * factor)
    if (retryAfterMs !== undefined && retryAfterMs > delayMs) {
      delayMs = retryAfterMs * (1 + POLL_JITTER * this.random())
    }
    this.advance()
    return delayMs
  }

  private advance(): void {
    this.fallbackIntervalMs = Math.min(this.fallbackIntervalMs * FALLBACK_POLL_GROWTH, FALLBACK_POLL_MAX_INTERVAL_MS)
  }

  private jitter(intervalMs: number): number {
    return intervalMs * (1 + POLL_JITTER * (2 * this.random() - 1))
  }
}

/** @internal Whether a wait's poll failed on a rate limit (HTTP 429). */
export function isRateLimitError(error: unknown): error is DaytonaError {
  return error instanceof DaytonaRateLimitError || (error instanceof DaytonaError && error.statusCode === 429)
}

/**
 * @internal The `Retry-After` of a rate-limit error in ms (seconds or an HTTP date), at most
 * {@link MAX_RETRY_AFTER_MS}. Negative, non-finite or unparseable values count as absent.
 */
export function retryAfterMs(error: DaytonaError, now: number = Date.now()): number | undefined {
  const headers = error.headers as unknown as { get?: (name: string) => unknown; [name: string]: unknown } | undefined
  if (!headers) return undefined
  const raw = typeof headers.get === 'function' ? headers.get('retry-after') : headers['retry-after']
  if (raw === undefined || raw === null) return undefined
  const value = String(raw).trim()
  let delayMs: number
  // Delay-seconds is a whole number (RFC 9110).
  if (/^\d+$/.test(value)) {
    delayMs = Number(value) * 1000
  } else if (/^[A-Za-z]{3}/.test(value)) {
    // An HTTP date starts with a day name; Date.parse alone would take '-1' or '2' as years.
    const date = Date.parse(value)
    if (Number.isNaN(date)) return undefined
    delayMs = Math.max(0, date - now)
  } else {
    return undefined
  }
  return Number.isFinite(delayMs) ? Math.min(delayMs, MAX_RETRY_AFTER_MS) : undefined
}
