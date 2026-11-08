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
import type { FakeApi } from './networkPolicyTestServer'

type Lister = (client: Daytona, api: FakeApi) => Promise<unknown>

// Every list in the client, lifecycle and platform-service area reads the
// contract's page shape; a body that breaks it is BadRequestError (400,
// BAD_REQUEST, SDK) naming the field, as in Python, never a raw TypeError.
const LISTS: readonly (readonly [string, string, Lister])[] = [
  ['sandboxes', 'sandboxes', (client) => client.v2.list()],
  ['images', 'images', (client) => client.v2.images.list()],
  ['snapshots', 'snapshots', (client) => client.v2.snapshots.list()],
  ['volumes', 'volumes', (client) => client.v2.volumes.list()],
  ['secrets', 'secrets', (client) => client.v2.secrets.list()],
  ['reservations', 'reservations', (client) => client.v2.reservations.list()],
  ['Mac models', 'mac_models', (client) => client.v2.macModels.list()],
  ['preview endpoints', 'ports', (_client, api) => createSandbox('sbx', createHttpClient(api.url, 'key')).ports.listEndpoints()],
]

async function refusal(body: unknown, list: Lister): Promise<unknown> {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    return await list(client, api).then(() => undefined, (error: unknown) => error)
  } finally {
    await client.close()
    await api.close()
  }
}

describe.each(LISTS)('the %s list', (_name, field, list) => {
  it.each([
    ['a body without the array', { items: [], nextCursor: null }, `missing the ${field} array`],
    ['a non-array field', { [field]: {} }, `missing the ${field} array`],
    ['a non-object entry', { [field]: [3] }, `${field} entry that is not an object`],
    ['a non-string cursor', { [field]: [], next_cursor: 5 }, 'next_cursor that is not a string'],
    ['a non-object body', [], 'not a JSON object'],
  ])('refuses %s with BadRequestError', async (_case, body, message) => {
    const error = await refusal(body, list)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(message) })
  })

  it('reads an empty page', async () => {
    const api = await startFakeApi([{ status: 200, body: { [field]: [], next_cursor: null } }])
    const client = clientAt(api.url)
    try {
      const result = await list(client, api)
      expect(Array.isArray(result) ? result : (result as { readonly items: unknown[] }).items).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it.each([{ items: [] }, { ports: ['3000'] }, { ports: [3000.5] }])('refuses a port list body %j with BadRequestError', async (body) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    const sandbox = createSandbox({ sandboxId: 'sbx', sandboxClass: 'linux' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })
    await expect(sandbox.ports.list()).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'port list response is missing the ports array of integers',
    })
  } finally {
    await api.close()
  }
})
