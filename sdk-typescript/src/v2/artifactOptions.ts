/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertKnownOptions, optionKeys, optionalSeconds } from './options.js'

export interface GetArtifactOptions {
  readonly wait?: 'ready'
  /** Long-poll budget in seconds; omitted or zero uses the server's default. */
  readonly timeoutSeconds?: number
}

const GET_ARTIFACT_OPTION_KEYS = optionKeys<GetArtifactOptions>()({ wait: true, timeoutSeconds: true })

/** @internal */
export function artifactRequestOptions(options: GetArtifactOptions) {
  assertKnownOptions('GetArtifactOptions', options, GET_ARTIFACT_OPTION_KEYS)
  const seconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
  const timeoutMs = seconds === undefined || seconds === 0 ? undefined : Math.min(Math.ceil(seconds * 1000), 60_000)
  return {
    params: options.wait === undefined ? undefined : { wait: options.wait, timeout_ms: timeoutMs },
    timeout: options.wait === undefined ? undefined : timeoutMs === undefined ? 0 : timeoutMs + 5_000,
  }
}
