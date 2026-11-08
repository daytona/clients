/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

describe('sandbox lifecycle completion', () => {
  it.each(['linux', 'macos', 'future'])('waits through stopping on %s without a persistence guard', async (sandboxClass) => {
    // Given a nonpersistent sandbox and an accepted stop that completes later.
    const api = await startFakeApi([
      { status: 202, body: {} },
      { status: 200, body: { id: 'sbx', class: sandboxClass, status: 'stopping', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 200, body: { id: 'sbx', class: sandboxClass, status: 'stopped', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'sbx', class: sandboxClass, persistent: false }, createHttpClient(api.url))
    try {
      // When stop waits with the default budget.
      await sandbox.stop()
      // Then it returns only after a fresh stopped observation.
      expect(sandbox.state).toBe('stopped')
      expect(api.requests.map((request) => request.method)).toEqual(['POST', 'GET', 'GET'])
      expect(api.requests[0]?.path).toBe('/sandboxes/sbx/stop')
      expect(api.requests[1]?.path).toBe('/sandboxes/sbx?wait=stopped&timeout_ms=30000')
    } finally {
      await api.close()
    }
  })

  it.each([
    { result: { status: 200, body: { id: 'sbx', class: 'linux', status: 'deleted', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } } },
    { result: { status: 404, body: { code: 'NOT_FOUND', message: 'gone' } } },
  ])('waits through destroying until deletion completes: $result.status', async ({ result }) => {
    // Given asynchronous deletion that either reports deleted or disappears.
    const api = await startFakeApi([
      { status: 202, body: {} },
      { status: 200, body: { id: 'sbx', class: 'linux', status: 'deleting', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      result,
    ])
    const sandbox = createSandbox('sbx', createHttpClient(api.url))
    try {
      // When deletion has no deadline.
      await sandbox.delete({ timeoutSeconds: 0 })
      // Then a later 404 is completion, just like an explicit destroyed state.
      expect(sandbox.state).toBe('destroyed')
      expect(sandbox.toJSON()).toMatchObject({ state: 'destroyed', rawState: 'deleted' })
      expect(api.requests.map((request) => request.method)).toEqual(['DELETE', 'GET', 'GET'])
    } finally {
      await api.close()
    }
  })

  it.each(['stop', 'delete'] as const)('surfaces terminal errors from %s', async (operation) => {
    // Given an accepted lifecycle request followed by an error state.
    const api = await startFakeApi([{ status: 202, body: {} }, { status: 200, body: { id: 'sbx', class: 'linux', status: 'failed', error: 'failed-operation', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const sandbox = createSandbox('sbx', createHttpClient(api.url))
    try {
      // When completion is awaited.
      const result = sandbox[operation]()
      // Then the terminal failure carries the latest observation.
      await expect(result).rejects.toMatchObject({ state: 'error', source: 'SDK' })
      expect(sandbox.error).toBe('failed-operation')
    } finally {
      await api.close()
    }
  })

  it.each([
    { operation: 'pause', status: 'running', expected: 'started' },
    { operation: 'resume', status: 'stopped', expected: 'stopped' },
  ] as const)('treats $expected as a failed $operation for Linux', async ({ operation, status, expected }) => {
    // Given a Linux transition that returns to its failure state.
    const api = await startFakeApi([{ status: 202, body: {} }, { status: 200, body: { id: 'sbx', status, class: 'linux', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When pause or resume is requested.
      const result = sandbox[operation]()
      // Then the server is called and the observed terminal state is surfaced.
      await expect(result).rejects.toMatchObject({ state: expected })
      expect(api.requests[0]?.path).toBe(`/sandboxes/sbx/${operation}`)
    } finally {
      await api.close()
    }
  })

  it.each([
    { status: 'paused', route: 'resume' },
    { status: 'stopped', route: 'start' },
  ])('starts a $status sandbox using $route after refreshing', async ({ status, route }) => {
    // Given stale started metadata and a fresh paused/stopped observation.
    const api = await startFakeApi([
      { status: 200, body: { id: 'sbx', class: 'linux', status, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 202, body: {} },
      { status: 200, body: { id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    ])
    const sandbox = createSandbox({ id: 'sbx', status: 'running' }, createHttpClient(api.url))
    try {
      // When start routes from the current server state.
      await sandbox.start()
      // Then it uses the correct transition and waits for running.
      expect(api.requests.map((request) => [request.method, request.path.split('?')[0]])).toEqual([
        ['GET', '/sandboxes/sbx'], ['POST', `/sandboxes/sbx/${route}`], ['GET', '/sandboxes/sbx'],
      ])
      expect(sandbox.state).toBe('started')
    } finally {
      await api.close()
    }
  })

  it.each([undefined, false, true])('disposes according to autoDelete=%s', async (autoDelete) => {
    // Given a handle acquired with an explicit or default disposal policy.
    const api = await startFakeApi([{ status: 202, body: {} }, { status: 404, body: { code: 'NOT_FOUND' } }])
    const sandbox = createSandbox('sbx', createHttpClient(api.url), undefined, undefined, undefined, { autoDelete })
    try {
      // When await-using disposal runs.
      await sandbox[Symbol.asyncDispose]()
      // Then only opt-in disposal deletes, waiting for completion.
      expect(api.requests.map((request) => request.method)).toEqual(autoDelete ? ['DELETE', 'GET'] : [])
    } finally {
      await api.close()
    }
  })
})
