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
  const cases: ReadonlyArray<readonly [string, unknown, string]> = [
    ['a body without the array', { items: [], nextCursor: null }, `missing the ${field} array`],
    ['a non-array field', { [field]: {} }, `missing the ${field} array`],
    ['a non-object entry', { [field]: [3] }, `${field} entry that is not an object`],
    ['a non-string cursor', { [field]: [], next_cursor: 5 }, 'next_cursor that is not a string'],
    ['a non-object body', [], 'not a JSON object'],
  // The Mac model registry is not paged: a next_cursor there is an unknown
  // field, ignored (tests/malformed-responses.test.ts pins it).
  ]
  it.each(cases.filter(([name]) => !(field === 'mac_models' && name === 'a non-string cursor')))('refuses %s with BadRequestError', async (_case, body, message) => {
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

it.each([
  [{ items: [] }, 'port list response is missing its ports'],
  [{ ports: ['3000'] }, 'port list response has a ports[0] that is not a 32-bit integer'],
  [{ ports: [3000.5] }, 'port list response has a ports[0] that is not a 32-bit integer'],
  // The contract types a port as an int32 and declares no range: 1e20 is out, 70000 is in.
  [{ ports: [1e20] }, 'port list response has a ports[0] that is not a 32-bit integer'],
] as const)('refuses a port list body %j with BadRequestError', async (body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    const sandbox = createSandbox({ sandboxId: 'sbx', sandboxClass: 'linux' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })
    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await api.close()
  }
})

it('reads a port list of int32 values, with no range of its own', async () => {
  const api = await startFakeApi([{ status: 200, body: { ports: [3000, 70000] } }])
  try {
    const sandbox = createSandbox({ sandboxId: 'sbx', sandboxClass: 'linux' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })
    const page = await sandbox.ports.list()
    expect(page.items).toEqual([3000, 70000])
    expect(Object.isFrozen(page) && Object.isFrozen(page.items)).toBe(true)
  } finally {
    await api.close()
  }
})

describe('account responses', () => {
  const limits = {
    sandboxes: { concurrent: { limit: 1, usage: 0 }, total: { limit: null, usage: 0 } },
    image_builds: { concurrent: { limit: 1, usage: 0 } },
    storage: { volumes: { limit: 1, usage: 0 }, images: { limit: 1, usage: 0 }, snapshots: { limit: 1, usage: 0 } },
    secrets_per_project: { limit: 1, usage: 0 }, api_keys_per_project: { limit: 1, usage: 0 }, api_keys_per_organization: { limit: 1, usage: 0 },
  }
  it.each([
    ['audit actions', (client: Daytona) => client.v2.listAuditActions(), { items: [] }, 'missing its targets'],
    ['audit actions', (client: Daytona) => client.v2.listAuditActions(), { targets: [{ target_type: 'sandbox' }] }, 'missing its targets[0].actions'],
    ['API key', (client: Daytona) => client.v2.currentApiKey(), { id: 'k', name: 'n', project_id: 'p', created_at: '2026-01-01T00:00:00Z', project_permissions: [] }, 'missing its context'],
    ['project budget', (client: Daytona) => client.v2.getProjectBudget(), [], 'not a JSON object'],
    ['organization limits', (client: Daytona) => client.v2.getLimits({ organizationId: 'org' }), { ...limits, storage: { volumes: { limit: 1, usage: 0 } } }, 'missing its storage.images'],
    ['organization limits', (client: Daytona) => client.v2.getLimits({ organizationId: 'org' }), { ...limits, members: 3 }, 'has a members that is not an object'],
  ] as const)('refuses a malformed %s response with BadRequestError', async (_name, call, body, message) => {
    const error = await refusal(body, (client) => call(client))
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(message) })
  })

  it('reads well-formed organization limits', async () => {
    const api = await startFakeApi([{ status: 200, body: limits }])
    const client = clientAt(api.url)
    try {
      await expect(client.v2.getLimits({ organizationId: 'org' })).resolves.toMatchObject({ sandboxes: { total: { usage: 0 } } })
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it('ends an iteration on an empty next_cursor, as Python does', async () => {
  const volume = { id: 'vol', name: 'data', type: 'disk', status: 'ready', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
  const api = await startFakeApi([{ status: 200, body: { volumes: [volume], next_cursor: '' } }])
  const client = clientAt(api.url)
  try {
    const seen: string[] = []
    for await (const item of client.v2.volumes.iter()) seen.push(item.id)
    expect(seen).toEqual(['vol'])
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})
