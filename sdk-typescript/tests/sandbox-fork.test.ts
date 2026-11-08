/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

describe('sandbox fork completion', () => {
  afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

  it('refuses a fork when the fresh source metadata changes to an unknown class', async () => {
    // Given a previously known class and a newer server response outside the vocabulary.
    const api = await startFakeApi([{ status: 200, body: { id: 'source', class: 'future-class', status: 'running', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const sandbox = createSandbox({ id: 'source', class: 'linux', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When a waiting fork refreshes its source before submission.
      const result = sandbox.fork()
      // Then class routing is checked against that fresh observation before POST.
      await expect(result).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
      expect(api.requests.map((request) => request.method)).toEqual(['GET'])
    } finally {
      await api.close()
    }
  })

  it('rejects the old timeout key before refreshing or posting', async () => {
    // Given a legacy fork option and no planned API requests.
    const api = await startFakeApi([])
    const sandbox = createSandbox({ id: 'source', class: 'linux', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    const options = { waitSeconds: 1, timeout: 1000 }
    try {
      // When the caller supplies the removed key.
      const result = sandbox.fork(options)
      // Then it fails as a typed configuration error before any work starts.
      await expect(result).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      expect(api.requests).toEqual([])
    } finally {
      await api.close()
    }
  })

  it.each([
    { source: 'running', memory: undefined, interim: 'paused', result: 'running', expected: 'started' },
    { source: 'running', memory: false, interim: 'stopped', result: 'running', expected: 'started' },
    { source: 'paused', memory: undefined, interim: 'stopped', result: 'paused', expected: 'paused' },
    { source: 'paused', memory: false, interim: 'paused', result: 'stopped', expected: 'stopped' },
    { source: 'stopped', memory: true, interim: 'paused', result: 'stopped', expected: 'stopped' },
  ])('waits for $expected from Linux $source with memory=$memory', async ({ source, memory, interim, result, expected }) => {
    // Given a stale source and a fork that passes through another resting state.
    const api = await startFakeApi([
      { status: 200, body: { id: 'source', class: 'linux', status: source, name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', forked_from: 'source', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], toolbox_proxy_url: 'https://proxy.example/sandboxes' } },
      { status: 200, body: { id: 'fork', class: 'linux', status: interim, name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 200, body: { id: 'fork', class: 'linux', status: result, name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'linux', status: 'running', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When the default fork wait runs.
      const fork = await sandbox.fork({ memory })
      // Then it waits for exactly the state determined by the fresh source.
      expect(fork.state).toBe(expected)
      expect(api.requests.map((request) => [request.method, request.path.split('?')[0]])).toEqual([
        ['GET', '/sandboxes/source'], ['POST', '/sandboxes/source/fork'], ['GET', '/sandboxes/fork'], ['GET', '/sandboxes/fork'],
      ])
      expect(api.requests[3]?.path).toContain(`wait=${result}`)
    } finally {
      await api.close()
    }
  })

  it.each(['running', 'stopped'])('accepts macOS fork completion at %s without reading source state', async (status) => {
    // Given a known macOS class and a fork that settles independently of cached source state.
    const api = await startFakeApi([
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', forked_from: 'source', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], toolbox_proxy_url: 'https://proxy.example/sandboxes' } },
      { status: 200, body: { id: 'fork', class: 'macos', status, name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'macos', status: 'paused', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When waiting for the fork.
      const fork = await sandbox.fork()
      // Then either macOS completion state resolves with no source GET.
      expect(fork.rawState).toBe(status)
      expect(api.requests.map((request) => [request.method, request.path.split('?')[0]])).toEqual([
        ['POST', '/sandboxes/source/fork'], ['GET', '/sandboxes/fork'],
      ])
    } finally {
      await api.close()
    }
  })

  it('returns at acceptance with waitSeconds=0 and keeps source overrides out of the fork', async () => {
    // Given a source using a per-sandbox URL/token and a separate fork toolbox URL.
    const api = await startFakeApi([
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', forked_from: 'source', toolbox_proxy_url: 'https://fork-toolbox.invalid', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'linux', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url), undefined, undefined, undefined, {
      toolboxUrl: 'https://source-toolbox.invalid', accessToken: 'source-fixture', autoDelete: true,
    })
    try {
      // When waiting is disabled.
      const fork = await sandbox.fork({ waitSeconds: 0 })
      await fork[Symbol.asyncDispose]()
      // Then one request, as for create: no source refresh, no detail read,
      // no wait and no implicit deletion.
      expect(api.requests.map((request) => [request.method, request.path])).toEqual([
        ['POST', '/sandboxes/source/fork'],
      ])
      expect(fork.state).toBe('creating')
      expect(fork.toolboxProxyUrl).toBe('https://fork-toolbox.invalid/fork')
      expect(fork.accessToken).toBeUndefined()
    } finally {
      await api.close()
    }
  })

  it('opts a fork into deletion independently of its source', async () => {
    // Given a persistent source and an explicitly auto-deleting fork.
    const api = await startFakeApi([
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], forked_from: 'source', toolbox_proxy_url: 'https://proxy.example/sandboxes' } },
      { status: 202, body: {} },
      { status: 404, body: { code: 'NOT_FOUND' } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'linux', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      const fork = await sandbox.fork({ waitSeconds: 0, autoDelete: true })
      // When the fork is disposed.
      await fork[Symbol.asyncDispose]()
      // Then only the fork is deleted and waited on.
      expect(api.requests[1]).toMatchObject({ method: 'DELETE', path: '/sandboxes/fork' })
      expect(fork.state).toBe('destroyed')
    } finally {
      await api.close()
    }
  })

  it('returns the observed handle when the fork reaches a terminal state', async () => {
    // Given an accepted macOS fork that fails during provisioning.
    const api = await startFakeApi([
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], forked_from: 'source', toolbox_proxy_url: 'https://proxy.example/sandboxes' } },
      { status: 200, body: { id: 'fork', class: 'macos', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 200, body: { id: 'fork', class: 'linux', status: 'failed', error: 'provisioning failed', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When the fork wait observes failure.
      const fork = await sandbox.fork()
      // Then the handle, including the actionable error, is returned.
      expect(fork).toMatchObject({ state: 'error', error: 'provisioning failed' })
    } finally {
      await api.close()
    }
  })

  it('returns the last observed handle when a fractional fork wait expires', async () => {
    // Given an accepted fork that stays queued.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'fork', class: 'linux', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], forked_from: 'source', toolbox_proxy_url: 'https://proxy.example/sandboxes' } })
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'fork', class: 'macos', status: 'queued', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, api)
    // When the wait budget expires.
    const pending = sandbox.fork({ waitSeconds: 0.25 })
    await jest.advanceTimersByTimeAsync(250)
    const fork = await pending
    // Then timeout is an observation, not a failed acquisition.
    expect(fork.state).toBe('queued')
  })

  it.each([403, 408])('returns the fork when a wait read fails with HTTP %s', async (status) => {
    // Given an accepted fork with pending detail metadata.
    const api = await startFakeApi([
      { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], forked_from: 'source', toolbox_proxy_url: 'https://proxy.example/sandboxes' } },
      { status: 200, body: { id: 'fork', class: 'macos', status: 'pending', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status, body: { code: status === 408 ? 'REQUEST_TIMEOUT' : 'FORBIDDEN', source: 'DAYTONA_API', message: 'request failed' } },
    ])
    const sandbox = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When the wait tries to read the fork.
      const result = await sandbox.fork()
      // Then successful acquisition retains the last observed state.
      expect(result).toMatchObject({ id: 'fork', state: 'creating' })
      expect(api.requests).toHaveLength(3)
    } finally {
      await api.close()
    }
  })

  it.each([403, 408])('raises when the fork POST fails with HTTP %s', async (status) => {
    // Given a rejected fork submission.
    const api = await startFakeApi([{ status, body: { code: status === 408 ? 'REQUEST_TIMEOUT' : 'FORBIDDEN', source: 'DAYTONA_API' } }])
    const sandbox = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When submission fails.
      const result = sandbox.fork()
      // Then the failure remains a request error.
      await expect(result).rejects.toMatchObject({ statusCode: status, source: 'DAYTONA_API' })
    } finally {
      await api.close()
    }
  })
})
