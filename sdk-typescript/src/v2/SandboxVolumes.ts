/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as toolbox from './gen/toolbox-types.js'
import { BadRequestError } from './errors/DaytonaError.js'
import { HttpTransport, strictUriEncode } from './http.js'
import { WAIT_READ_SLACK_MS } from './Process.js'
import { PULL_VOLUME_STATUSES } from './types.js'
import type { PullVolumeStatus } from './types.js'
import { knownOrUnknown } from './knownValue.js'

export interface PullVolumeOptions {
  /** Caller-owned identity. Retain it when checking pending work or retrying. */
  readonly requestId: string
  /**
   * Server wait in milliseconds (0–30000); omitted means 30000. Does not cancel work.
   * Zero submits a new requestId, or returns a known one's last outcome without
   * retrying it; a wait above zero retries pending, unavailable or rate-limited work.
   */
  readonly timeout?: number
}

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
  constructor(private readonly transport: HttpTransport) {}

  /** One submission/check only. Pending and errors never trigger automatic retries. */
  public async pull(volumeId: string, options: PullVolumeOptions): Promise<PullVolumeResult> {
    assertId(volumeId, 'volumeId')
    assertId(options.requestId, 'requestId')
    if (options.timeout !== undefined && (!Number.isInteger(options.timeout) || options.timeout < 0 || options.timeout > 30_000)) {
      throw new BadRequestError('timeout must be an integer in 0..30000 milliseconds', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    const body: toolbox.PullVolumeRequest = {
      requestId: options.requestId,
      ...(options.timeout === undefined ? {} : { waitMs: options.timeout }),
    }
    // Leave time to receive pending after the server wait, including a zero wait.
    return pullVolumeResult(await this.transport.post<toolbox.PullVolumeResponse>(`/volumes/${strictUriEncode(volumeId)}/pull`, body, {
      timeout: (options.timeout ?? 30_000) + WAIT_READ_SLACK_MS,
    }))
  }
}
