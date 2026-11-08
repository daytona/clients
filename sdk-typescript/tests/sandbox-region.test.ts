/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

describe('sandbox region', () => {
  const originalApiUrl = process.env.DAYTONA_V2_API_URL
  beforeEach(() => { delete process.env.DAYTONA_V2_API_URL })
  afterEach(() => {
    if (originalApiUrl === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = originalApiUrl
  })
  describe.each(['create', 'get'])('%s', (operation) => {
    it.each([
      { id: '57VSdKNrJGxnSAabjcHD4M-region-a', region: undefined, expected: 'region-a' },
      { id: '57VSdKNrJGxnSAabjcHD4M-region-a', region: '', expected: 'region-a' },
      { id: '57VSdKNrJGxnSAabjcHD4M', region: '', expected: undefined },
      { id: '57VSdKNrJGxnSAabjcHD4M-region-a', region: 'region-b', expected: 'region-b' },
      { id: '57VSdKNrJGxnSAabjcHD4M', region: undefined, expected: undefined },
      { id: '550e8400-e29b-41d4-a716-446655440000', region: undefined, expected: undefined },
    ])('resolves $id with region $region to $expected', async (fixture) => {
      // Given responses covering explicit regions, region tags and untagged ids.
      const api = await startFakeApi([{
        status: 200,
        body: { id: fixture.id, class: 'linux', ...(operation === 'get' ? { region_id: fixture.region } : {}), name: 'test', status: 'running', toolbox_proxy_url: 'http://toolbox.invalid', wait_cap_seconds: 0, cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] },
      }])
      process.env.DAYTONA_V2_API_URL = api.url
      const client = new Daytona({ apiKey: 'key' })
      try {
        // When a public operation produces a sandbox.
        const sandbox = operation === 'create'
          ? await client.create({ waitSeconds: 0 })
          : await client.get(fixture.id)

        // Then it preserves the complete multi-part region tag, unless overridden.
        expect(sandbox.region).toBe(operation === 'get' ? fixture.expected : (fixture.id.endsWith('-region-a') ? 'region-a' : undefined))
      } finally {
        await client.close()
        await api.close()
      }
    })
  })

  it('keeps the region tag when the refreshed region is empty', async () => {
    // Given a fetched sandbox whose next response has an empty region.
    const id = '57VSdKNrJGxnSAabjcHD4M-region-a'
    const response = { id, class: 'linux', name: 'test', status: 'running', toolbox_proxy_url: 'http://toolbox.invalid', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] }
    const api = await startFakeApi([
      { status: 200, body: { ...response, region_id: 'region-a' } },
      { status: 200, body: { ...response, region_id: '' } },
    ])
    process.env.DAYTONA_V2_API_URL = api.url
    const client = new Daytona({ apiKey: 'key' })
    try {
      const sandbox = await client.get(id)
      // When metadata is refreshed.
      await sandbox.refreshData()
      // Then an empty region does not erase the region tag.
      expect(sandbox.region).toBe('region-a')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('uses the region tag when the serialized region is empty', () => {
    // Given serialized metadata with an empty region.
    const info = { sandboxId: '57VSdKNrJGxnSAabjcHD4M-region-a', region: '' }
    // When a low-level handle is constructed.
    const sandbox = createSandbox(info)
    // Then the complete region tag remains available.
    expect(sandbox.region).toBe('region-a')
  })

  it.each([
    { id: '57VSdKNrJGxnSAabjcHD4M-region-a', expected: 'region-a' },
    { id: '57VSdKNrJGxnSAabjcHD4M', expected: undefined },
  ])('preserves the id-derived region in handle JSON for $id', ({ id, expected }) => {
    // Given a metadata-less handle with only its id.
    const sandbox = createSandbox(id)
    // When the handle is serialized.
    const payload = sandbox.toJSON()
    // Then the recovered region survives, without inventing one for untagged ids.
    expect(payload.region).toBe(expected)
  })

  it('uses the region tag when the create selector is local', async () => {
    // Given a create response with a region-tagged id.
    const api = await startFakeApi([{ status: 200, body: {
      id: '57VSdKNrJGxnSAabjcHD4M-region-a', name: 'test', status: 'running',
      toolbox_proxy_url: 'http://toolbox.invalid', wait_cap_seconds: 0,
    } }])
    process.env.DAYTONA_V2_API_URL = api.url
    const client = new Daytona({ apiKey: 'key' })
    try {
      // When the default region is requested.
      const sandbox = await client.create({ region: 'local', waitSeconds: 0 })

      // Then the region tag resolves the default selector.
      expect(sandbox.region).toBe('region-a')
    } finally {
      await client.close()
      await api.close()
    }
  })
})
