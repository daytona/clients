/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { FakeApi, PlannedResponse } from './networkPolicyTestServer'

// A handle of a JWT client (`jwtToken` + `projectId`) mints its own sandbox
// token, as one with an API key does: its toolbox calls send the minted
// bearer, never the project token. A mint route answering 405 falls back to
// the project token, and a caller-supplied `accessToken` is never minted
// over. The Python twin is tests/test_jwt_handle_mint.py.
const EXCHANGE = { access_token: 'project-token', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }
const MINTED = { access_token: 'minted-bearer', expires_at: '2040-01-01T00:00:00Z', token_type: 'Bearer', operations: ['toolbox'] }
const detail = (url: string) => ({ id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', toolbox_proxy_url: `${url}/toolbox` })
const sent = (api: FakeApi) => api.requests.map((request) => [request.method, request.path.split('?')[0], request.headers.authorization])

async function listFiles(answers: readonly PlannedResponse[], accessToken?: string): Promise<FakeApi> {
  // The fake shifts from this array, so answers that name its URL go in after it starts.
  const planned: PlannedResponse[] = []
  const api = await startFakeApi(planned)
  planned.push({ status: 200, body: EXCHANGE }, { status: 200, body: detail(api.url) }, ...answers)
  const client = clientAt(api.url, { apiKey: undefined, jwtToken: 'jwt', projectId: 'project-1' })
  try {
    const sandbox = await client.v2.get('sbx', accessToken === undefined ? {} : { accessToken })
    await sandbox.fs.list('/')
  } finally {
    await client.close()
    await api.close()
  }
  return api
}

it('mints on a JWT handle and sends the minted bearer', async () => {
  const api = await listFiles([{ status: 200, body: MINTED }, { status: 200, body: { items: [] } }])
  expect(sent(api).slice(1)).toEqual([
    ['GET', '/sandboxes/sbx', 'Bearer project-token'],
    ['POST', '/sandboxes/sbx/access-token', 'Bearer project-token'],
    ['GET', '/toolbox/sbx/files', 'Bearer minted-bearer'],
  ])
})

it('falls back to the project token when the mint route answers 405', async () => {
  const api = await listFiles([{ status: 405, body: { message: 'no mint' } }, { status: 200, body: { items: [] } }])
  expect(sent(api).slice(2)).toEqual([
    ['POST', '/sandboxes/sbx/access-token', 'Bearer project-token'],
    ['GET', '/toolbox/sbx/files', 'Bearer project-token'],
  ])
})

it('sends a caller token unchanged on a JWT handle and never mints', async () => {
  const api = await listFiles([{ status: 200, body: { items: [] } }], 'caller-token')
  expect(sent(api).slice(1)).toEqual([
    ['GET', '/sandboxes/sbx', 'Bearer project-token'],
    ['GET', '/toolbox/sbx/files', 'Bearer caller-token'],
  ])
})

// The handle's own mint reads an expiry that is not an RFC 3339 timestamp as
// absent, as a create response's token expiry: the call that needed the token
// still works, and a 401 re-mints (as in Python).
it('sends the minted bearer when the handle mint has a malformed expiry', async () => {
  const api = await listFiles([{ status: 200, body: { ...MINTED, expires_at: 'Jan 1 2030' } }, { status: 200, body: { items: [] } }])
  expect(sent(api).at(-1)).toEqual(['GET', '/toolbox/sbx/files', 'Bearer minted-bearer'])
})
