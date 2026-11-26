/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { GenericAbortSignal } from 'axios'
import { DaytonaError, TransferCancelledError } from './errors/DaytonaError.js'

interface TransitionRetryOptions {
  readonly ensureOpen?: () => void
  readonly signal?: GenericAbortSignal
  readonly deadline?: number
}

/** Retry only a rejected submission: an accepted operation is never replayed. */
export async function retrySandboxTransition<T>(
  attempt: () => Promise<T>,
  options: TransitionRetryOptions = {},
): Promise<T> {
  const deadline = Math.min(Date.now() + 30_000, options.deadline ?? Infinity)
  for (;;) {
    options.ensureOpen?.()
    if (options.signal?.aborted) throw new TransferCancelledError('sandbox transition retry cancelled')
    try {
      return await attempt()
    } catch (error) {
      if (!(error instanceof DaytonaError) || error.code !== 'SANDBOX_TRANSITION_IN_PROGRESS') throw error
      options.ensureOpen?.()
      const delay = Math.min(5, Math.max(1, error.retryAfterSeconds ?? 1)) * 1000
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw error
      await retryDelay(Math.min(delay, remaining), options.signal)
      options.ensureOpen?.()
      if (options.signal?.aborted) throw new TransferCancelledError('sandbox transition retry cancelled')
      if (Date.now() >= deadline) throw error
    }
  }
}

function retryDelay(ms: number, signal: GenericAbortSignal | undefined): Promise<void> {
  if (signal?.aborted) return Promise.reject(new TransferCancelledError('sandbox transition retry cancelled'))
  return new Promise((resolve, reject) => {
    const abort = (): void => {
      clearTimeout(timer)
      signal?.removeEventListener?.('abort', abort)
      reject(new TransferCancelledError('sandbox transition retry cancelled'))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener?.('abort', abort)
      resolve()
    }, ms)
    signal?.addEventListener?.('abort', abort, { once: true })
  })
}
