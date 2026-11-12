/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, DaytonaError, SearchUnavailableError } from '../src/v2/index'
import { SearchProbe, searchNotServed, searchOrList } from '../src/v2/searchFirst'
import { createDaytonaError } from '../src/v2/errors/DaytonaError'
import { imageWire, snapshotWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { PlannedResponse } from './networkPolicyTestServer'

// Lists read the search route first and the plain list route only where the
// deployment does not serve search. Python's twin is tests/test_search_first_lists.py.

const sandboxItem = { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: 'r1', port_map: [] }

type Case = {
  readonly name: string
  readonly searchPath: string
  readonly listPath: string
  readonly field: string
  readonly item: unknown
  readonly call: (client: Daytona) => Promise<{ readonly items: readonly { readonly id: string }[]; readonly nextCursor?: string }>
  readonly query: string
}

const CASES: readonly Case[] = [
  { name: 'sandboxes', searchPath: '/sandboxes/search', listPath: '/sandboxes', field: 'sandboxes', item: sandboxItem, call: (client) => client.v2.list({ limit: 5, cursor: 'c1' }), query: '?cursor=c1&limit=5' },
  { name: 'images', searchPath: '/images/search', listPath: '/images', field: 'images', item: imageWire({ id: 'img_1' }), call: (client) => client.v2.images.list({ name: 'base', limit: 5 }), query: '?limit=5&name=base' },
  { name: 'snapshots', searchPath: '/snapshots/search', listPath: '/snapshots', field: 'snapshots', item: snapshotWire({ id: 'snap_1' }), call: (client) => client.v2.snapshots.list({ sandboxId: 'sbx', limit: 5 }), query: '?limit=5&sandbox_id=sbx' },
]

const errorBody = (code: string) => ({ code, message: code.toLowerCase(), source: 'DAYTONA_API' })

async function run(responses: PlannedResponse[], call: Case['call']) {
  const api = await startFakeApi(responses)
  const client = clientAt(api.url)
  try {
    type Page = Awaited<ReturnType<Case['call']>>
    let page: Page | undefined
    let error: unknown
    try {
      page = await call(client)
    } catch (caught) {
      error = caught
    }
    return { page, error, paths: api.requests.map((request) => request.path) }
  } finally {
    await client.close()
    await api.close()
  }
}

describe.each(CASES)('$name list', ({ searchPath, listPath, field, item, call, query }) => {
  const page = (cursor: string | null) => ({ [field]: [item], next_cursor: cursor })

  it('reads the search route on a deployment with search', async () => {
    const result = await run([{ status: 200, body: { ...page('n1'), total: 1, total_relation: 'eq' } }], call)
    expect(result.paths).toEqual([`${searchPath}${query}`])
    expect(result.page?.items).toHaveLength(1)
    expect(result.page?.nextCursor).toBe('n1')
  })

  it('falls back to the list route, with the same query, on 501 SEARCH_NOT_CONFIGURED', async () => {
    const result = await run([{ status: 501, body: errorBody('SEARCH_NOT_CONFIGURED') }, { status: 200, body: page(null) }], call)
    expect(result.paths).toEqual([`${searchPath}${query}`, `${listPath}${query}`])
    expect(result.page?.items).toHaveLength(1)
    expect(result.page?.nextCursor).toBeUndefined()
  })

  it('falls back to the list route on 404 from an API without the search route', async () => {
    const result = await run([{ status: 404, body: errorBody('NOT_FOUND') }, { status: 200, body: page(null) }], call)
    expect(result.paths).toEqual([`${searchPath}${query}`, `${listPath}${query}`])
    expect(result.page?.items).toHaveLength(1)
  })

  it('raises 503 SEARCH_UNAVAILABLE without falling back', async () => {
    const result = await run([{ status: 503, body: errorBody('SEARCH_UNAVAILABLE') }], call)
    expect(result.paths).toEqual([`${searchPath}${query}`])
    expect(result.error).toBeInstanceOf(SearchUnavailableError)
    expect(result.error).toMatchObject({ statusCode: 503, code: 'SEARCH_UNAVAILABLE' })
  })

  it.each([[501, 'SEARCH_NOT_CONFIGURED'], [404, 'NOT_FOUND']])('remembers a deployment without search (%i): later calls go straight to the list route', async (status, code) => {
    const result = await run(
      [{ status, body: errorBody(code) }, { status: 200, body: page(null) }, { status: 200, body: page(null) }],
      async (client) => {
        await call(client)
        return call(client)
      },
    )
    expect(result.paths).toEqual([`${searchPath}${query}`, `${listPath}${query}`, `${listPath}${query}`])
    expect(result.page?.items).toHaveLength(1)
  })

  it('does not remember a 503: the next call reads search again', async () => {
    const result = await run(
      [{ status: 503, body: errorBody('SEARCH_UNAVAILABLE') }, { status: 200, body: { ...page(null), total: 1, total_relation: 'eq' } }],
      async (client) => {
        await expect(call(client)).rejects.toBeInstanceOf(SearchUnavailableError)
        return call(client)
      },
    )
    expect(result.paths).toEqual([`${searchPath}${query}`, `${searchPath}${query}`])
    expect(result.page?.items).toHaveLength(1)
  })

  it('raises a 501 with another code without falling back', async () => {
    const result = await run([{ status: 501, body: errorBody('NOT_IMPLEMENTED') }], call)
    expect(result.paths).toEqual([`${searchPath}${query}`])
    expect(result.error).toMatchObject({ statusCode: 501, code: 'NOT_IMPLEMENTED' })
  })
})

describe('an iter() walk on a deployment without search', () => {
  it('probes search once, then reads every page from the list route', async () => {
    const result = await run(
      [
        { status: 501, body: errorBody('SEARCH_NOT_CONFIGURED') },
        { status: 200, body: { images: [imageWire({ id: 'img_1' })], next_cursor: 'p2' } },
        { status: 200, body: { images: [imageWire({ id: 'img_2' })], next_cursor: null } },
      ],
      async (client) => {
        const items: { id: string }[] = []
        for await (const image of client.v2.images.iter({ limit: 1 })) items.push(image)
        return { items }
      },
    )
    expect(result.page?.items.map((item) => item.id)).toEqual(['img_1', 'img_2'])
    expect(result.paths).toEqual(['/images/search?limit=1', '/images?limit=1', '/images?cursor=p2&limit=1'])
  })
})

describe("'search' is not a name", () => {
  it.each([['images', 'image'], ['snapshots', 'snapshot']] as const)('%s.get(\'search\') throws before any request', async (service, kind) => {
    const result = await run([], async (client) => ({ items: [await client.v2[service].get('search')] }))
    expect(result.paths).toEqual([])
    expect(result.error).toBeInstanceOf(BadRequestError)
    expect(result.error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect((result.error as Error).message).toBe(`'search' cannot be looked up by name; it is reserved for the search route — use the ${kind} ID`)
  })
})

describe('name lookups read the item route', () => {
  it('images.get(name) reads GET /images/{name}; 404 is absent', async () => {
    const found = await run([{ status: 200, body: imageWire({ id: 'img_1', name: 'base' }) }], async (client) => ({ items: [await client.v2.images.get('base')] }))
    expect(found.paths).toEqual(['/images/base'])
    expect(found.page?.items[0]?.id).toBe('img_1')
    const absent = await run([{ status: 404, body: errorBody('IMAGE_NOT_FOUND') }], async (client) => ({ items: [await client.v2.images.get('missing')] }))
    expect(absent.paths).toEqual(['/images/missing'])
    expect(absent.error).toMatchObject({ statusCode: 404 })
  })

  it('snapshots.get(name) reads GET /snapshots/{name}; 404 is absent', async () => {
    const found = await run([{ status: 200, body: snapshotWire({ id: 'snap_1', name: 'after-setup' }) }], async (client) => ({ items: [await client.v2.snapshots.get('after-setup')] }))
    expect(found.paths).toEqual(['/snapshots/after-setup'])
    expect(found.page?.items[0]?.id).toBe('snap_1')
    const absent = await run([{ status: 404, body: errorBody('SNAPSHOT_NOT_FOUND') }], async (client) => ({ items: [await client.v2.snapshots.get('missing')] }))
    expect(absent.error).toMatchObject({ statusCode: 404 })
  })
})

describe('searchNotServed', () => {
  it('is true only for 501 SEARCH_NOT_CONFIGURED and 404', () => {
    expect(searchNotServed(new DaytonaError('x', { statusCode: 501, code: 'SEARCH_NOT_CONFIGURED' }))).toBe(true)
    expect(searchNotServed(new DaytonaError('x', { statusCode: 404, code: 'NOT_FOUND' }))).toBe(true)
    expect(searchNotServed(new DaytonaError('x', { statusCode: 503, code: 'SEARCH_UNAVAILABLE' }))).toBe(false)
    expect(searchNotServed(new DaytonaError('x', { statusCode: 410, code: 'GONE' }))).toBe(false)
    expect(searchNotServed(new Error('plain'))).toBe(false)
  })
})

describe('a remembered search-off deployment that switches to search', () => {
  it('a remembered fallback answering 410 GONE forgets the probe and retries search once', async () => {
    const calls: string[] = []
    const searchAnswers: Array<() => Promise<string>> = [
      () => Promise.reject(createDaytonaError('x', 501, 'SEARCH_NOT_CONFIGURED')),
      () => Promise.resolve('s1'),
      () => Promise.resolve('s2'),
    ]
    const listAnswers: Array<() => Promise<string>> = [
      () => Promise.resolve('l1'),
      () => Promise.reject(createDaytonaError('x', 410, 'GONE')),
    ]
    const search = () => {
      calls.push('search')
      return searchAnswers.shift()!()
    }
    const list = () => {
      calls.push('list')
      return listAnswers.shift()!()
    }
    const probe = new SearchProbe()
    expect(await searchOrList(search, list, probe)).toBe('l1')
    expect(probe.notServed).toBe(true)
    expect(await searchOrList(search, list, probe)).toBe('s1')
    expect(probe.notServed).toBe(false)
    expect(await searchOrList(search, list, probe)).toBe('s2')
    expect(calls).toEqual(['search', 'list', 'list', 'search', 'search'])
  })

  it('a 410 from the list right after a search miss in the same call is thrown', async () => {
    const calls: string[] = []
    const search = () => {
      calls.push('search')
      return Promise.reject(createDaytonaError('x', 501, 'SEARCH_NOT_CONFIGURED'))
    }
    const list = () => {
      calls.push('list')
      return Promise.reject(createDaytonaError('x', 410, 'GONE'))
    }
    await expect(searchOrList(search, list, new SearchProbe())).rejects.toMatchObject({ statusCode: 410 })
    expect(calls).toEqual(['search', 'list'])
  })
})
