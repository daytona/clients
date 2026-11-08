/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DockerService, DOCKER_WAIT_DEADLINE_GRACE_MS } from '../src/v2/Docker.js'
import { HttpTransport } from '../src/v2/http.js'
import { BadRequestError, DockerWaitTimeoutError } from '../src/v2/errors/DaytonaError.js'
import { INTERNAL } from '../src/v2/internal'

describe('Docker wait seconds', () => {
  beforeEach(() => { jest.useFakeTimers({ now: 0 }) })
  afterEach(() => { jest.useRealTimers() })

  it('uses the fractional-second deadline to shorten the last status hold', async () => {
    // Given
    const polls: { path?: string; timeout?: number }[] = []
    const transport = new HttpTransport('http://toolbox.invalid')
    transport.http.defaults.adapter = async (config) => {
      polls.push({ path: config.url, timeout: config.timeout })
      jest.setSystemTime(Date.now() + (polls.length === 1 ? 1000 : 500))
      return { config, status: 200, statusText: 'OK', headers: {}, data: { state: 'starting', owner: 'daemon' } }
    }
    // When
    const result = new DockerService(INTERNAL, transport).waitUntilReady({ timeoutSeconds: 1.5 })
    // Then
    await expect(result).rejects.toBeInstanceOf(DockerWaitTimeoutError)
    expect(polls).toEqual([
      { path: '/docker/status?waitMs=1000', timeout: 1500 + DOCKER_WAIT_DEADLINE_GRACE_MS },
      { path: '/docker/status?waitMs=500', timeout: 500 + DOCKER_WAIT_DEADLINE_GRACE_MS },
    ])
  })

  it('waits sixty seconds when the caller omits a deadline', async () => {
    // Given
    let polls = 0
    const transport = new HttpTransport('http://toolbox.invalid')
    transport.http.defaults.adapter = async (config) => {
      polls += 1
      jest.setSystemTime(Date.now() + 1000)
      return { config, status: 200, statusText: 'OK', headers: {}, data: { state: 'starting', owner: 'daemon' } }
    }
    // When
    const result = new DockerService(INTERNAL, transport).waitUntilReady()
    // Then
    await expect(result).rejects.toBeInstanceOf(DockerWaitTimeoutError)
    expect(polls).toBe(60)
    expect(Date.now()).toBe(60_000)
  })

  it('keeps polling past the default budget when timeoutSeconds is zero', async () => {
    // Given
    let polls = 0
    const transport = new HttpTransport('http://toolbox.invalid')
    transport.http.defaults.adapter = async (config) => {
      polls += 1
      jest.setSystemTime(Date.now() + 70_000)
      return { config, status: 200, statusText: 'OK', headers: {},
        data: { state: polls === 1 ? 'starting' : 'ready', owner: 'daemon' } }
    }
    // When
    const result = await new DockerService(INTERNAL, transport).waitUntilReady({ timeoutSeconds: 0 })
    // Then
    expect(result.state).toBe('ready')
    expect(polls).toBe(2)
  })

  it.each([-1, NaN, Infinity])('rejects invalid seconds %s before making a request', async (timeoutSeconds) => {
    // Given
    let calls = 0
    const transport = new HttpTransport('http://toolbox.invalid')
    transport.http.defaults.adapter = async () => { calls += 1; throw new Error('unexpected HTTP call') }
    const service = new DockerService(INTERNAL, transport)
    // When
    const result = service.waitUntilReady({ timeoutSeconds })
    // Then
    await expect(result).rejects.toBeInstanceOf(BadRequestError)
    expect(calls).toBe(0)
  })
})
