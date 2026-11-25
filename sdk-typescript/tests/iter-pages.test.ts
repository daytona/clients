/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  BadRequestError,
  ClientTimeoutError,
  ConnectionError,
  DaytonaError,
  InternalServerError,
  LimitExceededError,
  RateLimitError,
  ServiceUnavailableError,
} from '../src/v2/index'
import { createDaytonaError } from '../src/v2/errors/DaytonaError'
import { iterPages, pageRetryTimer } from '../src/v2/pages'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { ListResult } from '../src/v2/types'
import type { PlannedResponse } from './networkPolicyTestServer'

// Every iter() reads a failed page again after a transient failure, at most
// three reads, and the error that ends the walk carries the cursor of the page
// that failed, so the walk resumes there with no item repeated or skipped.
// Python's twin is tests/test_iter_pages.py; Go's is page_retry_test.go.

const sandboxItem = (id: string) => ({ id, class: 'linux', name: id, status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: 'r1', port_map: [] })
const page = (ids: readonly string[], nextCursor: string | null = null): PlannedResponse => ({
  status: 200,
  body: { sandboxes: ids.map(sandboxItem), next_cursor: nextCursor, total: 0, total_relation: 'eq' },
})
const gateway = (status = 502): PlannedResponse => ({ status, body: '<html>bad gateway</html>', headers: { 'content-type': 'text/html' } })
const cursorOf = (path: string) => new URL(path, 'http://x').searchParams.get('cursor')

let sleeps: number[]
beforeEach(() => {
  sleeps = []
  jest.spyOn(pageRetryTimer, 'sleep').mockImplementation(async (ms) => {
    sleeps.push(ms)
  })
})
afterEach(() => jest.restoreAllMocks())

async function drain<T>(items: AsyncIterable<T>, into: T[] = []): Promise<T[]> {
  for await (const item of items) into.push(item)
  return into
}

it('reads a transiently failed page again and completes the walk', async () => {
  const api = await startFakeApi([page(['a'], 'p2'), gateway(502), gateway(503), page(['b'])])
  const client = clientAt(api.url)
  try {
    const ids = (await drain(client.v2.iter())).map((sandbox) => sandbox.id)
    expect(ids).toEqual(['a', 'b'])
    expect(api.requests.map((request) => cursorOf(request.path))).toEqual([null, 'p2', 'p2', 'p2'])
    expect(sleeps).toHaveLength(2)
    expect(sleeps[0]).toBeGreaterThanOrEqual(400)
    expect(sleeps[0]).toBeLessThanOrEqual(500)
    expect(sleeps[1]).toBeGreaterThanOrEqual(800)
    expect(sleeps[1]).toBeLessThanOrEqual(1000)
  } finally {
    await client.close()
    await api.close()
  }
})

it('ends the walk after three reads of a failing page, with that page cursor', async () => {
  const api = await startFakeApi([page(['a', 'b'], 'p2'), gateway(), gateway(), gateway()])
  const client = clientAt(api.url)
  try {
    const error = await drain(client.v2.iter()).then(
      () => undefined,
      (caught: unknown) => caught,
    )
    expect(error).toBeInstanceOf(InternalServerError)
    expect(error).toMatchObject({ statusCode: 502, pageCursor: 'p2' })
    expect(api.requests.map((request) => cursorOf(request.path))).toEqual([null, 'p2', 'p2', 'p2'])
  } finally {
    await client.close()
    await api.close()
  }
})

it('resumes a broken walk with no item repeated or skipped', async () => {
  const api = await startFakeApi([page(['a', 'b'], 'p2'), gateway(504), gateway(504), gateway(504), page(['c'], 'p3'), page(['d'])])
  const client = clientAt(api.url)
  try {
    const seen: string[] = []
    let failure: unknown
    try {
      for await (const sandbox of client.v2.iter()) seen.push(sandbox.id)
    } catch (caught) {
      failure = caught
    }
    expect(failure).toBeInstanceOf(DaytonaError)
    const pageCursor = (failure as DaytonaError).pageCursor
    expect(pageCursor).toBe('p2')
    for await (const sandbox of client.v2.iter({ cursor: pageCursor })) seen.push(sandbox.id)
    expect(seen).toEqual(['a', 'b', 'c', 'd'])
  } finally {
    await client.close()
    await api.close()
  }
})

it('a first-page failure carries no cursor and is not retried when retrying cannot help', async () => {
  const api = await startFakeApi([{ status: 400, body: { code: 'BAD_REQUEST', message: 'bad', source: 'DAYTONA_API' } }])
  const client = clientAt(api.url)
  try {
    const error = await drain(client.v2.iter()).then(() => undefined, (caught: unknown) => caught)
    expect(error).toBeInstanceOf(BadRequestError)
    expect((error as DaytonaError).pageCursor).toBeUndefined()
    expect('pageCursor' in (error as object)).toBe(true)
    expect(sleeps).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

function failingThen<T>(results: readonly (Error | ListResult<T>)[]) {
  const queue = [...results]
  const calls: (string | undefined)[] = []
  return {
    calls,
    fetch: async (cursor: string | undefined): Promise<ListResult<T>> => {
      calls.push(cursor)
      const next = queue.shift()
      if (next === undefined) throw new Error('unexpected read')
      if (next instanceof Error) throw next
      return next
    },
  }
}

it.each([
  ['a 400', () => createDaytonaError('bad', 400, 'BAD_REQUEST', 'DAYTONA_API')],
  ['a 500', () => createDaytonaError('boom', 500, 'INTERNAL_SERVER_ERROR', 'DAYTONA_API')],
  ['the client timeout', () => new ClientTimeoutError()],
  ['an organization limit', () => new LimitExceededError('limit', { statusCode: 429, code: 'LIMIT_EXCEEDED', source: 'DAYTONA_API' })],
  ['a retry hint over 10 s', () => new RateLimitError('slow down', { statusCode: 429, code: 'RATE_LIMITED', retryAfterSeconds: 11 })],
])('raises %s at once, with the page cursor', async (_name, build) => {
  const error = build()
  const { calls, fetch } = failingThen<string>([error])
  await expect(drain(iterPages(fetch, 'c1'))).rejects.toBe(error)
  expect(error.pageCursor).toBe('c1')
  expect(calls).toEqual(['c1'])
  expect(sleeps).toEqual([])
})

it.each([
  ['a 429 hint in seconds', () => new RateLimitError('slow down', { statusCode: 429, retryAfterSeconds: 3 }), 3000],
  ['a 503 hint in milliseconds', () => new ServiceUnavailableError('busy', { statusCode: 503, retryAfterMs: 2500 }), 2500],
  ['a dropped connection', () => new ConnectionError('reset'), undefined],
] as const)('waits at least the server hint after %s', async (_name, build, delay) => {
  const error = build()
  const { fetch } = failingThen<string>([error, { items: ['x'] }])
  expect(await drain(iterPages(fetch, undefined))).toEqual(['x'])
  expect(sleeps).toHaveLength(1)
  if (delay !== undefined) expect(sleeps[0]).toBe(delay)
  expect(error.pageCursor).toBeUndefined()
})

it('still ends the walk on a cursor cycle', async () => {
  const pages = new Map<string | undefined, ListResult<number>>([
    [undefined, { items: [1], nextCursor: 'a' }],
    ['a', { items: [2], nextCursor: 'a' }],
  ])
  expect(await drain(iterPages(async (cursor) => pages.get(cursor) ?? { items: [] }, undefined))).toEqual([1, 2])
})

it('leaves errors outside a walk without a page cursor', () => {
  expect(createDaytonaError('x', 404, 'NOT_FOUND', 'DAYTONA_API').pageCursor).toBeUndefined()
})
