/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import type { components } from '../src/v2/gen/api'
import { startFakeApi } from './networkPolicyTestServer'

it('reads an unknown class response with additional fields', async () => {
  // Given: an open generated response type and an additional response field.
  const response = {
    id: 'sbx', name: 'sandbox', class: 'future-class', status: 'running',
    cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 2048,
    port_map: [], region_id: 'region-a', unknown_field: 2,
  }
  const typedResponse: components['schemas']['Sandbox'] = response
  const api = await startFakeApi([{ status: 200, body: typedResponse }])
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = api.url
  const client = new Daytona({ apiKey: 'key' })
  try {
    // When: the real HTTP response passes through the SDK's sandbox mapper.
    const sandbox = await client.get('sbx')

    // Then: unknown classes and fields do not prevent reading known fields.
    expect(sandbox).toMatchObject({ id: 'sbx', name: 'sandbox', state: 'started', cpu: 1, memory: 1, disk: 2 })
    expect(sandbox.sandboxClass).toBe('unknown')
    expect(sandbox.rawSandboxClass).toBe('future-class')
  } finally {
    client.close()
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
    await api.close()
  }
})
