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
import { runPollLoop, targetPredicate } from '../src/v2/events'
import { HttpTransport, createHttpClient } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'
import { READ_RETRY_MIN_DELAY_MS, ReadRetry, isRetryableReadError, retryAfterMs } from '../src/v2/readRetry'
import { imageWire, snapshotWire } from './artifactFixtures'
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
    // 0.2 + 0.4 s of back-off, then a read cut by the deadline.
    expect(record.times.length).toBeGreaterThanOrEqual(3)
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
    const failure = createDaytonaError('bad gateway', 502)
    const predicate = targetPredicate(new Set(['started']), new Set(['error']))
    let calls = 0
    const outcome = await runPollLoop('sbx', predicate, {
      deadline: Date.now() + 5_000,
      fn: async () => { calls += 1; if (calls === 1) throw failure; return { state: 'started', rawState: 'running' } },
      retry: new ReadRetry(),
    })
    expect(outcome.state).toBe('started')
    expect(calls).toBe(2)
    const timedOut = await caught(runPollLoop('sbx', predicate, { deadline: Date.now() + 300, fn: async () => { throw failure }, retry: new ReadRetry() }))
    expect(timedOut).toBeInstanceOf(RequestTimeoutError)
    expect((timedOut as RequestTimeoutError).cause).toBe(failure)
    const refused = createDaytonaError('no', 401, 'UNAUTHORIZED', 'DAYTONA_API')
    await expect(runPollLoop('sbx', predicate, { deadline: Date.now() + 5_000, fn: async () => { throw refused }, retry: new ReadRetry() })).rejects.toBe(refused)
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
