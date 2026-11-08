/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { FakeApi } from './networkPolicyTestServer'

type Call = (client: Daytona, api: FakeApi) => Promise<unknown>

const source = (api: FakeApi) => createSandbox({ id: 'src', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))

// The malformed-response rule, for every read of the client, lifecycle and
// platform-service area: a `{}` body is a BadRequestError (400, BAD_REQUEST,
// SDK) naming what is missing, and nothing follows it (no
// `GET /images/undefined`, no fork handle). Python's twin is
// tests/test_malformed_responses.py.
const CALLS: readonly (readonly [string, Call, string])[] = [
  ['get', (client) => client.v2.get('sbx'), 'sandbox response is missing its id'],
  ['images.get', (client) => client.v2.images.get('img'), 'image response is missing its id'],
  ['images.promote', (client) => client.v2.images.promote('img', 'named'), 'image response is missing its id'],
  ['images.build', (client) => client.v2.images.build(ImageBuilder.fromRegistry('alpine')), 'image create response is missing its id'],
  ['images.pull', (client) => client.v2.images.pull('alpine', { wait: false }), 'image create response is missing its id'],
  ['snapshots.get', (client) => client.v2.snapshots.get('snap'), 'snapshot response is missing its id'],
  ['volumes.get', (client) => client.v2.volumes.get('data'), 'volume response is missing its id'],
  ['volumes.create', (client) => client.v2.volumes.create({ name: 'data' }), 'volume response is missing its id'],
  ['secrets.get', (client) => client.v2.secrets.get('KEY'), 'secret response is missing its name'],
  ['secrets.set', (client) => client.v2.secrets.set('KEY', 'value', { allowedDomains: [] }), 'secret response is missing its name'],
  ['reservations.get', (client) => client.v2.reservations.get('res'), 'reservation response is missing its id'],
  ['reservations.create', (client) => client.v2.reservations.create({ region: 'mac' }), 'reservation response is missing its id'],
  ['reservations.createBulk', (client) => client.v2.reservations.createBulk({ region: 'mac', count: 2 }), 'reservation batch response is missing its requested'],
  ['reservations.renew', (client) => client.v2.reservations.renew('res'), 'reservation response is missing its id'],
  ['reservations.update', (client) => client.v2.reservations.update('res', { autoRenew: false }), 'reservation response is missing its id'],
  ['macModels.catalog', (client) => client.v2.macModels.catalog(), 'Mac catalog response is missing its regions'],
  ['currentApiKey', (client) => client.v2.currentApiKey(), 'API key response is missing its id'],
  ['listAuditActions', (client) => client.v2.listAuditActions(), 'audit actions response is missing its targets'],
  ['getLimits', (client) => client.v2.getLimits({ organizationId: 'org' }), 'organization limits response is missing its sandboxes'],
  ['fork', (_client, api) => source(api).fork({ waitSeconds: 0 }), 'sandbox fork response is missing its id'],
  ['captureImage', (_client, api) => source(api).captureImage(), 'image capture response is missing its id'],
  ['createSnapshot', (_client, api) => source(api).createSnapshot('snap'), 'snapshot create response is missing its id'],
  ['createAccessToken', (_client, api) => source(api).createAccessToken(), 'sandbox access token response is missing its access_token'],
  ['ports.expose', (_client, api) => source(api).ports.expose(8080), 'preview endpoint response is missing its port'],
  ['ports.signedPreviewLink', (_client, api) => source(api).ports.signedPreviewLink(8080), 'signed preview url response is missing its url'],
]

it.each(CALLS)('%s refuses a {} body with BadRequestError and sends nothing after it', async (_name, call, message) => {
  const api = await startFakeApi([{ status: 200, body: {} }])
  const client = clientAt(api.url)
  try {
    const error = await call(client, api).then(() => undefined, (failure: unknown) => failure)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  [{ access_token: 'tok', token_type: 'Bearer', expires_at: 'tomorrow', operations: [] }, 'sandbox access token response has an expires_at that is not a timestamp'],
  [{ access_token: '', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [] }, 'sandbox access token response has an empty access_token'],
  [{ access_token: 'tok', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [1] }, 'sandbox access token response has a operations[0] that is not a string'],
])('refuses a malformed access token %j', async (body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    await expect(source(api).createAccessToken()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await api.close()
  }
})

it('falls back to the list item when the detail read that fills a missing toolbox URL breaks the contract', async () => {
  const item = { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [] }
  const api = await startFakeApi([
    { status: 200, body: { sandboxes: [item], next_cursor: null } },
    { status: 200, body: ['not a sandbox'] },
  ])
  const client = clientAt(api.url)
  try {
    const page = await client.v2.list()
    expect(page.items.map((sandbox) => [sandbox.id, sandbox.state])).toEqual([['sbx', 'started']])
    expect(api.requests.map((request) => request.path)).toEqual(['/sandboxes', '/sandboxes/sbx'])
  } finally {
    await client.close()
    await api.close()
  }
})
