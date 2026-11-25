/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Retries of a wait's failed observation reads. Sandbox state waits, Docker
// readiness and image / snapshot readiness retry a read that fails
// transiently (no response, a stalled read, HTTP 429 or a 5xx other than 501)
// with back-off under the wait's own deadline, honouring the server's retry
// hint; a spent deadline throws the wait's timeout with the last failure as
// its cause, and any other failure is thrown at once. The Python twin is
// tests/test_wait_read_retries.py.

import { AxiosError } from 'axios'
import type { AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { DockerService } from '../src/v2/Docker'
import { ImagesService } from '../src/v2/Images'
import { SnapshotsService } from '../src/v2/Snapshots'
import {
  AuthenticationError,
  BadRequestError,
  ClientClosedError,
  ClientTimeoutError,
  ConnectionError,
  DaytonaError,
  DockerNotReadyError,
  DockerWaitTimeoutError,
  ForbiddenError,
  NotFoundError,
  RequestTimeoutError,
  SandboxTerminalStateError,
  ServiceUnavailableError,
  createDaytonaError,
} from '../src/v2/errors/DaytonaError'
import { WaitHub, runPollLoop, targetPredicate } from '../src/v2/events'
import type { WaitOutcome } from '../src/v2/events'
import * as httpModule from '../src/v2/http'
import { HttpTransport, createHttpClient } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'
import { READ_RETRY_MIN_DELAY_MS, ReadRetry, isRetryableReadError, retryAfterMs } from '../src/v2/readRetry'
import { imageWire, snapshotWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import { ImageBuilder } from '../src/v2/Image'
import { MAX_TIMER_DELAY_MS } from '../src/v2/options'
import { createSandbox } from './helpers/sandbox'

/** A scripted answer: a body (200), an HTTP failure, or a thrown error. */
type Step = { readonly body: unknown } | { readonly status: number; readonly body?: unknown; readonly headers?: Record<string, string> } | { readonly error: Error }

const TRANSIENT: ReadonlyArray<[string, Step]> = [
  ['502', { status: 502, body: '<html>bad gateway</html>', headers: { 'content-type': 'text/html' } }],
  ['503', { status: 503, body: { code: 'SERVICE_UNAVAILABLE', message: 'busy', source: 'DAYTONA_API' } }],
  ['504', { status: 504 }],
  ['500', { status: 500, body: { code: 'INTERNAL_SERVER_ERROR', message: 'boom', source: 'DAYTONA_API' } }],
  ['429', { status: 429, body: { code: 'RATE_LIMITED', message: 'slow down', source: 'DAYTONA_API' } }],
  ['connection', { error: new ConnectionError('connection reset') }],
  ['client timeout', { error: new ClientTimeoutError() }],
]

const PERMANENT: ReadonlyArray<[string, Step, new (...args: never[]) => Error]> = [
  ['400', { status: 400, body: { code: 'BAD_REQUEST', message: 'bad', source: 'DAYTONA_API' } }, BadRequestError],
  ['401', { status: 401, body: { code: 'UNAUTHORIZED', message: 'no', source: 'DAYTONA_API' } }, AuthenticationError],
  ['403', { status: 403, body: { code: 'FORBIDDEN', message: 'no', source: 'DAYTONA_API' } }, ForbiddenError],
  ['404', { status: 404, body: { code: 'NOT_FOUND', message: 'gone', source: 'DAYTONA_API' } }, NotFoundError],
  ['408', { status: 408, body: { code: 'REQUEST_TIMEOUT', message: 'server 408', source: 'DAYTONA_API' } }, RequestTimeoutError],
  ['501', { status: 501 }, DaytonaError],
  ['LIMIT_EXCEEDED', { status: 429, body: { code: 'LIMIT_EXCEEDED', message: 'limit', source: 'DAYTONA_API' } }, DaytonaError],
]

interface Scripted {
  readonly times: number[]
  readonly urls: string[]
}

/** Answers from `steps`; the last one repeats. */
function script(http: AxiosInstance, steps: Step[]): Scripted {
  const record: Scripted = { times: [], urls: [] }
  http.defaults.adapter = async (config: InternalAxiosRequestConfig): Promise<AxiosResponse> => {
    record.times.push(Date.now())
    record.urls.push(config.url ?? '')
    const step = (steps.length > 1 ? steps.shift() : steps[0])!
    if ('error' in step) throw step.error
    if ('status' in step) {
      const json = typeof step.body === 'object'
      const response = {
        config, status: step.status, statusText: String(step.status), data: step.body ?? '',
        headers: { 'content-type': json ? 'application/json' : 'text/plain', ...step.headers },
      }
      throw new AxiosError(`Request failed with status code ${step.status}`, AxiosError.ERR_BAD_RESPONSE, config, undefined, response)
    }
    return { config, status: 200, statusText: 'OK', headers: { 'content-type': 'application/json' }, data: step.body }
  }
  return record
}

/** A hub whose early probe fails once, then polls (or lets its share run out). */
function probingHub(thenTimeOut: boolean): WaitHub {
  return {
    enabled: true,
    wait: async (_id: string, _predicate: unknown, options: { readonly sandboxPollFn?: () => Promise<WaitOutcome> }) => {
      await options.sandboxPollFn!().catch(() => undefined)
      if (thenTimeOut) throw new RequestTimeoutError('hub share spent', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      return options.sandboxPollFn!()
    },
  } as unknown as WaitHub
}

const gaps = (record: Scripted): number[] => record.times.slice(1).map((time, index) => time - record.times[index]!)

const detail = (status: string, extra: Record<string, unknown> = {}) => ({
  id: 'sbx', class: 'linux', name: 'sbx', status, cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], ...extra,
})

function sandboxWith(steps: Step[]) {
  const http = createHttpClient('http://api.invalid', 'key')
  const record = script(http, steps)
  return { sandbox: createSandbox('sbx', http), record }
}

function dockerWith(steps: Step[]) {
  const transport = new HttpTransport('http://toolbox.invalid', 'key')
  const record = script(transport.http, steps)
  return { docker: new DockerService(INTERNAL, transport), record }
}

function imagesWith(steps: Step[]) {
  const http = createHttpClient('http://api.invalid', 'key')
  const record = script(http, steps)
  return { images: new ImagesService(INTERNAL, http), snapshots: new SnapshotsService(INTERNAL, http), record }
}

async function caught(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(() => undefined, (error: unknown) => error)
}

afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

describe('the read retry policy', () => {
  it.each([
    new ClientTimeoutError(),
    new ConnectionError('reset'),
    createDaytonaError('busy', 503, 'SERVICE_UNAVAILABLE', 'DAYTONA_API'),
    createDaytonaError('bad gateway', 502),
    createDaytonaError('boom', 500),
    createDaytonaError('slow down', 429, 'RATE_LIMITED', 'DAYTONA_API'),
  ])('retries %s', (error) => {
    expect(isRetryableReadError(error)).toBe(true)
  })

  it.each([
    createDaytonaError('bad', 400, 'BAD_REQUEST', 'DAYTONA_API'),
    createDaytonaError('no', 401, 'UNAUTHORIZED', 'DAYTONA_API'),
    createDaytonaError('no', 403, 'FORBIDDEN', 'DAYTONA_API'),
    createDaytonaError('gone', 404, 'NOT_FOUND', 'DAYTONA_API'),
    createDaytonaError('server 408', 408, 'REQUEST_TIMEOUT', 'DAYTONA_API'),
    createDaytonaError('nope', 501),
    createDaytonaError('limit', 429, 'LIMIT_EXCEEDED', 'DAYTONA_API'),
    new RequestTimeoutError('wait ran out', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' }),
    new TypeError('not an SDK error'),
    createDaytonaError('odd', 600),
  ])('does not retry %s', (error) => {
    expect(isRetryableReadError(error)).toBe(false)
  })

  it('doubles the back-off to five seconds, resets it on success and keeps the last failure', () => {
    jest.useFakeTimers({ now: 0 })
    const retry = new ReadRetry()
    const failure = createDaytonaError('busy', 503)
    const delays: number[] = []
    for (let i = 0; i < 7; i++) {
      expect(retry.note(failure)).toBe(true)
      delays.push((retry as unknown as { nextReadAt: number }).nextReadAt)
    }
    expect(delays).toEqual([200, 400, 800, 1600, 3200, 5000, 5000])
    retry.succeeded()
    retry.note(failure)
    expect((retry as unknown as { nextReadAt: number }).nextReadAt).toBe(READ_RETRY_MIN_DELAY_MS)
    expect(retry.lastError).toBe(failure)
  })

  it('reads the retry hint from the body first, then from the Retry-After header', async () => {
    const http = createHttpClient('http://api.invalid', 'key')
    script(http, [
      { status: 503, body: { code: 'OVERLOADED', message: 'busy', retryAfterMs: 1500, retry_after_seconds: 4 }, headers: { 'retry-after': '9' } },
      { status: 429, body: { code: 'RATE_LIMITED', message: 'busy', retry_after_seconds: 4 }, headers: { 'retry-after': '9' } },
      { status: 503, body: 'busy', headers: { 'retry-after': '9' } },
      { status: 503, body: 'busy', headers: { 'retry-after': new Date(Date.now() + 30_000).toUTCString() } },
      { status: 503, body: 'busy', headers: { 'retry-after': 'soon' } },
    ])
    const hints: number[] = []
    for (let i = 0; i < 5; i++) {
      const error = await caught(http.get('/x'))
      expect(error).toBeInstanceOf(DaytonaError)
      hints.push(retryAfterMs(error as DaytonaError))
    }
    expect(hints.slice(0, 3)).toEqual([1500, 4000, 9000])
    expect(hints[3]).toBeGreaterThan(25_000)
    expect(hints[3]).toBeLessThanOrEqual(30_000)
    expect(hints[4]).toBe(0)
  })
})

describe('sandbox waits', () => {
  it.each(TRANSIENT)('retry one %s read failure', async (_name, failure) => {
    const { sandbox, record } = sandboxWith([failure, { body: detail('running') }])
    await sandbox.wait({ until: 'started', timeoutSeconds: 20 })
    expect(sandbox.state).toBe('started')
    expect(record.times).toHaveLength(2)
    expect(gaps(record)[0]).toBeGreaterThanOrEqual(READ_RETRY_MIN_DELAY_MS - 5)
  })

  it.each(PERMANENT)('throw a %s read failure at once', async (_name, failure, ErrorClass) => {
    const { sandbox, record } = sandboxWith([failure, { body: detail('running') }])
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 20 }))
    expect(error).toBeInstanceOf(ErrorClass)
    expect(error).not.toMatchObject({ source: 'SDK', code: 'REQUEST_TIMEOUT', statusCode: 408, message: 'sandbox failed to reach the requested state before timeout' })
    expect(record.times).toHaveLength(1)
  })

  it('keep the terminal answer read after a retry', async () => {
    const { sandbox, record } = sandboxWith([{ status: 503 }, { body: detail('failed', { error: 'boom' }) }])
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 20 }))
    expect(error).toBeInstanceOf(SandboxTerminalStateError)
    expect(error).toMatchObject({ rawState: 'failed', reason: 'boom' })
    expect(record.times).toHaveLength(2)
  })

  it('honour the server retry hint', async () => {
    const { sandbox, record } = sandboxWith([
      { status: 429, body: { code: 'RATE_LIMITED', message: 'slow down', source: 'DAYTONA_API', retryAfterMs: 700 } },
      { body: detail('running') },
    ])
    await sandbox.wait({ until: 'started', timeoutSeconds: 20 })
    expect(gaps(record)[0]).toBeGreaterThanOrEqual(690)
  })

  it('end at the deadline when the retry hint is past it, with the failure as the cause', async () => {
    const { sandbox, record } = sandboxWith([{ status: 429, body: { code: 'RATE_LIMITED', message: 'slow down' }, headers: { 'retry-after': '60' } }])
    const started = Date.now()
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 1 }))
    expect(Date.now() - started).toBeGreaterThanOrEqual(900)
    expect(Date.now() - started).toBeLessThan(2_000)
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ source: 'SDK', code: 'REQUEST_TIMEOUT' })
    expect((error as RequestTimeoutError).cause).toMatchObject({ statusCode: 429 })
    expect(record.times).toHaveLength(1)
  })

  it('end in the wait timeout with the last failure as its cause when reads keep failing', async () => {
    const { sandbox, record } = sandboxWith([{ status: 503, body: { code: 'SERVICE_UNAVAILABLE', message: 'busy' } }])
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ source: 'SDK', code: 'REQUEST_TIMEOUT', message: 'sandbox failed to reach the requested state before timeout' })
    expect((error as RequestTimeoutError).cause).toBeInstanceOf(ServiceUnavailableError)
    // 0.2 + 0.4 s of back-off, then a read cut by the deadline (fewer on a slow host).
    expect(record.times.length).toBeGreaterThanOrEqual(2)
    expect(record.times.length).toBeLessThanOrEqual(4)
  })

  it('keep the last failure as the cause after a later successful read', async () => {
    const failure = new ConnectionError('reset')
    const { sandbox } = sandboxWith([{ error: failure }, { body: detail('booting') }])
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 0.6 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect((error as RequestTimeoutError).cause).toBe(failure)
  })

  it('retry in the events fallback poll loop', async () => {
    // The sandbox's poll function records each failure in the shared retry
    // and waits it out before its next read.
    const failure = createDaytonaError('bad gateway', 502)
    const predicate = targetPredicate(new Set(['started']), new Set(['error']))
    const polling = (retry: ReadRetry, steps: Array<Error | 'ok'>) => async () => {
      await retry.pause(undefined)
      const step = (steps.length > 1 ? steps.shift() : steps[0])!
      if (step !== 'ok') { retry.probeFailed(step); throw step }
      retry.succeeded()
      return { state: 'started', rawState: 'running' } as const
    }
    let retry = new ReadRetry()
    const started = Date.now()
    const outcome = await runPollLoop('sbx', predicate, { deadline: Date.now() + 5_000, fn: polling(retry, [failure, 'ok']), retry })
    expect(outcome.state).toBe('started')
    expect(Date.now() - started).toBeGreaterThanOrEqual(READ_RETRY_MIN_DELAY_MS - 5)
    retry = new ReadRetry()
    const timedOut = await caught(runPollLoop('sbx', predicate, { deadline: Date.now() + 300, fn: polling(retry, [failure]), retry }))
    expect(timedOut).toBeInstanceOf(RequestTimeoutError)
    expect((timedOut as RequestTimeoutError).cause).toBe(failure)
    const refused = createDaytonaError('no', 401, 'UNAUTHORIZED', 'DAYTONA_API')
    retry = new ReadRetry()
    await expect(runPollLoop('sbx', predicate, { deadline: Date.now() + 5_000, fn: polling(retry, [refused]), retry })).rejects.toBe(refused)
  })

  it('make no read at the deadline when a pause timer fires a little early', async () => {
    // Node can fire a timer about 1 ms before the clock reads its time.
    const realSleep = httpModule.sleep
    jest.spyOn(httpModule, 'sleep').mockImplementation((ms: number) => realSleep(Math.max(0, ms - 1)))
    const { sandbox, record } = sandboxWith([{ status: 429, body: { code: 'RATE_LIMITED', message: 'slow down' }, headers: { 'retry-after': '60' } }])
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 0.3 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(record.times).toHaveLength(1)
  })

  it('hold back the fallback read after a failed probe by its retry hint', async () => {
    const http = createHttpClient('http://api.invalid', 'key')
    const record = script(http, [
      { status: 429, body: { code: 'RATE_LIMITED', message: 'slow down', retryAfterMs: 600 } },
      { body: detail('running') },
    ])
    const hub = probingHub(false)
    const sandbox = createSandbox('sbx', http, undefined, 'key', undefined, {}, undefined, undefined, hub)
    await sandbox.wait({ until: 'started', timeoutSeconds: 20 })
    expect(sandbox.state).toBe('started')
    expect(gaps(record)[0]).toBeGreaterThanOrEqual(590)
  })

  it('keep a failed probe the hub waits through as the timeout cause', async () => {
    const http = createHttpClient('http://api.invalid', 'key')
    script(http, [{ status: 400, body: { code: 'BAD_REQUEST', message: 'bad', source: 'DAYTONA_API' } }, { body: detail('booting') }])
    const sandbox = createSandbox('sbx', http, undefined, 'key', undefined, {}, undefined, undefined, probingHub(true))
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ source: 'SDK' })
    expect((error as RequestTimeoutError).cause).toBeInstanceOf(BadRequestError)
  })
})

describe('Docker readiness', () => {
  const starting = { body: { state: 'starting', owner: 'daemon' } }
  const ready = { body: { state: 'ready', owner: 'daemon' } }

  it.each(TRANSIENT)('retries a %s status read', async (_name, failure) => {
    const { docker, record } = dockerWith([starting, failure, ready])
    await expect(docker.waitUntilReady({ timeoutSeconds: 20 })).resolves.toMatchObject({ state: 'ready' })
    expect(record.times).toHaveLength(3)
  })

  it('throws a permanent failure at once and keeps the terminal states', async () => {
    const missing = dockerWith([{ status: 404, body: { code: 'NOT_FOUND', message: 'no docker status', source: 'DAYTONA_DAEMON' } }])
    await expect(missing.docker.waitUntilReady({ timeoutSeconds: 20 })).rejects.toBeInstanceOf(NotFoundError)
    expect(missing.record.times).toHaveLength(1)
    const failed = dockerWith([{ status: 503 }, { body: { state: 'failed', owner: 'daemon' } }])
    await expect(failed.docker.waitUntilReady({ timeoutSeconds: 20 })).rejects.toBeInstanceOf(DockerNotReadyError)
  })

  it('ends in the Docker timeout with the last status and failure when reads keep failing', async () => {
    const { docker } = dockerWith([starting, { status: 503 }])
    const error = await caught(docker.waitUntilReady({ timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(DockerWaitTimeoutError)
    expect(error).toMatchObject({ status: { state: 'starting' } })
    expect((error as DockerWaitTimeoutError).cause).toMatchObject({ statusCode: 503 })
  })
})

describe('image and snapshot readiness', () => {
  it('retries an intermediate client timeout with most of the budget left', async () => {
    const { images, record } = imagesWith([{ error: new ClientTimeoutError() }, { body: imageWire({ status: 'ready' }) }])
    await expect(images.get('img-one', { wait: 'ready', timeoutSeconds: 600 })).resolves.toMatchObject({ status: 'ready' })
    expect(record.times).toHaveLength(2)
  })

  it.each(TRANSIENT)('retries a %s read', async (_name, failure) => {
    const { images, record } = imagesWith([{ body: imageWire({ status: 'building' }) }, failure, { body: imageWire({ status: 'ready' }) }])
    await expect(images.get('img-one', { wait: 'ready', timeoutSeconds: 600 })).resolves.toMatchObject({ status: 'ready' })
    expect(record.times).toHaveLength(3)
  })

  it('retries a snapshot read and returns its failure for the caller to check', async () => {
    const { snapshots, record } = imagesWith([{ status: 503 }, { body: snapshotWire({ status: 'failed' }) }])
    await expect(snapshots.get('snap-one', { wait: 'ready', timeoutSeconds: 60 })).resolves.toMatchObject({ status: 'failed' })
    expect(record.times).toHaveLength(2)
  })

  it('throws a permanent failure at once', async () => {
    const { images, record } = imagesWith([{ status: 404, body: { code: 'NOT_FOUND', message: 'no such image', source: 'DAYTONA_API' } }])
    await expect(images.get('img-one', { wait: 'ready', timeoutSeconds: 60 })).rejects.toBeInstanceOf(NotFoundError)
    expect(record.times).toHaveLength(1)
  })

  it('times out with the last failure as its cause when reads keep failing', async () => {
    const { images } = imagesWith([{ status: 503 }])
    const error = await caught(images.get('img-one', { wait: 'ready', timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ source: 'SDK', message: 'artifact did not become ready before timeout' })
    expect((error as RequestTimeoutError).cause).toMatchObject({ statusCode: 503 })
  })

  it('retries a transient readiness read during a pull', async () => {
    const { images, record } = imagesWith([
      { body: { id: 'img-one', status: 'pending' } },
      { body: imageWire({ status: 'pending' }) },
      { status: 502 },
      { body: imageWire({ status: 'ready' }) },
    ])
    await expect(images.pull('alpine', { timeoutSeconds: 60 })).resolves.toMatchObject({ status: 'ready' })
    expect(record.times).toHaveLength(4)
  })

  it('times out a pull whose readiness reads keep failing, with the failure as the cause', async () => {
    const { images } = imagesWith([{ body: { id: 'img-one', status: 'pending' } }, { body: imageWire({ status: 'pending' }) }, { status: 503 }])
    const error = await caught(images.pull('alpine', { timeoutSeconds: 1.5 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ message: 'image pull did not finish before timeout' })
    expect((error as RequestTimeoutError).cause).toMatchObject({ statusCode: 503 })
  })
})

describe('review follow-ups', () => {
  it.each([undefined, 0, 60])('createFromBuild retries a transient readiness read with buildTimeoutSeconds %s', async (buildTimeoutSeconds) => {
    const api = await startFakeApi([
      { status: 202, body: { id: 'img-one', status: 'pending' } },
      { status: 200, body: imageWire({ status: 'pending' }) },
      { status: 502, body: { code: 'BAD_GATEWAY', message: 'bad gateway' } },
      { status: 200, body: imageWire({ status: 'ready' }) },
      { status: 200, body: { ...detail('running'), wait_cap_seconds: 0 } },
    ])
    const client = clientAt(api.url)
    try {
      const sandbox = await client.v2.createFromBuild(ImageBuilder.fromRegistry('base'), { buildTimeoutSeconds, waitSeconds: 0 })
      expect(sandbox.id).toBe('sbx')
      expect(api.requests.map((request) => request.method)).toEqual(['POST', 'GET', 'GET', 'GET', 'POST'])
      // The default budget repeats holds without a deadline instead of one hold.
      expect(api.requests[3]!.path).toContain('timeout_ms=')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['retryAfterMs', 'retry_after_seconds'])('reads an unusable %s body hint as none', (field) => {
    // A JSON integer too large for a double reads as Infinity.
    const huge = createDaytonaError('busy', 503, undefined, undefined, field === 'retryAfterMs' ? { retryAfterMs: Infinity } : { retryAfterSeconds: 1e308 })
    expect(retryAfterMs(huge)).toBe(0)
    const negative = createDaytonaError('busy', 503, undefined, undefined, { retryAfterMs: -1, retryAfterSeconds: -5 })
    expect(retryAfterMs(negative)).toBe(0)
  })

  it.each(['after 2099', '2099-01-01', 'Sunday, 06-Nov-94 08:49:37 GMT', 'Fri, 1 Jan 2099 00:00:00 GMT'])('ignores a Retry-After that is not an HTTP date: %s', async (header) => {
    const http = createHttpClient('http://api.invalid', 'key')
    script(http, [{ status: 503, body: 'busy', headers: { 'retry-after': header } }])
    const error = await caught(http.get('/x'))
    expect(retryAfterMs(error as DaytonaError)).toBe(0)
  })

  it('keeps the Retry-After header of a body nested too deeply to read', async () => {
    const http = createHttpClient('http://api.invalid', 'key')
    let deep: unknown = 1
    for (let i = 0; i < 700; i++) deep = [deep]
    script(http, [{ status: 503, body: { nested: deep }, headers: { 'retry-after': '3' } }])
    const error = await caught(http.get('/x'))
    expect(error).toBeInstanceOf(DaytonaError)
    expect(retryAfterMs(error as DaytonaError)).toBe(3000)
  })

  it('keeps Docker readiness to its poll spacing after a failure', async () => {
    const { docker, record } = dockerWith([{ status: 429, body: { code: 'RATE_LIMITED', message: 'slow down' } }, { body: { state: 'ready', owner: 'daemon' } }])
    await docker.waitUntilReady({ timeoutSeconds: 20 })
    expect(gaps(record)[0]).toBeGreaterThanOrEqual(495)
  })

  it('retries the first lookup of a pull that waits, and not of one that does not', async () => {
    const waiting = imagesWith([{ body: { id: 'img-one', status: 'pending' } }, { error: new ConnectionError('reset') }, { body: imageWire({ status: 'ready' }) }])
    await expect(waiting.images.pull('alpine', { timeoutSeconds: 60 })).resolves.toMatchObject({ status: 'ready' })
    expect(waiting.record.times).toHaveLength(3)
    const failure = new ConnectionError('reset')
    const notWaiting = imagesWith([{ body: { id: 'img-one', status: 'pending' } }, { error: failure }, { body: imageWire({ status: 'ready' }) }])
    await expect(notWaiting.images.pull('alpine', { wait: false })).rejects.toBe(failure)
  })

  it('waits out a retry hint longer than one timer', async () => {
    jest.useFakeTimers({ now: 0 })
    const retry = new ReadRetry()
    retry.note(createDaytonaError('slow down', 429, 'RATE_LIMITED', 'DAYTONA_API', { retryAfterSeconds: 3_000_000 }))
    let done = false
    const pausing = retry.pause(undefined).then(() => { done = true })
    await jest.advanceTimersByTimeAsync(MAX_TIMER_DELAY_MS + 1)
    expect(done).toBe(false)
    await jest.advanceTimersByTimeAsync(3_000_000_000 - MAX_TIMER_DELAY_MS)
    await pausing
    expect(done).toBe(true)
  })
})

describe('events-hub probes', () => {
  /** A WebSocket that opens at once and lets the test inject frames. */
  class FrameSocket {
    readyState = 1
    onopen: ((ev: Event) => void) | null = null
    onmessage: ((ev: MessageEvent) => void) | null = null
    onerror: ((ev: Event) => void) | null = null
    onclose: ((ev: CloseEvent) => void) | null = null
    send(): void {}
    close(code = 1000): void {
      this.readyState = 3
      this.onclose?.({ code, reason: '', wasClean: true } as CloseEvent)
    }
    inject(frame: Record<string, unknown>): void {
      this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
    }
  }

  it('stop a probe pausing on a retry hint once a frame settles the wait', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    jest.spyOn(Math, 'random').mockReturnValue(0.5)
    const socket = new FrameSocket()
    const ticketApi = {
      post: jest.fn().mockResolvedValue({ data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' }, headers: {} }),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const hub = new WaitHub('https://api.test', 'key', { enabled: true, reconcileIntervalMs: 0 }, () => socket as unknown as WebSocket, ticketApi, { earlyProbeMs: 50, noFrameTimeoutMs: 60_000 })
    const http = createHttpClient('http://api.invalid', 'key')
    // The first probe is rate-limited for 3 s; later reads would answer.
    const record = script(http, [
      { status: 429, body: { code: 'RATE_LIMITED', message: 'slow down', retryAfterMs: 3_000 } },
      { body: detail('booting') },
    ])
    const sandbox = createSandbox('sbx', http, undefined, 'key', undefined, {}, undefined, undefined, hub)
    try {
      const waiting = sandbox.wait({ until: 'started', timeoutSeconds: 0 })
      await sleepMs(20)
      socket.inject({ type: 'snapshot', id: 'sbx', status: 'booting', version: 1, error: null })
      // The first probe fails; the next one pauses on the 3 s hint.
      await sleepMs(400)
      expect(record.times).toHaveLength(1)
      socket.inject({ type: 'event', id: 'sbx', status: 'running', version: 2, error: null })
      await waiting
      const afterWait = record.times.length
      // No read after the wait returned, though the hint has run out.
      await sleepMs(3_200)
      expect(record.times).toHaveLength(afterWait)
    } finally {
      await hub.detach()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  }, 15_000)
})

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

describe('client close during a retry pause', () => {
  const busy = { status: 503, body: { code: 'SERVICE_UNAVAILABLE', message: 'busy' }, headers: { 'retry-after': '8' } }

  it.each(['sandbox wait', 'image readiness'] as const)('ends an unbounded %s at once with ClientClosedError', async (kind) => {
    const api = await startFakeApi(kind === 'sandbox wait' ? [{ status: 200, body: detail('stopped') }, busy, busy] : [busy, busy])
    const client = clientAt(api.url)
    try {
      const waiting = kind === 'sandbox wait'
        ? (await client.v2.get('sbx')).wait({ until: 'started', timeoutSeconds: 0 })
        : client.v2.images.get('img-one', { wait: 'ready', timeoutSeconds: 3_000_000 })
      const outcome = caught(waiting)
      await sleepMs(300)
      const closedAt = Date.now()
      await client.close()
      // Then the 8 s retry hint does not hold the wait: it ends at its next read.
      expect(await outcome).toBeInstanceOf(ClientClosedError)
      expect(Date.now() - closedAt).toBeLessThan(2_000)
      expect(api.requests.filter((request) => request.method === 'GET').length).toBe(kind === 'sandbox wait' ? 2 : 1)
    } finally {
      await api.close()
    }
  })
})

describe('poll spacing after a failed readiness read', () => {
  const slowDown = { status: 429, body: { code: 'RATE_LIMITED', message: 'slow down' } }

  it('keeps an artifact wait and a pull to their one-second spacing', async () => {
    const artifact = imagesWith([{ body: imageWire({ status: 'building' }) }, slowDown, { body: imageWire({ status: 'ready' }) }])
    await artifact.images.get('img-one', { wait: 'ready', timeoutSeconds: 60 })
    expect(gaps(artifact.record)[1]).toBeGreaterThanOrEqual(995)
    const pull = imagesWith([{ body: { id: 'img-one', status: 'pending' } }, { body: imageWire({ status: 'pending' }) }, slowDown, { body: imageWire({ status: 'ready' }) }])
    await pull.images.pull('alpine', { timeoutSeconds: 60 })
    expect(gaps(pull.record)[2]).toBeGreaterThanOrEqual(995)
  })
})

describe('client close on a hub-backed wait', () => {
  it('ends the wait when a probe finds the client closed', async () => {
    const http = createHttpClient('http://api.invalid', 'key')
    const closed = new ClientClosedError()
    script(http, [{ error: closed }])
    const sandbox = createSandbox('sbx', http, undefined, 'key', undefined, {}, undefined, undefined, probingHub(true))
    // The reserve's read after the hub's share must not turn the close into a timeout.
    const error = await caught(sandbox.wait({ until: 'started', timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(ClientClosedError)
  })

  it('makes the hub reject a waiter whose probe finds the client closed', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const socket = { readyState: 1, onopen: null, onmessage: null, onerror: null, onclose: null, send: () => undefined, close: () => undefined }
    const ticketApi = {
      post: jest.fn().mockResolvedValue({ data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' }, headers: {} }),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const hub = new WaitHub('https://api.test', 'key', { enabled: true, reconcileIntervalMs: 0 }, () => socket as unknown as WebSocket, ticketApi, { earlyProbeMs: 50, noFrameTimeoutMs: 60_000 })
    try {
      const outcome = caught(hub.wait('sbx', targetPredicate(new Set(['started']), new Set(['error'])), {
        timeout: 0,
        sandboxPollFn: async () => { throw new ClientClosedError() },
      }))
      await expect(outcome).resolves.toBeInstanceOf(ClientClosedError)
    } finally {
      await hub.detach()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  }, 10_000)
})
