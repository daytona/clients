/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertKnownOptions, badRequest, optionKeys, optionalSeconds } from './options.js'
import type { AxiosInstance } from 'axios'
import { sleep } from './http.js'
import { ClientTimeoutError } from './errors/DaytonaError.js'

export interface GetArtifactOptions {
  readonly wait?: 'ready'
  /** Total readiness budget in seconds. Positive budgets repeat holds of at most
   * 60 seconds, pausing up to one second after an early non-terminal response.
   * Returns the last observation when spent. Omitted or zero sends one hold
   * using the server's default. */
  readonly timeoutSeconds?: number
}

const HOLD_READ_SLACK_MS = 5_000

const GET_ARTIFACT_OPTION_KEYS = optionKeys<GetArtifactOptions>()({ wait: true, timeoutSeconds: true })

/** @internal */
export async function getArtifact<T extends { readonly status: string }>(http: AxiosInstance, path: string, options: GetArtifactOptions): Promise<T> {
  assertKnownOptions('GetArtifactOptions', options, GET_ARTIFACT_OPTION_KEYS)
  if (options.wait !== undefined && options.wait !== 'ready') throw badRequest(`wait must be "ready" or omitted, got ${JSON.stringify(options.wait)}`)
  const seconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
  const deadline = options.wait === undefined || seconds === undefined || seconds === 0 ? undefined : Date.now() + seconds * 1000
  let last: T | undefined
  for (;;) {
    const remaining = deadline === undefined ? undefined : deadline - Date.now()
    if (remaining !== undefined && remaining <= 0 && last !== undefined) return last
    const started = Date.now()
    // A long poll is governed by the wait budget, not the request timeout:
    // each hold is the smaller of the remaining budget and the server's 60 s
    // cap, in whole milliseconds, and the read allows the hold plus network
    // slack. A hold of the server's default length reads without a client deadline.
    const holdMs = remaining === undefined ? undefined : Math.min(60_000, Math.max(1, Math.ceil(remaining)))
    const timeout = options.wait === undefined ? undefined : holdMs === undefined ? 0 : holdMs + HOLD_READ_SLACK_MS
    try {
      const { data } = await http.get<T>(path, {
        params: options.wait === undefined ? undefined : { wait: options.wait, timeout_ms: holdMs },
        timeout,
      })
      last = data
    } catch (error) {
      if (error instanceof ClientTimeoutError && deadline !== undefined && last !== undefined && Date.now() >= deadline) return last
      throw error
    }
    if (deadline === undefined || last.status === 'ready' || last.status === 'failed' || last.status === 'error') return last
    const pause = Math.min(1000 - (Date.now() - started), deadline - Date.now())
    if (pause > 0) await sleep(pause)
  }
}
