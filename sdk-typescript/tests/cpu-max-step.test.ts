/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ImageBuilder } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'

// `resources.cpuMax` is a positive multiple of 0.5 cores (the contract's
// `cores_max`): any other value is BadRequestError naming it, before any
// request, on `create()` and `createFromBuild()` (before the build). The
// Python twin is tests/test_cpu_max_step.py.
it.each([
  [1.1, 'create'], [0.3, 'create'], [1.1, 'createFromBuild'], [0.3, 'createFromBuild'],
] as const)('refuses cpuMax %d off the half-core step (%s)', async (cpuMax, route) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const call = route === 'create'
      ? client.v2.create('img_1', { resources: { cpuMax }, waitSeconds: 0 })
      : client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { resources: { cpuMax }, waitSeconds: 0 })
    await expect(call).rejects.toMatchObject({ name: 'BadRequestError', message: 'resources.cpuMax must be a positive multiple of 0.5 cores' })
    expect(api.requests).toHaveLength(0)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([0.5, 1, 1.5])('sends cpuMax %d on the half-core step', async (cpuMax) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await client.v2.create('img_1', { resources: { cpuMax }, waitSeconds: 0 })
    expect((JSON.parse(sentRequest(api, 1, 0).body) as { cores_max: number }).cores_max).toBe(cpuMax)
  } finally {
    await client.close()
    await api.close()
  }
})
