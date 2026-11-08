/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'

// One rule for `null` on every create and fork option: it behaves exactly as
// Python's `None` does for that parameter — omitted, refused, or (the network
// policy) an explicit null. Python's twin table is tests/test_null_options.py.
type Route = 'create' | 'createFromSnapshot' | 'createMacos'
const OMITTED: Readonly<Record<Route, readonly string[]>> = {
  create: ['name', 'labels', 'ports', 'lifecyclePolicy', 'region', 'createTimeoutSeconds', 'idempotencyKey', 'volumes', 'env', 'resources', 'secrets', 'spot', 'user', 'cpuArch', 'cpuType'],
  createFromSnapshot: ['name', 'labels', 'ports', 'lifecyclePolicy', 'region', 'createTimeoutSeconds', 'idempotencyKey'],
  createMacos: ['name', 'labels', 'ports', 'lifecyclePolicy', 'region', 'createTimeoutSeconds', 'idempotencyKey', 'volumes', 'env', 'secrets', 'resources', 'persistent', 'reservation', 'autoRenew'],
}

function call(client: Daytona, route: Route, options: Record<string, unknown>): Promise<unknown> {
  const all = { waitSeconds: 0, ...options }
  return route === 'create' ? client.v2.create('img_1', all)
    : route === 'createFromSnapshot' ? client.v2.createFromSnapshot('snap_1', all)
      : client.v2.createMacos('sonoma', all)
}

async function body(route: Route, options: Record<string, unknown>): Promise<Record<string, unknown>> {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await call(client, route, options)
    const sent = JSON.parse(api.requests[0]?.body ?? '{}') as Record<string, unknown>
    // A random idempotency key differs per call; the rest must match.
    delete sent.idempotency_key
    return sent
  } finally {
    await client.close()
    await api.close()
  }
}

describe.each(Object.keys(OMITTED) as Route[])('%s', (route) => {
  it.each(OMITTED[route])('reads %s: null as omitted, as Python reads None', async (key) => {
    expect(await body(route, { [key]: null })).toEqual(await body(route, {}))
  })

  it('refuses waitSeconds: null, as Python refuses None', async () => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      await expect(call(client, route, { waitSeconds: null })).rejects.toBeInstanceOf(BadRequestError)
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it.each(['create', 'createMacos'] as const)('%s sends networkPolicy: null as an explicit null policy, as Python sends None', async (route) => {
  expect(await body(route, { networkPolicy: null })).toMatchObject({ network_policy: null })
})

it('reads fork name and memory: null as omitted and refuses waitSeconds: null', async () => {
  const fork = { id: 'f', status: 'pending', forked_from: 'src', toolbox_proxy_url: 'https://proxy.example/sandboxes' }
  const api = await startFakeApi([{ status: 200, body: fork }])
  try {
    const source = createSandbox({ id: 'src', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))
    await source.fork({ name: null as never, memory: null as never, waitSeconds: 0 })
    expect(JSON.parse(api.requests[0]?.body ?? '')).toEqual({})
    await expect(source.fork({ waitSeconds: null as never })).rejects.toBeInstanceOf(BadRequestError)
    expect(api.requests).toHaveLength(1)
  } finally {
    await api.close()
  }
})
