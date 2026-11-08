/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DockerNotReadyError, DockerWaitTimeoutError, ClientTimeoutError } from './errors/DaytonaError.js'
import { encodeQuery, HttpTransport, sleep } from './http.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as toolbox from './gen/toolbox-types.js'
import { WAIT_READ_SLACK_MS } from './Process.js'
import { DOCKER_OWNERS, DOCKER_STATES } from './types.js'
import type { DockerOwner, DockerState, DockerStatus } from './types.js'
import { knownOrUnknown } from './knownValue.js'
import { assertKnownOptions, optionKeys, optionalSeconds, waitDeadline } from './options.js'
import { checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

export type { DockerOwner, DockerState, DockerStatus } from './types.js'

// The contract's required fields, checked before the mapper reads them (the
// malformed-response rule): a malformed status fails at once rather than read
// as `unknown` until a wait's deadline.
const DOCKER_STATUS: ResponseFields = { owner: 'string', state: 'string', processId: 'string?', reason: 'string?' }

function dockerStatusFromWire(data: unknown): DockerStatus {
  const status = checkResponse<toolbox.DockerStatus>(data, DOCKER_STATUS, 'docker status')
  return Object.freeze({
    owner: knownOrUnknown(status.owner, DOCKER_OWNERS),
    rawOwner: status.owner,
    processId: status.processId,
    reason: status.reason,
    state: knownOrUnknown(status.state, DOCKER_STATES),
    rawState: status.state,
  })
}

export interface DockerWaitOptions {
  /**
   * Wait budget in seconds. Default 60. `0` (or a value above about 24.8 days) waits without a
   * deadline — avoid it for sandboxes whose Docker can stay `deferred`.
   */
  readonly timeoutSeconds?: number
}

const DOCKER_WAIT_KEYS = optionKeys<DockerWaitOptions>()({ timeoutSeconds: true })

/** Default `waitUntilReady()` budget in seconds. */
export const DOCKER_WAIT_DEFAULT_TIMEOUT_SECONDS = 60
/** Longest requested hold per status poll, in milliseconds. */
export const DOCKER_STATUS_MAX_WAIT_MS = 1_000
/** Minimum spacing between polls answered without holding (e.g. `deferred`). */
export const DOCKER_STATUS_POLL_INTERVAL_MS = 500
/**
 * How far past the caller's deadline one status request may run before the
 * transport abandons it. Bounds the overshoot of a stalled request.
 */
export const DOCKER_WAIT_DEADLINE_GRACE_MS = 250
/**
 * States that will not become `ready` during this boot.
 * `starting` and `deferred` remain eligible for polling.
 */
export const DOCKER_TERMINAL_STATES = ['failed', 'not_present', 'disabled', 'unmanaged', 'unsupported'] as const satisfies readonly DockerState[]

const TERMINAL_STATE_SET: ReadonlySet<string> = new Set<string>(DOCKER_TERMINAL_STATES)

function isTerminalDockerStatus(status: DockerStatus): boolean {
  return TERMINAL_STATE_SET.has(status.state)
}

/**
 * Docker Engine status inside the sandbox (`sandbox.docker`). Read-only:
 * nothing here starts or restarts Docker. The sandbox env variable
 * `DAYTONA_DOCKER_START` chooses how Docker starts: `auto` (default; start
 * the image's `dockerd` when it ships one), `entrypoint` (the image's own
 * entrypoint starts Docker; only wait for it) or `off` (neither start nor
 * wait; state `disabled`). Unknown values count as `auto`.
 *
 * If the status endpoint is unavailable, `status()` and `waitUntilReady()`
 * throw `NotFoundError` (404).
 */
export class DockerService {
  /** @internal */
  constructor(token: Internal, transport: HttpTransport)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly transport: HttpTransport) {
    assertInternal(token, 'DockerService')
  }

  /**
   * Current Docker Engine status for this boot, answered immediately.
   * Throws `NotFoundError` (404) when the endpoint is unavailable.
   */
  public async status(): Promise<DockerStatus> {
    return dockerStatusFromWire(await this.transport.get<toolbox.DockerStatus>('/docker/status'))
  }

  /**
   * Poll the Docker status until it is `ready` and return that status.
   *
   * Each poll requests a hold of up to one second while Docker is
   * `starting`; answers that come back sooner are spaced 500 ms apart.
   * Throws {@link DockerNotReadyError} as soon as Docker settles in a state
   * that cannot become `ready` (`failed`, `not_present`, `disabled`,
   * `unmanaged`, `unsupported`), and {@link DockerWaitTimeoutError} when the
   * budget runs out while Docker is still `starting` or `deferred`,
   * including when a status request is still unanswered at
   * the deadline. No request runs more than a fraction of a second past the
   * deadline. `timeoutSeconds: 0`, or a value above about 24.8 days, waits without a deadline.
   */
  public async waitUntilReady(options: DockerWaitOptions = {}): Promise<DockerStatus> {
    assertKnownOptions('DockerWaitOptions', options, DOCKER_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? DOCKER_WAIT_DEFAULT_TIMEOUT_SECONDS
    // 0, or above the timer maximum (about 24.8 days), is no deadline.
    const deadline = waitDeadline(timeoutSeconds)
    let last: DockerStatus | undefined
    for (;;) {
      const remaining = deadline === undefined ? undefined : Math.ceil(deadline - Date.now())
      if (remaining !== undefined && remaining <= 0) throw new DockerWaitTimeoutError(last)
      const waitMs = remaining === undefined ? DOCKER_STATUS_MAX_WAIT_MS : Math.min(DOCKER_STATUS_MAX_WAIT_MS, remaining)
      // Allow response slack without running far past the caller's deadline.
      const requestTimeout = remaining === undefined ? waitMs + WAIT_READ_SLACK_MS : Math.min(waitMs + WAIT_READ_SLACK_MS, remaining + DOCKER_WAIT_DEADLINE_GRACE_MS)
      const pollStarted = Date.now()
      try {
        last = dockerStatusFromWire(await this.transport.get<toolbox.DockerStatus>(`/docker/status${encodeQuery({ waitMs })}`, { timeout: requestTimeout }))
      } catch (error) {
        if (error instanceof ClientTimeoutError && deadline !== undefined && Date.now() >= deadline) throw new DockerWaitTimeoutError(last)
        throw error
      }
      if (last.state === 'ready') return last
      if (isTerminalDockerStatus(last)) throw new DockerNotReadyError(last)
      // `starting`, `deferred`, or a state this SDK does not know yet: keep polling.
      const elapsed = Date.now() - pollStarted
      if (elapsed < DOCKER_STATUS_POLL_INTERVAL_MS) {
        let pause = DOCKER_STATUS_POLL_INTERVAL_MS - elapsed
        if (deadline !== undefined) pause = Math.min(pause, Math.max(0, deadline - Date.now()))
        if (pause > 0) await sleep(pause)
      }
    }
  }
}
