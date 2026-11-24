/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Build-log follower HTTP errors: decoded before the retry decision, kept as
// the final error or its cause.

import { Daytona } from '../src/v2/Daytona.js'
import { BuildLogInterruptedError, DaytonaError, LimitExceededError, RateLimitError, ServiceUnavailableError } from '../src/v2/index.js'
import { PERMANENT_BUILD_LOG_CODES } from '../src/v2/BuildLogs.js'
import { API_ERROR_CODES, CODE_STATUS } from '../src/v2/codes.js'

const DISCOVERY = '/images/image/build-logs-url'
const STREAM = '/build-logs/images/image'
const DONE = 'event: done\ndata: {"status":"ready","reason":"terminal","output_complete":true}\n\n'

function errorResponse(status: number, code: string, message: string, retryAfter?: string): Response {
  const headers = new Headers({ 'content-type': 'application/json' })
  if (retryAfter !== undefined) headers.set('retry-after', retryAfter)
  return new Response(JSON.stringify({ code, message, source: 'DAYTONA_API', trace_id: `trace-${code.toLowerCase()}` }), { status, headers })
}

function setup(discoveries: Response[], streams: Response[] = []) {
  const paths: string[] = []
  jest.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = new URL(String(input))
    paths.push(url.pathname)
    if (url.pathname === DISCOVERY) {
      return discoveries.shift() ?? new Response(JSON.stringify({ url: `https://logs.example${STREAM}` }), { headers: { 'content-type': 'application/json' } })
    }
    return streams.shift() ?? new Response(DONE, { headers: { 'content-type': 'text/event-stream' } })
  })
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = 'https://api.example'
  try {
    return { client: new Daytona({ apiKey: 'test-key' }), paths }
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}

async function advance<T>(pending: Promise<T>): Promise<T> {
  const observed = pending.then(value => ({ value }), error => ({ error: error as unknown }))
  await jest.runAllTimersAsync()
  const result = await observed
  if ('error' in result) throw result.error
  return result.value
}

async function failure(pending: Promise<unknown>): Promise<DaytonaError> {
  try { await advance(pending) } catch (error) {
    if (error instanceof DaytonaError) return error
    throw error
  }
  throw new Error('expected a failure')
}

beforeEach(() => { jest.useFakeTimers(); jest.spyOn(Math, 'random').mockReturnValue(1) })
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

describe('build-log follower HTTP errors', () => {
  it('retries a transient discovery 503, then succeeds', async () => {
    const { client, paths } = setup([errorResponse(503, 'SERVICE_UNAVAILABLE', 'build-log resolver busy')])
    const start = performance.now()
    await expect(advance(client.images.followBuildLogs('image', { onLogs: () => {} }))).resolves.toMatchObject({ status: 'ready' })
    expect(paths).toEqual([DISCOVERY, DISCOVERY, STREAM])
    expect(performance.now() - start).toBe(250)
  })

  it.each(['TOO_MANY_REQUESTS', 'RATE_LIMITED'])('honours Retry-After on a discovery 429 %s, then succeeds', async code => {
    const { client, paths } = setup([errorResponse(429, code, 'slow down', '3')])
    const start = performance.now()
    await expect(advance(client.images.followBuildLogs('image', { onLogs: () => {} }))).resolves.toMatchObject({ status: 'ready' })
    expect(paths).toEqual([DISCOVERY, DISCOVERY, STREAM])
    expect(performance.now() - start).toBe(3000)
  })

  it('keeps the server typed error as the cause when discovery retries end', async () => {
    const { client, paths } = setup(Array.from({ length: 6 }, () => errorResponse(503, 'SERVICE_UNAVAILABLE', 'build-log discovery is not configured')))
    const error = await failure(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(error).toBeInstanceOf(BuildLogInterruptedError)
    expect((error as BuildLogInterruptedError).message).toContain('reconnect budget')
    const cause = (error as BuildLogInterruptedError).cause
    expect(cause).toBeInstanceOf(ServiceUnavailableError)
    expect(cause).toMatchObject({ statusCode: 503, code: 'SERVICE_UNAVAILABLE', message: 'build-log discovery is not configured', traceId: 'trace-service_unavailable' })
    expect(paths).toEqual(Array(6).fill(DISCOVERY))
  })

  it('keeps the server typed error as the cause when stream retries end', async () => {
    const { client, paths } = setup([], Array.from({ length: 6 }, () => errorResponse(502, 'BAD_GATEWAY', 'upstream reset')))
    const error = await failure(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(error).toBeInstanceOf(BuildLogInterruptedError)
    const cause = (error as BuildLogInterruptedError).cause
    expect(cause).toBeInstanceOf(DaytonaError)
    expect(cause).toMatchObject({ statusCode: 502, code: 'BAD_GATEWAY', message: 'upstream reset', traceId: 'trace-bad_gateway' })
    expect(paths).toEqual([DISCOVERY, ...Array<string>(6).fill(STREAM)])
  })

  it.each([
    [429, 'LIMIT_EXCEEDED', LimitExceededError],
    [501, 'WORKLOAD_PLANE_ELSEWHERE', DaytonaError],
  ] as const)('raises a permanent discovery %s %s at once, unwrapped', async (status, code, type) => {
    const { client, paths } = setup([errorResponse(status, code, 'not here')])
    const start = performance.now()
    const error = await failure(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(error).toBeInstanceOf(type)
    expect(error).not.toBeInstanceOf(BuildLogInterruptedError)
    expect(error).toMatchObject({ statusCode: status, code, message: 'not here' })
    expect(paths).toEqual([DISCOVERY])
    expect(performance.now() - start).toBe(0)
  })

  it('interrupts on a permanent stream failure without retrying', async () => {
    const { client, paths } = setup([], [errorResponse(429, 'LIMIT_EXCEEDED', 'quota reached')])
    const error = await failure(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(error).toBeInstanceOf(BuildLogInterruptedError)
    expect(error).toMatchObject({ statusCode: 429, code: 'LIMIT_EXCEEDED' })
    expect((error as BuildLogInterruptedError).cause).toBeInstanceOf(RateLimitError)
    expect(paths).toEqual([DISCOVERY, STREAM])
  })

  it('still retries a 5xx whose body carries no code', async () => {
    const { client, paths } = setup([new Response('<html>bad gateway</html>', { status: 502, headers: { 'content-type': 'text/html' } })])
    await expect(advance(client.images.followBuildLogs('image', { onLogs: () => {} }))).resolves.toMatchObject({ status: 'ready' })
    expect(paths).toEqual([DISCOVERY, DISCOVERY, STREAM])
  })

  it('lists only catalog codes of retryable statuses as permanent', () => {
    for (const code of PERMANENT_BUILD_LOG_CODES) {
      expect(API_ERROR_CODES).toContain(code)
      const status = (CODE_STATUS as Readonly<Record<string, number>>)[code]
      expect(status === 429 || status >= 500).toBe(true)
    }
  })
})
