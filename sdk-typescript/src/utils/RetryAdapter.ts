/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */
import { trace } from '@opentelemetry/api'
import { AxiosAdapter, AxiosError, InternalAxiosRequestConfig } from 'axios'

/**
 * Transport-level retry for transient connection failures, mirroring the
 * Python SDK (`urllib3_retry.RemoteDisconnectedRetry` / `SharedAiohttpSession`).
 *
 * Node's http client, unlike Go's transport or urllib3, never retries a request
 * whose kept-alive socket the server already closed. That surfaces as
 * `socket hang up` (ECONNRESET) — typically after a proxy/LB rollout — even
 * though the request was never processed. This wraps the axios adapter so each
 * logical request gets a small retry budget for exactly those cases, without
 * re-running request interceptors (one span covers all attempts).
 */

const MAX_RETRIES = 2
// Backoff = base * attempt + uniform(0, jitter); jitter de-correlates fleets
// that hit the same blip so they don't return in synchronized waves.
const BACKOFF_BASE_MS = 250
const BACKOFF_JITTER_MS = 100

// RFC 9110 §9.2.2 — safe to replay even if the server saw the first attempt.
const IDEMPOTENT_METHODS: ReadonlySet<string> = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE', 'PUT', 'DELETE'])

// Raised before any bytes reach the socket (DNS / TCP connect). The server
// cannot have seen the request, so any method may be retried.
const CONNECT_PHASE_CODES: ReadonlySet<string> = new Set([
  'ECONNREFUSED',
  'ENOTFOUND',
  'EAI_AGAIN',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'EADDRNOTAVAIL',
])

// May fire after the request was (partially) written — the server may have
// started processing it — so only idempotent methods are retried.
const MID_FLIGHT_CODES: ReadonlySet<string> = new Set(['ECONNRESET', 'EPIPE'])

type RetryVerdict = 'any-method' | 'idempotent-only' | 'never'

interface ConnectionRetryOptions {
  readonly maxRetries?: number
  readonly sleep?: (ms: number) => Promise<void>
}

function syscallOf(error: AxiosError): string | undefined {
  const cause: unknown = error.cause
  if (typeof cause === 'object' && cause !== null && 'syscall' in cause) {
    const syscall: unknown = (cause as { syscall?: unknown }).syscall
    return typeof syscall === 'string' ? syscall : undefined
  }
  return undefined
}

function classify(error: AxiosError): RetryVerdict {
  if (error.response || !error.code) return 'never'
  // axios' own deadline (ECONNABORTED / clarified ETIMEDOUT) is never a stale socket.
  if (error.code === AxiosError.ECONNABORTED || error.code === AxiosError.ETIMEDOUT) {
    return syscallOf(error) === 'connect' ? 'any-method' : 'never'
  }
  // Node reports "socket hang up" only when the peer closed before a single
  // response byte arrived — the request was never processed.
  if (error.code === 'ECONNRESET' && error.message === 'socket hang up') return 'any-method'
  if (CONNECT_PHASE_CODES.has(error.code) || syscallOf(error) === 'connect') return 'any-method'
  if (MID_FLIGHT_CODES.has(error.code)) return 'idempotent-only'
  return 'never'
}

/** Streams (Node Readable, web ReadableStream, `form-data`) are consumed by the first attempt. */
function isReplayable(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return true
  const shape = data as { pipe?: unknown; getReader?: unknown; getBoundary?: unknown }
  return (
    typeof shape.pipe !== 'function' && typeof shape.getReader !== 'function' && typeof shape.getBoundary !== 'function'
  )
}

function retryableError(error: unknown, config: InternalAxiosRequestConfig): AxiosError | undefined {
  if (!(error instanceof AxiosError) || config.signal?.aborted || !isReplayable(config.data)) return undefined
  const method = (config.method ?? 'get').toUpperCase()
  switch (classify(error)) {
    case 'any-method':
      return error
    case 'idempotent-only':
      return IDEMPOTENT_METHODS.has(method) ? error : undefined
    case 'never':
      return undefined
  }
}

function backoffMs(attempt: number): number {
  return BACKOFF_BASE_MS * attempt + Math.random() * BACKOFF_JITTER_MS
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/**
 * Wraps an axios adapter with connection-level retries. See module docs for
 * the classification rules.
 */
export function withConnectionRetry(base: AxiosAdapter, options: ConnectionRetryOptions = {}): AxiosAdapter {
  const maxRetries = options.maxRetries ?? MAX_RETRIES
  const sleep = options.sleep ?? defaultSleep

  return async (config) => {
    for (let attempt = 1; ; attempt++) {
      try {
        return await base(config)
      } catch (error) {
        const retryable = attempt <= maxRetries ? retryableError(error, config) : undefined
        if (!retryable) throw error
        trace.getActiveSpan()?.addEvent('http.request.retry', {
          'retry.attempt': attempt,
          'error.code': retryable.code ?? '',
          'error.message': retryable.message,
        })
        await sleep(backoffMs(attempt))
      }
    }
  }
}
