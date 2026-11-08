/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { RequestTimeoutError } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

describe('create wait error ownership', () => {
  it.each(['create', 'createFromSnapshot', 'createMacos'] as const)('propagates a server 408 when %s is waiting', async (route) => {
    // Given an accepted create followed by a failed API read.
    const api = await startFakeApi([
      { status: 202, body: { id: 'sbx', status: 'pending', wait_cap_seconds: 0 } },
      { status: 408, body: { code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API', message: 'request timed out' } },
    ])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create(),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source'),
        createMacos: () => client.v2.createMacos('macos'),
      }
      // When the request fails, rather than the SDK wait ending.
      const pending = acquire[route]()
      // Then the request failure is not converted to a sandbox result.
      await expect(pending).rejects.toBeInstanceOf(RequestTimeoutError)
      await expect(pending).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API' })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['create', 'createFromSnapshot', 'createMacos'] as const)('returns the handle when %s observes a terminal sandbox state', async (route) => {
    // Given an accepted create whose sandbox fails during provisioning.
    const api = await startFakeApi([
      { status: 202, body: { id: 'sbx', status: 'pending', wait_cap_seconds: 0 } },
      { status: 200, body: { id: 'sbx', status: 'failed', error: 'provisioning failed' } },
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
