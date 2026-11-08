/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { RequestTimeoutError } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

describe('create wait error ownership', () => {
  it.each(['create', 'createFromSnapshot', 'createMacos'] as const)('returns the handle when %s encounters a server 408 while waiting', async (route) => {
    // Given an accepted create followed by a failed API read.
    const api = await startFakeApi([
      { status: 202, body: { id: 'sbx', class: 'linux', status: 'pending', wait_cap_seconds: 0, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] } },
      { status: 408, body: { code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API', message: 'request timed out' } },
    ])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create(),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source'),
        createMacos: () => client.v2.createMacos('macos'),
      }
      // When a wait read fails after successful submission.
      const sandbox = await acquire[route]()
      // Then acquisition succeeds with the last known state, after the failed wait read.
      expect(sandbox).toMatchObject({ id: 'sbx', state: 'creating' })
      expect(api.requests.map((request) => [request.method, request.path.split('?')[0]])).toEqual([
        ['POST', { create: '/sandboxes', createFromSnapshot: '/sandboxes/from-snapshot', createMacos: '/sandboxes/macos' }[route]],
        ['GET', '/sandboxes/sbx'],
      ])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['create', 'createFromSnapshot', 'createMacos'] as const)('preserves a server 408 when the %s POST fails', async (route) => {
    // Given a server-originated submission timeout.
    const api = await startFakeApi([{ status: 408, body: { code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API', message: 'server timeout' } }])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create(),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source'),
        createMacos: () => client.v2.createMacos('macos'),
      }
      // When submission fails before a handle is available.
      const pending = acquire[route]()
      // Then the server error is not reclassified as a client timeout.
      await expect(pending).rejects.toBeInstanceOf(RequestTimeoutError)
      await expect(pending).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API', message: 'server timeout' })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['create', 'createFromSnapshot', 'createMacos'] as const)('returns the handle when %s observes a terminal sandbox state', async (route) => {
    // Given an accepted create whose sandbox fails during provisioning.
    const api = await startFakeApi([
      { status: 202, body: { id: 'sbx', class: 'linux', status: 'pending', wait_cap_seconds: 0, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] } },
      { status: 200, body: { id: 'sbx', class: 'linux', status: 'failed', error: 'provisioning failed', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] } },
    ])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create(),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source'),
        createMacos: () => client.v2.createMacos('macos'),
      }
      // When the wait observes the terminal state.
      const sandbox = await acquire[route]()
      // Then the caller receives the failed handle to inspect.
      expect(sandbox.state).toBe('error')
      expect(sandbox.error).toBe('provisioning failed')
    } finally {
      await client.close()
      await api.close()
    }
  })
})
