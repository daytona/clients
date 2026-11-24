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
import { assertKnownOptions, assertOptionsObject, optionKeys, optionalSeconds, waitDeadline } from './options.js'
import { checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

export interface PullVolumeOptions {
  /** Caller-owned identity. Retain it when checking pending work or retrying. */
  readonly requestId: string
  /**
   * Wait in seconds; omitted means 30. Does not cancel work. Zero submits a
   * new requestId, or returns a known one's last outcome without retrying it;
   * during a wait above zero the sandbox itself retries pending, unavailable or
   * rate-limited work. Up to 30 seconds is one request the sandbox holds for that long. A longer wait
   * is spent in rounds of at most 30 seconds with the same requestId while the
   * result is `pending` (a value above 2,147,483.647, about 24.8 days, is no
   * deadline), so no request stays open for the whole wait.
   */
  readonly timeoutSeconds?: number
}

const PULL_VOLUME_KEYS = optionKeys<PullVolumeOptions>()({ requestId: true, timeoutSeconds: true })

export interface PullVolumeResult {
  readonly manifestId?: string
  readonly requestId: string
  readonly status: PullVolumeStatus
  readonly rawStatus: string
}

// The contract's required fields; `manifestId` is required and nullable.
const PULL_VOLUME_RESPONSE: ResponseFields = { requestId: 'string', status: 'string', manifestId: 'string|null' }

function pullVolumeResult(data: unknown): PullVolumeResult {
  const result = checkResponse<toolbox.PullVolumeResponse>(data, PULL_VOLUME_RESPONSE, 'volume pull')
  return Object.freeze({
    manifestId: result.manifestId ?? undefined,
    requestId: result.requestId,
    status: knownOrUnknown(result.status, PULL_VOLUME_STATUSES),
    rawStatus: result.status,
  })
}

/** The longest server hold of one pull request (the contract's `waitMs` maximum). */
const PULL_ROUND_MS = 30_000
/** A round answered `pending` sooner than this is followed by a pause, so an early answer never makes a busy loop. */
const PULL_ROUND_MIN_MS = 1_000

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

  /**
   * Submit/check a pull. A wait up to 30 seconds is one request; a longer one
   * is spent in rounds of at most 30 seconds while the result is `pending`.
   * The SDK never retries an error returned to it, and never generates an identity.
   * `volumeId` is the attached volume's `vol_` ID or its UUID.
   */
  public async pull(volumeId: string, options: PullVolumeOptions): Promise<PullVolumeResult> {
    assertOptionsObject('PullVolumeOptions', options)
    assertKnownOptions('PullVolumeOptions', options, PULL_VOLUME_KEYS)
    assertId(volumeId, 'volumeId')
    assertId(options.requestId, 'requestId')
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
    if (timeoutSeconds === undefined || timeoutSeconds * 1000 <= PULL_ROUND_MS) return this.pullRound(volumeId, options.requestId, timeoutSeconds)
    const deadline = waitDeadline(timeoutSeconds, performance.now())
    for (;;) {
      const started = performance.now()
      const holdMs = deadline === undefined ? PULL_ROUND_MS : Math.min(PULL_ROUND_MS, deadline - started)
      const result = await this.pullRound(volumeId, options.requestId, Math.max(holdMs, 1) / 1000)
      const now = performance.now()
      if (result.status !== 'pending' || (deadline !== undefined && now >= deadline)) return result
      const pause = PULL_ROUND_MIN_MS - (now - started)
      // Never return early: a pause cut at the deadline is followed by one last check there.
      if (pause > 0) await new Promise<void>((resolve) => setTimeout(resolve, deadline === undefined ? pause : Math.min(pause, deadline - now)))
    }
  }

  /** One request: the sandbox holds it for `timeoutSeconds` (0–30; omitted, its 30-second default). */
  private async pullRound(volumeId: string, requestId: string, timeoutSeconds: number | undefined): Promise<PullVolumeResult> {
    const waitMs = timeoutSeconds === undefined ? undefined
      : timeoutSeconds === 0 ? 0 : Math.max(1, Math.floor(timeoutSeconds * 1000))
    const body: toolbox.PullVolumeRequest = { requestId, waitMs }
    // Leave time to receive pending after the server wait, including a zero wait.
    return pullVolumeResult(await this.transport.post<toolbox.PullVolumeResponse>(`/volumes/${strictUriEncode(volumeId)}/pull`, body, {
      timeout: (waitMs ?? PULL_ROUND_MS) + WAIT_READ_SLACK_MS,
    }))
  }
}
