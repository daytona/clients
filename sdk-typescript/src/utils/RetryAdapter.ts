/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */
import { trace } from '@opentelemetry/api'
import { AxiosError, CanceledError } from 'axios'
import type { AxiosAdapter, GenericAbortSignal, InternalAxiosRequestConfig } from 'axios'

/**
 * Transport-level retry for transient connection failures, modelled on the
 * Python async SDK's `SharedAiohttpSession`.
 *
 * Node's http client never retries a request whose kept-alive socket the server
 * already closed; that surfaces as `socket hang up` (ECONNRESET), typically
 * after a proxy/LB rollout. This wraps the axios adapter so each logical
 * request gets a small retry budget, without re-running request interceptors
 * (one span covers all attempts).
 *
 * Policy: a failure is replayed on any method only when it provably happened
 * before any bytes were written (DNS / TCP connect). Anything that may have
 * reached the server — including `socket hang up`, which only says the peer
 * closed before writing a response — is replayed for idempotent methods only,
 * since the server may already have executed the request.
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
// processed it — so only idempotent methods are retried. This includes
// "socket hang up": Node raises it both for a stale keep-alive socket that
// never read the request and for a peer that processed it and then dropped
// the connection, and the client cannot tell the two apart.
const MID_FLIGHT_CODES: ReadonlySet<string> = new Set(['ECONNRESET', 'EPIPE'])

type RetryVerdict = 'any-method' | 'idempotent-only' | 'never'

interface ConnectionRetryOptions {
  readonly maxRetries?: number
  readonly sleep?: (ms: number, signal?: GenericAbortSignal) => Promise<void>
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

export function retryBackoffSleep(ms: number, signal?: GenericAbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve()
  return new Promise<void>((resolve) => {
    const onAbort = () => {
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener?.('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener?.('abort', onAbort, { once: true })
  })
}

/**
 * Wraps an axios adapter with connection-level retries. See module docs for
 * the classification rules.
 */
export function withConnectionRetry(base: AxiosAdapter, options: ConnectionRetryOptions = {}): AxiosAdapter {
  const maxRetries = options.maxRetries ?? MAX_RETRIES
  const sleep = options.sleep ?? retryBackoffSleep

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
        await sleep(backoffMs(attempt), config.signal)
        if (config.signal?.aborted) throw new CanceledError(undefined, config)
      }
    }
  }
}
