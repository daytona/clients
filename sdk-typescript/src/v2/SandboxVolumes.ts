/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as toolbox from './gen/toolbox-types.js'
import { BadRequestError } from './errors/DaytonaError.js'
import { HttpTransport, strictUriEncode } from './http.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { WAIT_READ_SLACK_MS } from './Process.js'
import { PULL_VOLUME_STATUSES } from './types.js'
import type { PullVolumeStatus } from './types.js'
import { knownOrUnknown } from './knownValue.js'
import { assertKnownOptions, assertOptionsObject, optionKeys, optionalSeconds } from './options.js'

export interface PullVolumeOptions {
  /** Caller-owned identity. Retain it when checking pending work or retrying. */
  readonly requestId: string
  /**
   * Server wait in seconds (0–30); omitted means 30. Does not cancel work.
   * Zero submits a new requestId, or returns a known one's last outcome without
   * retrying it; a wait above zero retries pending, unavailable or rate-limited work.
   */
  readonly timeoutSeconds?: number
}

const PULL_VOLUME_KEYS = optionKeys<PullVolumeOptions>()({ requestId: true, timeoutSeconds: true })

export interface PullVolumeResult {
  readonly manifestId: string | null
  readonly requestId: string
  readonly status: PullVolumeStatus
  readonly rawStatus: string
}

function pullVolumeResult(result: toolbox.PullVolumeResponse): PullVolumeResult {
  return {
    manifestId: result.manifestId,
    requestId: result.requestId,
    status: knownOrUnknown(result.status, PULL_VOLUME_STATUSES),
    rawStatus: result.status,
  }
}

function assertId(value: string, name: string): void {
  if (typeof value !== 'string' || value.length < 1 || value.length > 128 || /[^A-Za-z0-9_-]/.test(value)) {
    throw new BadRequestError(`${name} must contain 1–128 ASCII letters, digits, underscores or hyphens`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

/** Pull attached volumes in this sandbox; distinct from the platform volume service. */
export class SandboxVolumesService {
  /** @internal */
  constructor(token: Internal, transport: HttpTransport)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly transport: HttpTransport) {
    assertInternal(token, 'SandboxVolumesService')
  }

  /** One submission/check only. Pending and errors never trigger automatic retries. */
  public async pull(volumeId: string, options: PullVolumeOptions): Promise<PullVolumeResult> {
    assertOptionsObject('PullVolumeOptions', options)
    assertKnownOptions('PullVolumeOptions', options, PULL_VOLUME_KEYS)
    assertId(volumeId, 'volumeId')
    assertId(options.requestId, 'requestId')
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
    if (timeoutSeconds !== undefined && timeoutSeconds > 30) {
      throw new BadRequestError('timeoutSeconds must be in 0..30 seconds', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    const waitMs = timeoutSeconds === undefined ? undefined
      : timeoutSeconds === 0 ? 0 : Math.max(1, Math.floor(timeoutSeconds * 1000))
    const body: toolbox.PullVolumeRequest = {
      requestId: options.requestId,
      waitMs,
    }
    // Leave time to receive pending after the server wait, including a zero wait.
    return pullVolumeResult(await this.transport.post<toolbox.PullVolumeResponse>(`/volumes/${strictUriEncode(volumeId)}/pull`, body, {
      timeout: (waitMs ?? 30_000) + WAIT_READ_SLACK_MS,
    }))
  }
}
