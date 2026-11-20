/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError, DaytonaRateLimitError } from '../errors/DaytonaError'

// State events drive waits; polling is only a safety net, so keep it sparse while events flow.
export const EVENT_BACKED_POLL_INTERVAL_MS = 5000
// Subscribed but no events arrive (connection down, subscription rejected or over the connection's
// limit): start at 1 s and back off, so thousands of such waits do not exhaust the request budget.
export const FALLBACK_POLL_INTERVAL_MS = 1000
export const FALLBACK_POLL_GROWTH = 1.5
export const FALLBACK_POLL_MAX_INTERVAL_MS = 10_000
// Spreads the polls of concurrent waits apart.
export const POLL_JITTER = 0.2

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

/** @internal The `Retry-After` of a rate-limit error in ms (seconds or an HTTP date), if present. */
export function retryAfterMs(error: DaytonaError, now: number = Date.now()): number | undefined {
  const headers = error.headers as unknown as { get?: (name: string) => unknown; [name: string]: unknown } | undefined
  if (!headers) return undefined
  const raw = typeof headers.get === 'function' ? headers.get('retry-after') : headers['retry-after']
  if (raw === undefined || raw === null) return undefined
  const value = String(raw).trim()
  if (/^\d+(\.\d+)?$/.test(value)) return Number(value) * 1000
  const date = Date.parse(value)
  return Number.isNaN(date) ? undefined : Math.max(0, date - now)
}
