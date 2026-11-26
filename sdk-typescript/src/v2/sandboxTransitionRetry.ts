/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError } from './errors/DaytonaError.js'

interface TransitionRetryOptions {
  readonly ensureOpen?: () => void
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
    try {
      return await attempt()
    } catch (error) {
      if (!(error instanceof DaytonaError) || error.code !== 'SANDBOX_TRANSITION_IN_PROGRESS') throw error
      options.ensureOpen?.()
      const delay = Math.min(5, Math.max(1, error.retryAfterSeconds ?? 1)) * 1000
      // A retry the window cannot admit after the full pause is not worth waiting for.
      if (deadline - Date.now() <= delay) throw error
      await new Promise<void>((resolve) => setTimeout(resolve, delay))
      options.ensureOpen?.()
    }
  }
}
