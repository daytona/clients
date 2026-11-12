/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { once } from 'node:events'
import { createServer } from 'node:http'
import { WaitHub } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ClientTimeoutError, NotFoundError, RequestTimeoutError } from '../src/v2/errors/DaytonaError'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

describe('sandbox wait budgets', () => {
  afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

  it('bounds a stalled HTTP long poll by a fifty-millisecond wait deadline', async () => {
    // Given a real HTTP server that accepts the request but never responds.
    let requestUrl: string | undefined
    const server = createServer((request) => { requestUrl = request.url })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    try {
      const address = server.address()
      if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
      const sandbox = createSandbox('sbx', createHttpClient(`http://127.0.0.1:${address.port}`))
      // When the deadline expires while the network read is still pending.
      const started = performance.now()
      const error = await sandbox.wait({ timeoutSeconds: 0.05 }).catch((failure: unknown) => failure)
      const elapsedMs = performance.now() - started
      // Then read slack cannot extend the budget or replace the documented error.
      expect({ error, withinBudget: elapsedMs < 5000 }).toMatchObject({
        error: { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' }, withinBudget: true,
      })
      expect(error).toBeInstanceOf(RequestTimeoutError)
      expect(elapsedMs).toBeGreaterThanOrEqual(25)
      expect(new URL(requestUrl ?? '', 'http://localhost').searchParams.get('wait')).toBe('running')
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
    }
  }, 10_000)

  it('bounds a stalled delete long poll, held with wait_exact, by its wait deadline', async () => {
    // Given a server that accepts the DELETE and holds every read after it.
    // The Python twin is test_a_stalled_long_poll_ends_at_the_wait_deadline_async[delete].
    const held: string[] = []
    const server = createServer((request, response) => {
      if (request.method === 'DELETE') {
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end('{}')
        return
      }
      held.push(request.url ?? '')
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    try {
      const address = server.address()
      if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
      const sandbox = createSandbox('sbx', createHttpClient(`http://127.0.0.1:${address.port}`))
      const started = performance.now()
      const error = await sandbox.delete({ timeoutSeconds: 0.05 }).catch((failure: unknown) => failure)
      const elapsedMs = performance.now() - started
      expect(error).toBeInstanceOf(RequestTimeoutError)
      expect(error).toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      expect(elapsedMs).toBeGreaterThanOrEqual(25)
      expect(elapsedMs).toBeLessThan(5000)
      // The deadline cut the held long poll for `deleted`, held exactly.
      expect(held).toHaveLength(1)
      const query = new URL(held[0] ?? '', 'http://localhost').searchParams
      expect([query.get('wait_exact'), query.get('wait')]).toEqual(['deleted', null])
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
    }
  }, 10_000)

  it('bounds each finite slice by what is left of the wait budget', async () => {
    // Given a wait that sees a non-target state first, then the target.
    jest.useFakeTimers({ now: 0 })
    const api = createHttpClient('http://api.invalid')
    const detail = { id: 'sbx', class: 'linux', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
    const get = jest.spyOn(api, 'get')
      .mockImplementationOnce(async () => { jest.setSystemTime(10_000); return { data: { ...detail, status: 'pending' } } })
      .mockImplementationOnce(async () => ({ data: { ...detail, status: 'running' } }))
    const sandbox = createSandbox('sbx', api)
    // When the first held read takes ten seconds of a 31 s budget.
    const waiting = sandbox.wait({ timeoutSeconds: 31 })
    await jest.advanceTimersByTimeAsync(1_000)
    await waiting
    // Then each read asks for the smaller of the slice and what is left, its
    // read bounded by that hold plus slack and never past the deadline.
    const requests = get.mock.calls.map(([, config]) => config as { readonly timeout: number; readonly params: { readonly timeout_ms: number } })
    expect(requests).toHaveLength(2)
    expect(requests[0]).toMatchObject({ timeout: 31_000, params: { wait: 'running', timeout_ms: 30_000 } })
    const second = requests[1] as { readonly timeout: number; readonly params: { readonly timeout_ms: number } }
    expect(second.params.timeout_ms).toBeLessThanOrEqual(21_000)
    expect(second.params.timeout_ms).toBeGreaterThan(19_000)
    expect(second.timeout).toBeLessThanOrEqual(21_000)
  })

  it.each([
    { timeoutSeconds: 0.05, wireTimeout: 50, requestTimeout: 50 },
    { timeoutSeconds: 31, wireTimeout: 30_000, requestTimeout: 31_000 },
    { timeoutSeconds: 60, wireTimeout: 30_000, requestTimeout: 35_000 },
    { timeoutSeconds: 0, wireTimeout: 30_000, requestTimeout: 35_000 },
  ])('preserves wire timeout and available read slack with budget $timeoutSeconds', async ({ timeoutSeconds, wireTimeout, requestTimeout }) => {
    // Given a deterministic clock and an immediately completed long poll.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox('sbx', api)
    // When a wait draws a read from the available budget.
    await sandbox.wait({ timeoutSeconds })
    // Then only the HTTP budget is capped; the server still gets its slice.
    expect(get).toHaveBeenCalledWith('/sandboxes/sbx', {
      timeout: requestTimeout, params: { wait: 'running', timeout_ms: wireTimeout },
    })
  })

  it.each([
    { timeoutSeconds: 60, failedAt: 35_000 },
    { timeoutSeconds: 0.05, failedAt: 1 },
  ])('preserves a client timer failure before the wait deadline at $failedAt ms', async ({ timeoutSeconds, failedAt }) => {
    // Given either a slice timeout or an earlier client timer, not the wait deadline.
    jest.useFakeTimers({ now: 0 })
    const api = createHttpClient('http://api.invalid')
    const failure = new ClientTimeoutError('request timer expired')
    jest.spyOn(api, 'get').mockImplementation(async () => {
      jest.setSystemTime(failedAt)
      throw failure
    })
    const sandbox = createSandbox('sbx', api)
    // When the network read fails independently of the caller's deadline.
    const result = sandbox.wait({ timeoutSeconds })
    // Then the original request failure remains actionable.
    await expect(result).rejects.toBe(failure)
  })

  it('preserves an API HTTP 408 during a bounded wait', async () => {
    // Given a server that returns a real request-timeout response.
    const api = await startFakeApi([{ status: 408, body: { message: 'server timed out', code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API' } }])
    const sandbox = createSandbox('sbx', createHttpClient(api.url))
    try {
      // When the bounded read receives the API failure.
      const result = sandbox.wait({ timeoutSeconds: 1 })
      // Then it is not relabeled as a local wait expiration.
      await expect(result).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API' })
    } finally {
      await api.close()
    }
  })

  it.each(['stop', 'delete', 'pause', 'resume', 'start'] as const)('bounds %s waiting in fractional seconds', async (operation) => {
    // Given an accepted operation that remains in progress.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
    jest.spyOn(api, 'delete').mockResolvedValue({ data: {} })
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', class: 'linux', status: 'booting', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox('sbx', api)
    // When a quarter-second budget elapses.
    const result = expect(sandbox[operation]({ timeoutSeconds: 0.25 })).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    await jest.advanceTimersByTimeAsync(250)
    // Then the lifecycle operation reports the typed wait timeout.
    await result
  })

  it('rounds a positive sub-millisecond wait up at the wire boundary', async () => {
    // Given a deterministic clock and an immediately ready response.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox('sbx', api)
    // When waiting for half a millisecond.
    const result = await sandbox.wait({ timeoutSeconds: 0.0005 })
    // Then it never sends the wire's unbounded zero timeout.
    expect(result).toBe(sandbox)
    expect(get).toHaveBeenCalledWith('/sandboxes/sbx', { timeout: 1, params: { wait: 'running', timeout_ms: 1 } })
  })

  it('uses a sixty-second wait deadline by default', async () => {
    // Given an API that continues to report a starting sandbox.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', class: 'linux', status: 'booting', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox('sbx', api)
    let finished = false
    const result = sandbox.wait().catch((error: unknown) => error).finally(() => { finished = true })
    // When the default deadline is reached.
    await jest.advanceTimersByTimeAsync(59_999)
    expect(finished).toBe(false)
    await jest.advanceTimersByTimeAsync(1)
    // Then the wait expires at sixty seconds, not sixty milliseconds.
    expect(await result).toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
  })

  it.each([-1, NaN, Infinity])('rejects invalid timeoutSeconds=%s before reading metadata', async (timeoutSeconds) => {
    // Given an invalid wait budget and an untouched transport.
    const api = createHttpClient('http://api.invalid')
    const get = jest.spyOn(api, 'get')
    const sandbox = createSandbox('sbx', api)
    // When the public wait validates the budget.
    const result = sandbox.wait({ timeoutSeconds })
    // Then the error is a typed SDK validation failure.
    await expect(result).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect(get).not.toHaveBeenCalled()
  })

  it('keeps timeoutSeconds=0 unbounded while polling in finite slices', async () => {
    // Given a sandbox that becomes ready after a long caller wait.
    jest.useFakeTimers()
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(api, 'get').mockImplementation(async () => {
      jest.setSystemTime(Date.now() + 120_000)
      return { data: { id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }
    })
    const sandbox = createSandbox('sbx', api)
    // When waiting without a deadline.
    const result = await sandbox.wait({ timeoutSeconds: 0 })
    // Then it completes successfully despite spending longer than the default budget.
    expect(result.state).toBe('started')
  })

  it('treats a 404 from the event hub fallback as completed deletion', async () => {
    // Given an event-backed wait whose fallback read finds a deleted sandbox.
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(WaitHub.prototype, 'enabled', 'get').mockReturnValue(true)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unexpected websocket') }, api)
    jest.spyOn(api, 'delete').mockResolvedValue({ data: {} })
    jest.spyOn(api, 'get').mockRejectedValue(new NotFoundError('gone'))
    jest.spyOn(hub, 'wait').mockImplementation(async (_id, _predicate, options) => {
      if (!options?.sandboxPollFn) throw new Error('missing sandbox fallback')
      return options.sandboxPollFn()
    })
    const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
    // When deletion waits through the event-backed path.
    await sandbox.delete()
    // Then the same 404 completion rule updates the observed state.
    expect(sandbox.state).toBe('destroyed')
  })

  it('refreshes full metadata after an event-backed wait resolves', async () => {
    // Given a socket outcome carrying state only.
    const api = createHttpClient('http://api.invalid')
    jest.spyOn(WaitHub.prototype, 'enabled', 'get').mockReturnValue(true)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unexpected websocket') }, api)
    jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', class: 'linux', status: 'running', memory_mib: 513, name: 'sbx', cpu: 1, cores_max: 1, disk_mib: 1024, region_id: '', port_map: [] } })
    const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
    // When the socket completes the wait.
    await sandbox.wait()
    // Then the returned handle has a complete fresh observation too.
    expect(sandbox.memory).toBe(513 / 1024)
  })

  it.each(['stop', 'delete', 'start', 'pause', 'resume'] as const)('rejects old or invalid %s wait options before sending a request', async (operation) => {
    // Given a legacy timeout key and a transport that must remain untouched.
    const api = createHttpClient('http://api.invalid')
    const request = jest.spyOn(api, 'request')
    const sandbox = createSandbox('sbx', api)
    const options = { timeoutSeconds: 1, timeout: 1000 }
    // When the legacy key is supplied at runtime.
    const result = sandbox[operation](options)
    // Then the SDK returns a typed validation error.
    await expect(result).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect(request).not.toHaveBeenCalled()
  })
})
