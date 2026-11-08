/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

it.each([404, 405, 501])('retries minting only for transient HTTP %s', async (status) => {
  // Given a mint failure followed by a successful API-credential fallback.
  const api = await startFakeApi([
    { status, body: { message: 'mint unavailable' } },
    { status: 200, body: { files: [] } },
    ...(status === 404 ? [{ status: 200, body: { access_token: 'minted-bearer', expires_at: '2040-01-01T00:00:00Z' } }] : []),
    { status: 200, body: { files: [] } },
  ])
  const sandbox = createSandbox('sandbox', createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: `${api.url}/toolbox` })
  try {
    await sandbox.fs.list('/')
    // When another toolbox request needs a bearer.
    await sandbox.fs.list('/')
    // Then 404 only affects one request; unsupported routes remain disabled.
    // Minting goes to the API route, file reads to the toolbox route.
    const mint = ['POST', '/sandboxes/sandbox/access-token']
    const read = ['GET', '/toolbox/files']
    expect(api.requests.map((request) => [request.method, request.path.split('?')[0]])).toEqual(
      status === 404 ? [mint, read, mint, read] : [mint, read, read],
    )
    expect(api.requests.filter((request) => request.method === 'POST')).toHaveLength(status === 404 ? 2 : 1)
    const reads = api.requests.filter((request) => request.method === 'GET')
    expect(reads.map((request) => request.headers.authorization)).toEqual(['Bearer key', status === 404 ? 'Bearer minted-bearer' : 'Bearer key'])
    expect(sandbox.accessToken).toBe(status === 404 ? 'minted-bearer' : undefined)
  } finally {
    await api.close()
  }
})
