/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'
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
    const sent = JSON.parse(sentRequest(api, 1).body) as Record<string, unknown>
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
    expect(JSON.parse(sentRequest(api, 1).body)).toEqual({})
    await expect(source.fork({ waitSeconds: null as never })).rejects.toMatchObject({ name: 'BadRequestError', message: 'waitSeconds must be a non-negative number of seconds' })
    await expect(source.fork({ name: null as never, memory: null as never, waitSeconds: null as never })).rejects.toMatchObject({ name: 'BadRequestError', message: 'waitSeconds must be a non-negative number of seconds' })
    expect(api.requests).toHaveLength(1)
  } finally {
    await api.close()
  }
})

// The fields of nested option objects follow their Python dataclass fields.
describe('nested option fields', () => {
  it.each([
    ['create', 'resources.cpu', { resources: { cpu: 2, cpuMax: null } }, { resources: { cpu: 2 } }],
    ['create', 'resources.memory', { resources: { memory: null, disk: 10 } }, { resources: { disk: 10 } }],
    ['create', 'resources (all null)', { resources: { cpu: null, cpuMax: null, memory: null, memoryMax: null, disk: null, diskMax: null } }, {}],
    ['create', 'volumes[].subpath and readOnly', { volumes: [{ volumeId: 'v', mountPath: '/m', subpath: null, readOnly: null }] }, { volumes: [{ volumeId: 'v', mountPath: '/m' }] }],
    ['createMacos', 'resources.cpu', { resources: { cpu: null, memory: 8 } }, { resources: { memory: 8 } }],
    ['create', 'lifecyclePolicy.timeoutSeconds', { lifecyclePolicy: { timeoutSeconds: null } }, {}],
    ['create', 'networkPolicy.blockAll', { networkPolicy: { blockAll: null, allowDomains: ['a.example'] } }, { networkPolicy: { allowDomains: ['a.example'] } }],
  ] as const)('%s reads %s: null as omitted, as Python reads None', async (route, _field, withNull, without) => {
    expect(await body(route, withNull)).toEqual(await body(route, without))
  })

  it.each([
    ['ports[].port', { ports: [{ port: null }] }],
    ['ports[].proto', { ports: [{ port: 3000, proto: null }] }],
    ['volumes[].volumeId', { volumes: [{ volumeId: null, mountPath: '/m' }] }],
    ['networkPolicy.allowCidrs', { networkPolicy: { allowCidrs: null } }],
  ] as const)('refuses %s: null, as Python refuses None', async (_field, options) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      await expect(call(client, 'create', options)).rejects.toBeInstanceOf(BadRequestError)
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })
})
