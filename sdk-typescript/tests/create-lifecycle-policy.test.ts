/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

const LIFECYCLE = {
  timeout_seconds: 120,
  on_timeout: 'delete',
  timeout_at: '2026-11-17T10:02:00.123456Z',
  idle_expiry_seconds: null,
  on_idle_timeout: null,
  idle_timeout_at: null,
  wake_on_request: false,
  last_activity_at: '2026-11-17T10:00:00.123456Z',
  last_activity_source: 'api',
}

const ROUTES = ['create', 'createFromSnapshot', 'createMacos'] as const

async function createWith(route: (typeof ROUTES)[number], lifecycle: unknown) {
  const api = await startFakeApi([
    { status: 202, body: { id: 'sbx', status: 'pending', name: 'sbx', toolbox_proxy_url: 'https://proxy.example', wait_cap_seconds: 0, lifecycle_policy: lifecycle } },
  ])
  const client = clientAt(api.url)
  try {
    const acquire = {
      create: () => client.v2.create({ waitSeconds: 0 }),
      createFromSnapshot: () => client.v2.createFromSnapshot('snap_source', { waitSeconds: 0 }),
      createMacos: () => client.v2.createMacos('macos', { waitSeconds: 0 }),
    }
    const sandbox = await acquire[route]()
    return { sandbox, requests: api.requests.map((request) => request.method) }
  } finally {
    await client.close()
    await api.close()
  }
}

describe('create handle lifecycle policy', () => {
  it.each(ROUTES)('%s carries the lifecycle policy of the create response', async (route) => {
    const { sandbox, requests } = await createWith(route, LIFECYCLE)

    expect(sandbox.lifecyclePolicy).toEqual({
      timeoutSeconds: 120,
      onTimeout: 'delete',
      timeoutAt: '2026-11-17T10:02:00.123456Z',
      idleExpirySeconds: undefined,
      onIdleTimeout: undefined,
      idleTimeoutAt: undefined,
      lastActivityAt: '2026-11-17T10:00:00.123456Z',
      lastActivitySource: 'api',
    })
    expect(sandbox.toJSON().lifecyclePolicy?.timeoutAt).toBe('2026-11-17T10:02:00.123456Z')
    // Read from the create response alone: no detail read.
    expect(requests).toEqual(['POST'])
  })

  it.each([
    null,
    'delete',
    [],
    { ...LIFECYCLE, timeout_at: 'not a timestamp' },
    { ...LIFECYCLE, timeout_seconds: {} },
    { ...LIFECYCLE, on_timeout: 5 },
  ])('leaves a missing or malformed create lifecycle policy (%j) absent', async (lifecycle) => {
    const { sandbox } = await createWith('create', lifecycle)

    expect(sandbox.id).toBe('sbx')
    expect(sandbox.lifecyclePolicy).toBeUndefined()
  })
})
