/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ImagesService } from '../src/v2/Images'
import { SnapshotsService } from '../src/v2/Snapshots'
import { INTERNAL } from '../src/v2/internal'
import { createHttpClient } from '../src/v2/http'
import { imageWire, snapshotWire } from './artifactFixtures'

describe.each(['image', 'snapshot'] as const)('%s readiness budgets', (kind) => {
  beforeEach(() => { jest.useFakeTimers({ now: 0 }) })
  afterEach(() => { jest.useRealTimers() })

  function fixture(statuses: readonly string[], elapsedMs: number, rootTimeout = 0) {
    const requests: { readonly at: number; readonly timeout?: number; readonly hold: unknown }[] = []
    const http = createHttpClient('http://api.invalid', undefined, rootTimeout)
    http.defaults.adapter = async (config) => {
      const status = statuses[requests.length] ?? 'pending'
      requests.push({ at: Date.now(), timeout: config.timeout, hold: config.params?.timeout_ms })
      jest.setSystemTime(Date.now() + elapsedMs)
      return { config, status: 200, statusText: 'OK', headers: {},
        data: kind === 'image' ? imageWire({ status }) : snapshotWire({ status }) }
    }
    return { requests, service: kind === 'image' ? new ImagesService(INTERNAL, http) : new SnapshotsService(INTERNAL, http) }
  }

  it('repeats capped holds until ready when the total budget exceeds one hold', async () => {
    // Given a build requiring two long polls.
    const { service, requests } = fixture(['pending', 'ready'], 60_000)
    // When waiting for two minutes.
    const artifact = await service.get('artifact', { wait: 'ready', timeoutSeconds: 120 })
    // Then the remaining budget is shared across capped server holds.
    expect(artifact.status).toBe('ready')
    expect(requests).toEqual([
      { at: 0, hold: 60_000, timeout: 65_000 },
      { at: 60_000, hold: 60_000, timeout: 60_000 },
    ])
  })

  it('pauses after an early non-terminal response and returns the last observation at expiry', async () => {
    // Given a peer that repeatedly returns pending immediately.
    const { service, requests } = fixture(['pending'], 0)
    // When a fractional budget expires in the pause between reads.
    const pending = service.get('artifact', { wait: 'ready', timeoutSeconds: 1.5 })
    await jest.advanceTimersByTimeAsync(1500)
    // Then there is no busy loop and no timeout error replaces the artifact.
    expect((await pending).status).toBe('pending')
    expect(requests).toEqual([{ at: 0, hold: 1500, timeout: 1500 }, { at: 1000, hold: 500, timeout: 500 }])
  })

  it.each(['failed', 'error'])('returns without another hold when the artifact is %s', async (status) => {
    // Given a terminal build observation.
    const { service, requests } = fixture([status], 0)
    // When waiting with a positive budget.
    const artifact = await service.get('artifact', { wait: 'ready', timeoutSeconds: 120 })
    // Then terminal failure ends the readiness wait.
    expect(artifact.status).toBe(status)
    expect(requests).toHaveLength(1)
  })

  it.each([undefined, 0])('sends one default hold when timeoutSeconds is %s', async (timeoutSeconds) => {
    // Given a non-ready artifact and no positive budget.
    const { service, requests } = fixture(['pending'], 0)
    // When observing readiness once.
    const artifact = await service.get('artifact', { wait: 'ready', timeoutSeconds })
    // Then the server's hold default is used, without client-side repetition.
    expect(artifact.status).toBe('pending')
    expect(requests).toEqual([{ at: 0, hold: undefined, timeout: 0 }])
  })

  it('holds for the wait budget even when the request timeout is shorter', async () => {
    // Given a root request timeout shorter than the readiness budget.
    const { service, requests } = fixture(['ready'], 0, 20)
    // When waiting for readiness.
    await service.get('artifact', { wait: 'ready', timeoutSeconds: 2.0004 })
    // Then the hold is the whole (integer) budget and the read outlives it by the slack.
    expect(requests).toEqual([{ at: 0, hold: 2001, timeout: 2001 }])
  })
})
