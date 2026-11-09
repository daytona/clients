/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { PayloadTooLargeError } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { PlannedResponse } from './networkPolicyTestServer'

// The client's `maxResponseBytes` reaches every transport the client builds:
// the control-plane API, and the toolbox transport of a sandbox handle the
// client created. A buffered response above it is PayloadTooLargeError (413,
// PAYLOAD_TOO_LARGE, SDK). The Python twin is tests/test_client_response_cap.py.
const CAP = 2048
const detail = (url: string, extra: Record<string, string> = {}) => ({ id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', toolbox_proxy_url: `${url}/toolbox`, ...extra })

it('caps the control plane and the toolbox of a handle the client created', async () => {
  // The fake shifts from this array, so answers that name its URL go in after it starts.
  const planned: PlannedResponse[] = []
  const api = await startFakeApi(planned)
  planned.push(
    // get(): a detail padded past the cap.
    { status: 200, body: detail(api.url, { padding: 'x'.repeat(CAP * 2) }) },
    // get(): a detail under it, for the handle.
    { status: 200, body: detail(api.url) },
    // The handle's toolbox read, past the cap.
    { status: 200, body: 'y'.repeat(CAP * 2), headers: { 'content-type': 'application/octet-stream' } },
  )
  const client = clientAt(api.url, { maxResponseBytes: CAP })
  try {
    const controlPlane: unknown = await client.v2.get('sbx').catch((error: unknown) => error)
    const sandbox = await client.v2.get('sbx', { accessToken: 'tok' })
    const toolbox: unknown = await sandbox.fs.readBytes('/big').catch((error: unknown) => error)
    for (const error of [controlPlane, toolbox]) {
      expect(error).toBeInstanceOf(PayloadTooLargeError)
      expect(error).toMatchObject({ statusCode: 413, code: 'PAYLOAD_TOO_LARGE', source: 'SDK' })
    }
    expect(api.requests.map((request) => request.path.split('?')[0])).toEqual(['/sandboxes/sbx', '/sandboxes/sbx', '/toolbox/sbx/files/download'])
  } finally {
    await client.close()
    await api.close()
  }
})
