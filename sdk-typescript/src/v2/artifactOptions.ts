/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertKnownOptions, badRequest, optionKeys, optionalSeconds, waitDeadline } from './options.js'
import type { AxiosInstance } from 'axios'
import { sleep } from './http.js'
import { ClientTimeoutError, RequestTimeoutError } from './errors/DaytonaError.js'

export interface GetArtifactOptions {
  readonly wait?: 'ready'
  /** Total readiness budget in seconds. A positive budget repeats holds of at
   * most 60 seconds, pausing up to one second after an early non-terminal
   * response, until the artifact settles (`ready`, `failed` or `error`), which
   * is returned for the caller to check; a budget above the timer maximum
   * waits without a deadline. A budget that runs out first throws
   * `RequestTimeoutError`. Omitted or zero sends one hold of the server's
   * default length and returns what the server reports. */
  readonly timeoutSeconds?: number
}

const HOLD_READ_SLACK_MS = 5_000

const GET_ARTIFACT_OPTION_KEYS = optionKeys<GetArtifactOptions>()({ wait: true, timeoutSeconds: true })

/** A description of an invalid value that cannot itself throw (BigInt, cycles). */
function describe(value: unknown): string {
  try {
    return typeof value === 'string' ? JSON.stringify(value) : String(value)
  } catch {
    return Object.prototype.toString.call(value)
  }
}

/**
 * Read an artifact, as one hold or as repeated holds within a wait budget.
 * `parse` checks each response (the malformed-response rule) before its status
 * is read. Every read is capped at the wait's deadline, as the sandbox waits
 * are, and a budget that runs out before the artifact settles throws
 * `RequestTimeoutError` (source `SDK`), as `images.pull()` and `sandbox.wait()`
 * do: never an unsettled observation. A budget above the timer maximum repeats
 * holds without a deadline.
 * @internal
 */
export async function getArtifact<T extends { readonly status: string }>(http: AxiosInstance, path: string, options: GetArtifactOptions, parse: (data: unknown) => T): Promise<T> {
  assertKnownOptions('GetArtifactOptions', options, GET_ARTIFACT_OPTION_KEYS)
  if (options.wait !== undefined && options.wait !== 'ready') throw badRequest(`wait must be "ready" or omitted, got ${describe(options.wait)}`)
  const seconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
  if (options.wait === undefined) return parse((await http.get<unknown>(path, { params: undefined, timeout: undefined })).data)
  // No budget: one hold of the server's default length, without a client deadline.
  if (seconds === undefined || seconds === 0) return parse((await http.get<unknown>(path, { params: { wait: options.wait, timeout_ms: undefined }, timeout: 0 })).data)
  // Above the timer maximum (about 24.8 days): holds repeat with no deadline.
  const deadline = waitDeadline(seconds)
  const timedOut = () => new RequestTimeoutError('artifact did not become ready before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
  for (;;) {
    const remaining = deadline === undefined ? undefined : deadline - Date.now()
    if (remaining !== undefined && remaining <= 0) throw timedOut()
    const started = Date.now()
    // A long poll is governed by the wait budget, not the request timeout:
    // each hold is the smaller of the remaining budget and the server's 60 s
    // cap, in whole milliseconds, and the read allows the hold plus network
    // slack, never past the deadline.
    const holdMs = remaining === undefined ? 60_000 : Math.min(60_000, Math.max(1, Math.ceil(remaining)))
    const slackRead = holdMs + HOLD_READ_SLACK_MS
    const deadlineOwnsTimeout = remaining !== undefined && remaining <= slackRead
    const timeout = remaining === undefined ? slackRead : Math.max(1, Math.min(slackRead, Math.ceil(remaining)))
    let artifact: T
    try {
      const { data } = await http.get<unknown>(path, { params: { wait: options.wait, timeout_ms: holdMs }, timeout })
      artifact = parse(data)
    } catch (error) {
      // Only expiry of this wait's own cap is the wait running out.
      if (error instanceof ClientTimeoutError && deadline !== undefined && (deadlineOwnsTimeout || Date.now() >= deadline)) throw timedOut()
      throw error
    }
    if (artifact.status === 'ready' || artifact.status === 'failed' || artifact.status === 'error') return artifact
    if (deadline !== undefined && Date.now() >= deadline) throw timedOut()
    const pause = Math.min(1000 - (Date.now() - started), deadline === undefined ? Infinity : deadline - Date.now())
    if (pause > 0) await sleep(pause)
  }
}
