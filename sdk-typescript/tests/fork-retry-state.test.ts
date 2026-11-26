/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ConflictError } from '../src/v2/errors/DaytonaError'
import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'

it.each([
  ['starting', 'running', 'started'],
  ['pausing', 'paused', 'paused'],
  ['stopping', 'stopped', 'stopped'],
] as const)('waits for the settled source after retrying %s', async (initial, settled, state) => {
  jest.useFakeTimers({ doNotFake: ['nextTick', 'setImmediate'] })
  try {
    // Given a source that settles during submission backoff,
    const api = createHttpClient('https://api.example', 'key')
    const detail = { id: 'src', class: 'linux', status: initial, name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], persistent: true }
    let sourceReads = 0
    let posts = 0
    jest.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/sandboxes/src') {
        sourceReads++
        return { data: { ...detail, status: posts > 1 ? settled : initial } }
      }
      return { data: { ...detail, id: 'fork', status: settled } }
    })
    jest.spyOn(api, 'post').mockImplementation(async () => {
      posts++
      if (posts === 1) throw new ConflictError('busy', { code: 'SANDBOX_TRANSITION_IN_PROGRESS' })
      return { data: { id: 'fork', status: 'pending' } }
    })
    const source = createSandbox(detail, api)
    const started = Date.now()
    // When the public method retries and then observes its fork,
    const result = source.fork({ waitSeconds: 10 }).then((fork) => ({ fork, elapsed: Date.now() - started }))
    await jest.advanceTimersByTimeAsync(11_000)
    // Then it returns without spending the readiness budget on a stale target.
    expect(await result).toMatchObject({ fork: { id: 'fork', state }, elapsed: 1000 })
    expect(source.state).toBe(state)
    expect(sourceReads).toBe(2)
  } finally {
    jest.useRealTimers()
    jest.restoreAllMocks()
  }
})
