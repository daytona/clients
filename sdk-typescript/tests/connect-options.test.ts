/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'
import type { FakeApi, PlannedResponse } from './networkPolicyTestServer'

// `get()`'s connect options: `null` is omitted, as Python's `None` is, so the
// handle mints its own sandbox token; a non-string, a blank toolbox URL and an
// access token that breaks the header rule are BadRequestError naming the
// option before any request. The Python twin is tests/test_connect_options.py.
const detail = (url: string) => ({ id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', toolbox_proxy_url: `${url}/toolbox` })
const MINTED = { access_token: 'minted-bearer', expires_at: '2040-01-01T00:00:00Z', token_type: 'Bearer', operations: [] }
const paths = (api: FakeApi) => api.requests.map((request) => `${request.method} ${request.path.split('?')[0]}`)

it('reads null connect options as omitted, so the handle mints its own token', async () => {
  // The fake shifts from this array, so answers that name its URL go in after it starts.
  const planned: PlannedResponse[] = []
  const api = await startFakeApi(planned)
  planned.push({ status: 200, body: detail(api.url) }, { status: 200, body: MINTED }, { status: 200, body: { files: [] } })
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.get('sbx', { toolboxUrl: null as never, accessToken: null as never })
    expect(sandbox.accessToken).toBeUndefined()
    await sandbox.fs.list('/')
    expect(paths(api)).toEqual(['GET /sandboxes/sbx', 'POST /sandboxes/sbx/access-token', 'GET /toolbox/sbx/files'])
    expect(api.requests[2]?.headers.authorization).toBe('Bearer minted-bearer')
    expect(sandbox.accessToken).toBe('minted-bearer')
  } finally {
    await client.close()
    await api.close()
  }
})

// SAFETY: every `as never` is a deliberately invalid plain-JavaScript argument.
it.each([
  ['a numeric toolboxUrl', { toolboxUrl: 5 as never }, 'toolboxUrl must be a non-empty string'],
  ['a blank toolboxUrl', { toolboxUrl: ' \u0085 ' }, 'toolboxUrl must be a non-empty string'],
  ['a numeric accessToken', { accessToken: 5 as never }, 'accessToken must be a string'],
  ['an empty accessToken', { accessToken: '' }, 'accessToken must be a non-empty string'],
  ['a padded accessToken', { accessToken: ' tok ' }, 'accessToken must be printable ASCII with no leading or trailing whitespace'],
  ['a non-ASCII accessToken', { accessToken: 'tök' }, 'accessToken must be printable ASCII with no leading or trailing whitespace'],
] as const)('refuses %s before any request', async (_name, options, message) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('sbx', options)).rejects.toMatchObject({ name: 'BadRequestError', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
    expect(api.requests).toHaveLength(0)
  } finally {
    await client.close()
    await api.close()
  }
})

it('keeps a valid caller token verbatim and never mints', async () => {
  // The fake shifts from this array, so answers that name its URL go in after it starts.
  const planned: PlannedResponse[] = []
  const api = await startFakeApi(planned)
  planned.push({ status: 200, body: detail(api.url) }, { status: 200, body: { files: [] } })
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.get('sbx', { accessToken: 'caller-token' })
    await sandbox.fs.list('/')
    expect(sentRequest(api, 2, 1).headers.authorization).toBe('Bearer caller-token')
    expect(sandbox.accessToken).toBe('caller-token')
  } finally {
    await client.close()
    await api.close()
  }
})
