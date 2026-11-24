/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Region-filtered name lookup, label and name Unicode checks, volume
// get-or-create under a racing create, and the current API key projection.
// The Python twin is tests/test_service_correctness.py.

import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { createHttpClient } from '../src/v2/http'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { FakeApi, PlannedResponse } from './networkPolicyTestServer'

const EMPTY_PAGE = { sandboxes: [], total: 0, total_relation: 'eq', next_cursor: null }
const DETAIL = { id: 'sbx', class: 'linux', name: 'n', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'eu', labels: { name: 'n' }, toolbox_proxy_url: 'http://toolbox', created_at: '2026-01-01T00:00:00Z' }
const VOLUME = { id: 'vol', name: 'shared', type: 'disk', status: 'ready', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
const MISSING: PlannedResponse = { status: 404, body: { code: 'NOT_FOUND', message: 'volume not found', source: 'DAYTONA_API' } }
const NAME_TAKEN: PlannedResponse = { status: 409, body: { code: 'CONFLICT', message: 'a volume with that name already exists', source: 'DAYTONA_API' } }
const API_KEY = {
  id: 'k', name: 'ci', project_id: 'p', project_permissions: [], created_at: '2026-01-01T00:00:00Z',
  context: { project_name: 'p', org_id: 'org-1', org_name: 'o' },
}
const LIMIT = { limit: null, usage: 0 }
const LIMITS = {
  sandboxes: { concurrent: { limit: 100, usage: 3 }, total: LIMIT }, image_builds: { concurrent: LIMIT },
  storage: { volumes: LIMIT, images: LIMIT, snapshots: LIMIT }, secrets_per_project: LIMIT,
  api_keys_per_project: LIMIT, api_keys_per_organization: LIMIT,
}

async function withApi(responses: PlannedResponse[], run: (client: Daytona, api: FakeApi) => Promise<void>): Promise<void> {
  const api = await startFakeApi(responses)
  const client = clientAt(api.url)
  try {
    await run(client, api)
  } finally {
    await client.close()
    await api.close()
  }
}

function query(api: FakeApi, index: number): URLSearchParams {
  return new URL(api.requests[index]!.path, 'http://x').searchParams
}

describe('findByName with a region', () => {
  it('filters the search by the region on every page', async () => {
    const first = { sandboxes: [{ ...DETAIL, id: 'a' }], total: 2, total_relation: 'eq', next_cursor: 'c1' }
    const second = { sandboxes: [{ ...DETAIL, id: 'b' }], total: 2, total_relation: 'eq', next_cursor: null }
    await withApi([{ status: 200, body: first }, { status: 200, body: second }], async (client, api) => {
      const found = await client.v2.findByName('n', { region: 'eu' })

      expect(found.map((sandbox) => sandbox.id)).toEqual(['a', 'b'])
      expect([query(api, 0).get('region_ids'), query(api, 1).get('region_ids')]).toEqual(['eu', 'eu'])
      expect(query(api, 0).get('labels')).toBe(JSON.stringify({ name: 'n' }))
      expect(query(api, 1).get('cursor')).toBe('c1')
      expect(api.requests[0]!.headers['x-daytona-region']).toBe('eu')
    })
  })

  it.each([undefined, '', '  ', 'local', ' LOCAL '])('searches every region with region %j', async (region) => {
    await withApi([{ status: 200, body: EMPTY_PAGE }], async (client, api) => {
      expect(await client.v2.findByName('n', region === undefined ? {} : { region })).toEqual([])
      expect(query(api, 0).has('region_ids')).toBe(false)
      expect(api.requests[0]!.headers['x-daytona-region']).toBeUndefined()
    })
  })

  it('refuses a region that names several', async () => {
    await withApi([], async (client, api) => {
      await expect(client.v2.findByName('n', { region: 'eu,us' })).rejects.toMatchObject({
        statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'region entries must be non-empty strings without commas',
      })
      expect(api.requests).toEqual([])
    })
  })
})

describe('labels and names hold no lone surrogate', () => {
  const source = (api: FakeApi) => createSandbox({ id: 'src', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))
  type Call = (client: Daytona, api: FakeApi) => Promise<unknown>
  const CASES: readonly (readonly [string, Call, string])[] = [
    ['search label value', (client) => client.v2.search({ labels: { k: '\ud800' } }), 'label values must not contain a lone surrogate'],
    ['search label key', (client) => client.v2.search({ labels: { '\udfff': 'v' } }), 'label keys must not contain a lone surrogate'],
    ['findByName name', (client) => client.v2.findByName('a\ud800'), 'name must not contain a lone surrogate'],
    ['create name', (client) => client.v2.create('img', { name: 'a\ud800', waitSeconds: 0 }), 'name must not contain a lone surrogate'],
    ['create label value', (client) => client.v2.create('img', { labels: { k: '\ud800' }, waitSeconds: 0 }), 'label values must not contain a lone surrogate'],
    ['create label key', (client) => client.v2.create('img', { labels: { '\ud800': 'v' }, waitSeconds: 0 }), 'label keys must not contain a lone surrogate'],
    ['setLabels value', (_client, api) => source(api).setLabels({ name: 'n', k: '\ud800' }), 'label values must not contain a lone surrogate'],
    ['fork name', (_client, api) => source(api).fork({ name: '\ud800', waitSeconds: 0 }), 'fork name must not contain a lone surrogate'],
  ]

  it.each(CASES)('refuses %s before any request', async (_case, call, message) => {
    await withApi([], async (client, api) => {
      await expect(call(client, api)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
      expect(api.requests).toEqual([])
    })
  })

  it('reports an empty label key before a surrogate', async () => {
    await withApi([], async (client) => {
      await expect(client.v2.search({ labels: { k: '\ud800', '': 'v' } })).rejects.toMatchObject({ message: 'label keys must not be empty' })
    })
  })

  it('sends astral characters', async () => {
    await withApi([{ status: 200, body: EMPTY_PAGE }, { status: 200, body: EMPTY_PAGE }], async (client, api) => {
      await client.v2.search({ labels: { k: '\u{1f600}' } })
      await client.v2.findByName('\u{1f600}')
      expect(JSON.parse(query(api, 0).get('labels')!)).toEqual({ k: '\u{1f600}' })
      expect(JSON.parse(query(api, 1).get('labels')!)).toEqual({ name: '\u{1f600}' })
    })
  })
})

describe('volumes.get with create under a racing create', () => {
  it('reads the volume a racing create made', async () => {
    await withApi([MISSING, NAME_TAKEN, { status: 200, body: VOLUME }], async (client, api) => {
      expect((await client.v2.volumes.get('shared', { create: true })).id).toBe('vol')
      expect(api.requests.map((request) => `${request.method} ${request.path}`)).toEqual(['GET /volumes/shared', 'POST /volumes', 'GET /volumes/shared'])
    })
  })

  it('reads again until the volume is readable', async () => {
    await withApi([MISSING, NAME_TAKEN, MISSING, MISSING, { status: 200, body: VOLUME }], async (client, api) => {
      expect((await client.v2.volumes.get('shared', { create: true })).id).toBe('vol')
      expect(api.requests).toHaveLength(5)
    })
  })

  it('throws the conflict when the volume never becomes readable', async () => {
    await withApi([MISSING, NAME_TAKEN, MISSING, MISSING, MISSING, MISSING, MISSING], async (client, api) => {
      await expect(client.v2.volumes.get('shared', { create: true })).rejects.toMatchObject({ statusCode: 409, code: 'CONFLICT' })
      expect(api.requests.map((request) => request.method)).toEqual(['GET', 'POST', 'GET', 'GET', 'GET', 'GET', 'GET'])
    })
  })

  it('throws any other conflict unchanged', async () => {
    const other: PlannedResponse = { status: 409, body: { code: 'REGION_NOT_EMPTY', message: 'region not empty', source: 'DAYTONA_API' } }
    await withApi([MISSING, other], async (client, api) => {
      await expect(client.v2.volumes.get('shared', { create: true })).rejects.toMatchObject({ statusCode: 409, code: 'REGION_NOT_EMPTY' })
      expect(api.requests).toHaveLength(2)
    })
  })

  it('throws a failed read after the conflict', async () => {
    const denied: PlannedResponse = { status: 403, body: { code: 'FORBIDDEN', message: 'forbidden', source: 'DAYTONA_API' } }
    await withApi([MISSING, NAME_TAKEN, denied], async (client) => {
      await expect(client.v2.volumes.get('shared', { create: true })).rejects.toMatchObject({ statusCode: 403, code: 'FORBIDDEN' })
    })
  })

  it('returns the one volume to every concurrent caller', async () => {
    // Every caller reads before any create lands: the first create wins and
    // every later one is the name conflict.
    const callers = 6
    const volumes = new Map<string, unknown>()
    let readers = 0
    let creates = 0
    let releaseCreates!: () => void
    const allRead = new Promise<void>((resolve) => { releaseCreates = resolve })
    const server = createServer(async (request, response) => {
      const chunks: Buffer[] = []
      for await (const chunk of request) chunks.push(chunk as Buffer)
      response.setHeader('content-type', 'application/json')
      if (request.method === 'GET') {
        const name = decodeURIComponent((request.url ?? '').split('?')[0]!.replace('/volumes/', ''))
        if (volumes.has(name)) {
          response.end(JSON.stringify(volumes.get(name)))
          return
        }
        if (++readers >= callers) releaseCreates()
        response.statusCode = 404
        response.end(JSON.stringify(MISSING.body))
        return
      }
      await allRead
      creates++
      const { name } = JSON.parse(Buffer.concat(chunks).toString('utf8')) as { name: string }
      if (volumes.has(name)) {
        response.statusCode = 409
        response.end(JSON.stringify(NAME_TAKEN.body))
        return
      }
      volumes.set(name, { ...VOLUME, name })
      response.statusCode = 201
      response.end(JSON.stringify(volumes.get(name)))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const client = clientAt(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)
    try {
      const results = await Promise.all(Array.from({ length: callers }, () => client.v2.volumes.get('shared', { create: true })))
      expect(results.map((volume) => volume.id)).toEqual(Array(callers).fill('vol'))
      expect(creates).toBe(callers)
      expect([...volumes.keys()]).toEqual(['shared'])
    } finally {
      await client.close()
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
    }
  })
})

describe('the current API key ignores the key-sync flag', () => {
  it.each([['absent', {}], ['null', { sync_to_v0: null }], ['a string', { sync_to_v0: 'yes' }]] as const)('reads a key whose sync_to_v0 is %s', async (_case, extra) => {
    await withApi([{ status: 200, body: { ...API_KEY, ...extra } }], async (client) => {
      expect((await client.v2.currentApiKey()).context.orgId).toBe('org-1')
    })
  })

  it('looks up the key organization for getLimits', async () => {
    await withApi([{ status: 200, body: API_KEY }, { status: 200, body: LIMITS }], async (client, api) => {
      expect((await client.v2.getLimits()).sandboxes.concurrent.limit).toBe(100)
      expect(api.requests.map((request) => request.path)).toEqual(['/api-keys/current', '/orgs/org-1/limits'])
    })
  })
})
