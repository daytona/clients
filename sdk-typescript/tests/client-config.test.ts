/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Client configuration: option keys, the environment, the request timeout and
// its meaning. sdk-python/tests/test_client_config.py asserts the same behaviour.

import { INTERNAL } from '../src/v2/internal'
import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { Daytona } from '../src/index'
import { Daytona as DaytonaV2 } from '../src/v2/Daytona'
import { BadRequestError, AuthenticationError, ClientTimeoutError, ConnectionError, RequestTimeoutError, SandboxCreateRequestTimeoutError } from '../src/v2/index'
import { DEFAULT_REQUEST_TIMEOUT_MS, HttpTransport, createHttpClient, createPlatformTokenProvider } from '../src/v2/http'
import { RENAMED_OPTIONS, assertKnownOptions, optionKeys } from '../src/v2/options'
import { eventsHubConfig } from '../src/v2/events'
import { FileSystemService } from '../src/v2/FileSystem'
import { ImagesService } from '../src/v2/Images'
import { ImageBuilder } from '../src/v2/Image'

const ENV = ['DAYTONA_API_KEY', 'DAYTONA_JWT_TOKEN', 'DAYTONA_PROJECT_ID'] as const
const saved = Object.fromEntries(ENV.map((name) => [name, process.env[name]]))

// Every test starts from no ambient credentials: a developer's or CI's
// exported values must not change what a test observes.
beforeEach(() => {
  for (const name of ENV) delete process.env[name]
})

afterEach(() => {
  for (const name of ENV) {
    const value = saved[name]
    if (value === undefined) delete process.env[name]
    else process.env[name] = value
  }
})

function expectBadRequest(run: () => unknown, message: string | RegExp): void {
  let thrown: unknown
  try {
    run()
  } catch (error) {
    thrown = error
  }
  expect(thrown).toBeInstanceOf(BadRequestError)
  expect(thrown).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  expect((thrown as Error).message).toMatch(message)
}

describe('option keys', () => {
  it('rejects an unknown root config key, naming it', () => {
    expectBadRequest(() => new Daytona({ apiKey: 'k', aipKey: 'x' } as never), 'DaytonaConfig has no option `aipKey`')
  })

  it('names the replacement of a renamed root config key', () => {
    expectBadRequest(
      () => new Daytona({ apiKey: 'k', requestTimeout: 30_000 } as never),
      'DaytonaConfig: `requestTimeout` was renamed to `requestTimeoutSeconds` (seconds)',
    )
  })

  it.each([
    ['reconcileIntervalMs', 'reconcileIntervalSeconds'],
    ['idleCloseMs', 'idleCloseSeconds'],
  ])('names the replacement of the renamed events option %s', (old, renamed) => {
    expectBadRequest(
      () => new Daytona({ apiKey: 'k', events: { [old]: 1_000 } as never }),
      `SandboxEventsConfig: \`${old}\` was renamed to \`${renamed}\` (seconds)`,
    )
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', events: { [old]: 1_000 } as never }), `was renamed to \`${renamed}\``)
  })

  it('rejects an unknown v2 client config key', () => {
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', requestTimeout: 5 } as never), 'was renamed to `requestTimeoutSeconds`')
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', bogus: true } as never), 'DaytonaConfig has no option `bogus`')
  })

  it.each(['apiUrl', 'serverUrl', 'toolboxUrl'])('rejects %s as a BadRequestError with the custom-URL guidance', (field) => {
    expectBadRequest(() => new Daytona({ apiKey: 'k', [field]: 'https://self.example' } as never), `DaytonaConfig does not take '${field}': SDK 2.x targets Daytona Cloud`)
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', apiUrl: 'https://self.example' } as never), "does not take 'apiUrl'")
  })

  it('rejects an unknown key inherited through the prototype', () => {
    const config = Object.create({ requestTimeout: 5_000 }) as Record<string, unknown>
    config.apiKey = 'k'
    expectBadRequest(() => new Daytona(config as never), 'was renamed to `requestTimeoutSeconds`')
  })

  it('copies the nested events config, so later changes do not reach the client', () => {
    const events: { enabled?: boolean; idleCloseSeconds?: number } = { idleCloseSeconds: 5 }
    const daytona = new Daytona({ apiKey: 'k', events })
    events.idleCloseSeconds = 99
    events.enabled = false
    expect(daytona.v2['hubEventsConfig']).toEqual({ enabled: undefined, reconcileIntervalMs: undefined, idleCloseMs: 5_000 })
  })

  it('validates the events value it keeps, read once', () => {
    let reads = 0
    const config = { apiKey: 'k', get events() { reads += 1; return reads === 1 ? { idleCloseSeconds: 1 } : { bogus: true } } }
    const daytona = new Daytona(config as never)
    expect(reads).toBe(1)
    expect(daytona.v2['hubEventsConfig']).toMatchObject({ idleCloseMs: 1_000 })
    expectBadRequest(() => new Daytona({ apiKey: 'k', events: 'on' } as never), 'SandboxEventsConfig must be an options object, got a string')
  })

  it('reports a renamed key only where its replacement exists', () => {
    const keys = optionKeys<{ readonly timeout?: number; readonly other?: string }>()({ timeout: true, other: true })
    expect(() => assertKnownOptions('X', { timeout: 1 }, keys)).not.toThrow()
    const plain = optionKeys<{ readonly other?: string }>()({ other: true })
    expectBadRequest(() => assertKnownOptions('X', { timeout: 1 } as never, plain), 'X has no option `timeout`')
    const seconds = optionKeys<{ readonly timeoutSeconds?: number }>()({ timeoutSeconds: true })
    expectBadRequest(() => assertKnownOptions('X', { timeout: 1 } as never, seconds), 'X: `timeout` was renamed to `timeoutSeconds` (seconds)')
  })

  it.each([
    [true, 'a boolean'],
    ['name', 'a string'],
    [5, 'a number'],
    [null, 'null'],
    [['a'], 'an array'],
    [() => undefined, 'a function'],
  ])('refuses %p where an options object belongs (a positional argument from an older signature)', (value, given) => {
    const keys = optionKeys<{ readonly create?: boolean }>()({ create: true })
    expectBadRequest(() => assertKnownOptions('VolumeGetOptions', value as never, keys), `VolumeGetOptions must be an options object, got ${given}`)
    expect(() => assertKnownOptions('VolumeGetOptions', undefined, keys)).not.toThrow()
    // The root constructor reads a null config as none, like Python's `Daytona(None)`.
    if (value !== null) expectBadRequest(() => new Daytona(value as never), `DaytonaConfig must be an options object, got ${given}`)
  })

  it.each([
    [new Date(0), 'a built-in Date'],
    [new Map([['create', true]]), 'a built-in Map'],
    [new Set(['create']), 'a built-in Set'],
    [/create/, 'a built-in RegExp'],
    [Promise.resolve(), 'a built-in Promise'],
    [new Uint8Array(1), 'a built-in Uint8Array'],
    [new ArrayBuffer(1), 'a built-in ArrayBuffer'],
  ])('refuses a built-in %p, whose contents would read as no options', (value, given) => {
    const keys = optionKeys<{ readonly create?: boolean }>()({ create: true })
    expectBadRequest(() => assertKnownOptions('VolumeGetOptions', value as never, keys), `VolumeGetOptions must be an options object, got ${given}`)
    expectBadRequest(() => new Daytona(value as never), `DaytonaConfig must be an options object, got ${given}`)
  })

  it('accepts class instances and prototype-based options objects', () => {
    class Options { public create = true }
    const keys = optionKeys<{ readonly create?: boolean }>()({ create: true })
    expect(() => assertKnownOptions('VolumeGetOptions', new Options(), keys)).not.toThrow()
    expect(() => assertKnownOptions('VolumeGetOptions', Object.create({ create: true }) as never, keys)).not.toThrow()
    expect(() => assertKnownOptions('VolumeGetOptions', Object.create(null) as never, keys)).not.toThrow()
    expectBadRequest(() => assertKnownOptions('VolumeGetOptions', Object.create({ creat: true }) as never, keys), 'VolumeGetOptions has no option `creat`')
  })

  it('carries the rename hints the duration rename needs', () => {
    expect(RENAMED_OPTIONS).toEqual({
      timeout: 'timeoutSeconds',
      executionTimeout: 'executionTimeoutSeconds',
      escalateAfter: 'escalateAfterSeconds',
      requestTimeout: 'requestTimeoutSeconds',
      requestTimeoutMs: 'requestTimeoutSeconds',
      delay: 'delaySeconds',
      reconcileIntervalMs: 'reconcileIntervalSeconds',
      idleCloseMs: 'idleCloseSeconds',
    })
    expect(Object.isFrozen(RENAMED_OPTIONS)).toBe(true)
  })

  it('points the 0.x requestTimeoutMs at its replacement', () => {
    // The only 0.x configuration key with a 2.x replacement (the URL keys have
    // their own guidance; useDeprecatedPolling and _experimental have none).
    expectBadRequest(() => new Daytona({ apiKey: 'k', requestTimeoutMs: 5000 } as never), 'DaytonaConfig: `requestTimeoutMs` was renamed to `requestTimeoutSeconds` (seconds)')
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', requestTimeoutMs: 5000 } as never), '`requestTimeoutMs` was renamed to `requestTimeoutSeconds` (seconds)')
  })

  it('converts the events settings from seconds', () => {
    expect(eventsHubConfig({ enabled: false, reconcileIntervalSeconds: 2.5, idleCloseSeconds: 0 }))
      .toEqual({ enabled: false, reconcileIntervalMs: 2_500, idleCloseMs: 0 })
    expect(eventsHubConfig(undefined)).toEqual({ enabled: undefined, reconcileIntervalMs: undefined, idleCloseMs: undefined })
  })
})

describe('environment', () => {
  it('trims ambient credentials', () => {
    process.env.DAYTONA_API_KEY = '  dtn_key \n'
    expect(new DaytonaV2({})['apiKey']).toBe('dtn_key')
  })

  it.each(['', '   ', '\t\n'])('treats a blank ambient API key (%j) as unset', (blank) => {
    process.env.DAYTONA_API_KEY = blank
    delete process.env.DAYTONA_JWT_TOKEN
    expect(() => new DaytonaV2({})).toThrow(AuthenticationError)
  })

  it('does not let a blank ambient API key collide with a JWT', () => {
    process.env.DAYTONA_API_KEY = '  '
    process.env.DAYTONA_JWT_TOKEN = ' jwt-token '
    process.env.DAYTONA_PROJECT_ID = ' project-1 '
    const v2 = new DaytonaV2({})
    expect(v2['apiKey']).toBeUndefined()
    expect(v2['httpOptions'].authProvider).toBeDefined()
    expect(v2['hubIdentityKey']).toMatch(/^jwt:project-1:/)
  })

  it('treats a blank ambient project as unset', () => {
    delete process.env.DAYTONA_API_KEY
    process.env.DAYTONA_PROJECT_ID = '   '
    expectBadRequest(() => new DaytonaV2({ jwtToken: 'jwt-token' }), 'projectId is required')
  })

  it('rejects an explicitly empty projectId', () => {
    process.env.DAYTONA_PROJECT_ID = 'project-from-env'
    expectBadRequest(() => new DaytonaV2({ apiKey: 'k', projectId: '' }), 'projectId must be a non-empty string')
  })

  it('never reads DAYTONA_API_URL', () => {
    const previous = { v2: process.env.DAYTONA_V2_API_URL, v0sdk: process.env.DAYTONA_API_URL }
    delete process.env.DAYTONA_V2_API_URL
    process.env.DAYTONA_API_URL = 'http://v0-sdk.invalid'
    try {
      expect(new DaytonaV2({ apiKey: 'k' })['apiUrl']).toBe('https://api.daytona.com')
    } finally {
      if (previous.v2 === undefined) delete process.env.DAYTONA_V2_API_URL
      else process.env.DAYTONA_V2_API_URL = previous.v2
      if (previous.v0sdk === undefined) delete process.env.DAYTONA_API_URL
      else process.env.DAYTONA_API_URL = previous.v0sdk
    }
  })
})

describe('request timeout', () => {
  it('defaults to sixty seconds', () => {
    expect(DEFAULT_REQUEST_TIMEOUT_MS).toBe(60_000)
    const v2 = new DaytonaV2({ apiKey: 'k' })
    expect(v2['api'].defaults.timeout).toBe(60_000)
    expect(v2['apiTransport'].defaultTimeout).toBe(60_000)
    expect(new HttpTransport('http://toolbox.invalid').defaultTimeout).toBe(60_000)
  })

  it('takes seconds', () => {
    const v2 = new Daytona({ apiKey: 'k', requestTimeoutSeconds: 2.5 }).v2
    expect(v2['api'].defaults.timeout).toBe(2_500)
    expect(new Daytona({ apiKey: 'k', requestTimeoutSeconds: 0 }).v2['api'].defaults.timeout).toBe(0)
  })

  it.each([2_147_484, 30 * 24 * 3600, 1e300])('reads %p seconds (above the timer maximum) as no limit', (huge) => {
    expect(new Daytona({ apiKey: 'k', requestTimeoutSeconds: huge }).v2['api'].defaults.timeout).toBe(0)
    expect(new Daytona({ apiKey: 'k', requestTimeoutSeconds: 2_147_483 }).v2['api'].defaults.timeout).toBe(2_147_483_000)
  })

  it.each(['reconcileIntervalSeconds', 'idleCloseSeconds'])('rejects an invalid events.%s', (field) => {
    for (const bad of [-1, Number.POSITIVE_INFINITY, Number.NaN, '5']) {
      expectBadRequest(() => new Daytona({ apiKey: 'k', events: { [field]: bad } as never }).v2, `events.${field} must be a non-negative number of seconds`)
    }
  })

  it.each([-1, Number.POSITIVE_INFINITY, Number.NaN, '60'])('rejects %p', (bad) => {
    expectBadRequest(() => new Daytona({ apiKey: 'k', requestTimeoutSeconds: bad as number }).v2, 'requestTimeoutSeconds must be a non-negative number of seconds')
  })
})

describe('request timeout meaning', () => {
  let server: Server
  let base: string
  let handle: (request: IncomingMessage, response: ServerResponse) => void

  beforeEach(async () => {
    server = createServer((request, response) => handle(request, response))
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address()
    if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
    base = `http://127.0.0.1:${address.port}`
  })

  afterEach(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  /** Headers at once, then one byte every `every` ms, `chunks` times. */
  function trickle(chunks: number, every: number): (request: IncomingMessage, response: ServerResponse) => void {
    return (request, response) => {
      request.resume()
      response.writeHead(200, { 'Content-Type': 'application/octet-stream' })
      response.flushHeaders()
      let sent = 0
      const timer = setInterval(() => {
        if (response.destroyed) return clearInterval(timer)
        response.write('x')
        if (++sent === chunks) {
          clearInterval(timer)
          response.end()
        }
      }, every)
      response.on('close', () => clearInterval(timer))
    }
  }

  /** The whole response after `delay` ms. */
  function late(delay: number): (request: IncomingMessage, response: ServerResponse) => void {
    return (request, response) => {
      request.resume()
      const timer = setTimeout(() => {
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end('{"ok":true}')
      }, delay)
      response.on('close', () => clearTimeout(timer))
    }
  }

  it('bounds the whole ordinary request, body included', async () => {
    // Every byte arrives well inside the timeout, so a per-read (idle) timeout
    // never fires: the request timeout is the total time.
    handle = trickle(8, 50)
    const transport = new HttpTransport(base, 'k', 150)
    // The body finishes in 400 ms: only a total bound rejects.
    await expect(transport.get('/slow-body', { responseType: 'text' })).rejects.toBeInstanceOf(ClientTimeoutError)
  })

  it('bounds an ordinary request on the plain axios client too', async () => {
    handle = trickle(8, 50)
    await expect(createHttpClient(base, 'k', 150).get('/slow-body', { responseType: 'text' })).rejects.toBeInstanceOf(ClientTimeoutError)
  })

  it('is disabled by 0', async () => {
    handle = trickle(4, 50)
    await expect(new HttpTransport(base, 'k', 0).get('/slow-body', { responseType: 'text' })).resolves.toBe('xxxx')
  })

  it('does not cut a wait that carries its own budget', async () => {
    handle = late(300)
    const transport = new HttpTransport(base, 'k', 100)
    await expect(transport.get('/processes/p/wait', { timeout: 5_000 })).resolves.toEqual({ ok: true })
    await expect(transport.get('/hold', { timeout: 0 })).resolves.toEqual({ ok: true })
  })

  it('bounds a git pull / push by its own total deadline, whatever the request timeout', async () => {
    // The body trickles in well inside a per-read timeout; only a total
    // deadline ends it. timeoutSeconds replaces the 1 s request timeout.
    const { GitService } = await import('../src/v2/Git')
    // Shorter than the request timeout: with a 30 s request timeout and a 1 s
    // trickling body, only the 0.2 s per-call deadline can end the pull.
    handle = trickle(20, 50)
    await expect(new GitService(INTERNAL, new HttpTransport(base, 'k', 30_000)).pull('/repo', { timeoutSeconds: 0.2 }))
      .rejects.toBeInstanceOf(ClientTimeoutError)
    // Longer than the request timeout: the push answers after 1.3 s, past the
    // 0.5 s request timeout, and succeeds inside its own 3 s deadline.
    handle = late(1_300)
    await expect(new GitService(INTERNAL, new HttpTransport(base, 'k', 500)).push('/repo', { timeoutSeconds: 3 })).resolves.toBeUndefined()
  })

  it('treats a per-call timeout as a total bound that replaces the request timeout', async () => {
    // Every chunk arrives well inside 150 ms; the body takes 400 ms.
    handle = trickle(8, 50)
    const transport = new HttpTransport(base, 'k', 0)
    await expect(transport.get('/slow-body', { responseType: 'text', timeout: 150 })).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    // 0 and a value above the timer maximum mean no limit, as for the request timeout.
    await expect(transport.get('/slow-body', { responseType: 'text', timeout: 0 })).resolves.toBe('xxxxxxxx')
    await expect(transport.get('/slow-body', { responseType: 'text', timeout: 3_000_000_000 })).resolves.toBe('xxxxxxxx')
  })

  describe('a per-call timeout and credential resolution', () => {
    // The call's deadline (request or per-call timeout) starts before the
    // bearer is resolved; the token exchange request is also bounded by the
    // client's request timeout, since concurrent calls share it. Python's
    // test_client_config.py checks the same three cases.
    const exchangeThen = (exchangeMs: number, thingMs: number) => (request: IncomingMessage, response: ServerResponse): void => {
      request.resume()
      const exchange = request.url?.startsWith('/auth/token') === true
      const timer = setTimeout(() => {
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify(exchange ? { access_token: 't', expires_in: 3600 } : { ok: true }))
      }, exchange ? exchangeMs : thingMs)
      response.on('close', () => clearTimeout(timer))
    }
    const transport = (requestTimeoutMs: number): HttpTransport => new HttpTransport(base, undefined, requestTimeoutMs, {
      authProvider: createPlatformTokenProvider({ baseURL: base, jwtToken: 'jwt', projectId: 'p', requestTimeout: requestTimeoutMs }),
    })

    it('a shorter per-call timeout covers the exchange', async () => {
      handle = exchangeThen(400, 0)
      await expect(transport(5_000).get('/thing', { timeout: 100 })).rejects.toBeInstanceOf(ClientTimeoutError)
    })

    it('a longer per-call timeout does not lengthen the exchange past the request timeout', async () => {
      handle = exchangeThen(1_000, 0)
      await expect(transport(200).get('/thing', { timeout: 5_000 })).rejects.toBeInstanceOf(ClientTimeoutError)
    })

    it('a longer per-call timeout lets the request itself outlast the request timeout', async () => {
      handle = exchangeThen(50, 800)
      await expect(transport(500).get('/thing', { timeout: 5_000 })).resolves.toEqual({ ok: true })
    })
  })

  it('bounds image submissions by the request timeout', async () => {
    // POST /images is an ordinary request (no hold), as in Python.
    handle = late(400)
    const images = new ImagesService(createHttpClient(base, 'k', 100))
    await expect(images.build(ImageBuilder.fromDockerfile('FROM alpine'))).rejects.toBeInstanceOf(ClientTimeoutError)
    await expect(images.pull('alpine', { wait: false })).rejects.toBeInstanceOf(ClientTimeoutError)
    await expect(images.pull('alpine')).rejects.toBeInstanceOf(ClientTimeoutError)
  })

  it('does not cut file transfers that keep moving bytes', async () => {
    // 300 ms in all, a byte every 50 ms: past the 100 ms request timeout,
    // inside the transfer idle bound (tests/transfer-bounds.test.ts).
    const transport = new HttpTransport(base, 'k', 100)
    const fs = new FileSystemService(INTERNAL, transport)
    handle = trickle(6, 50)
    expect(new TextDecoder().decode(await fs.readBytes('/a'))).toBe('xxxxxx')
    await expect(fs.writeBytes('/a', new Uint8Array([1, 2, 3]))).resolves.toBeUndefined()
    const response = await transport.fetch('/files/download?path=%2Fa', { transfer: true })
    expect(await response.text()).toBe('xxxxxx')
    await expect(transport.fetch('/files/upload?path=%2Fa', { method: 'POST', body: 'data', transfer: true })).resolves.toMatchObject({ status: 200 })
  })

  it('gives the client timeout one shape on the axios and the fetch paths', async () => {
    const clientTimeout = { message: 'operation timed out', statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' }
    const transport = new HttpTransport(base, 'k', 100)
    handle = late(400)
    await expect(transport.get('/slow')).rejects.toMatchObject(clientTimeout)
    await expect(transport.fetch('/stalled')).rejects.toMatchObject(clientTimeout)
    handle = trickle(8, 50)
    await expect(transport.get('/slow-body', { responseType: 'text' })).rejects.toMatchObject(clientTimeout)
    expect(new ClientTimeoutError()).toMatchObject(clientTimeout)
    expect(new ConnectionError('refused')).toMatchObject({ source: 'SDK', statusCode: undefined, code: undefined })
  })

  it('turns a create POST that hits the request timeout into SandboxCreateRequestTimeoutError', async () => {
    const keys: (string | undefined)[] = []
    handle = (request, response) => {
      keys.push(request.headers['idempotency-key'] as string | undefined)
      late(400)(request, response)
    }
    const previous = process.env.DAYTONA_V2_API_URL
    process.env.DAYTONA_V2_API_URL = base
    try {
      const daytona = new Daytona({ apiKey: 'k', requestTimeoutSeconds: 0.1 })
      const error = await daytona.v2.create('python:3.12', { idempotencyKey: 'run-42' }).catch((caught: unknown) => caught)
      expect(error).toBeInstanceOf(SandboxCreateRequestTimeoutError)
      expect(error).toBeInstanceOf(ClientTimeoutError)
      expect(error).toMatchObject({ idempotencyKey: 'run-42', statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      expect(keys).toEqual(['run-42'])
      await daytona.close()
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
      else process.env.DAYTONA_V2_API_URL = previous
    }
  })

  it('keeps a server 408 on the create POST a RequestTimeoutError', async () => {
    handle = (request, response) => {
      request.resume()
      response.writeHead(408, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API', message: 'slow' }))
    }
    const previous = process.env.DAYTONA_V2_API_URL
    process.env.DAYTONA_V2_API_URL = base
    try {
      const daytona = new Daytona({ apiKey: 'k' })
      const error = await daytona.v2.create('python:3.12').catch((caught: unknown) => caught)
      expect(error).toBeInstanceOf(RequestTimeoutError)
      expect(error).not.toBeInstanceOf(SandboxCreateRequestTimeoutError)
      await daytona.close()
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
      else process.env.DAYTONA_V2_API_URL = previous
    }
  })

  it('arms no timer that would fire at once for a budget above the timer maximum', async () => {
    const huge = 2_200_000_000
    handle = late(60)
    await expect(new HttpTransport(base, 'k', huge).get('/slow')).resolves.toEqual({ ok: true })
    await expect(createHttpClient(base, 'k', huge).get('/slow')).resolves.toMatchObject({ data: { ok: true } })
    const response = await new HttpTransport(base, 'k', huge).fetch('/slow')
    expect(await response.json()).toEqual({ ok: true })
  })

  describe('one deadline for the bearer, a 401 refresh and the retry', () => {
    // Each step fits the 150 ms timeout on its own; together (about 260 ms)
    // they do not, so a fresh window per step would let the call succeed. The
    // rejections prove it; no wall-clock upper bound (a loaded runner could
    // break one), and the resolving controls get a budget far above their steps.
    const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))
    const unauthorizedThenOk = (request: IncomingMessage, response: ServerResponse): void => {
      request.resume()
      setTimeout(() => {
        const stale = request.headers.authorization === 'Bearer stale'
        response.writeHead(stale ? 401 : 200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify(stale ? { code: 'UNAUTHORIZED', message: 'expired' } : { ok: true }))
      }, 50)
    }
    const slowSandboxAuth = (delay: number) => ({
      bearer: async () => { await sleep(delay); return 'stale' },
      onUnauthorized: async () => { await sleep(delay); return 'fresh' },
    })
    const slowProvider = (delay: number) => ({
      authorizationHeader: async (options?: { readonly forceRefresh?: boolean; readonly signal?: AbortSignal }) => {
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(resolve, delay)
          options?.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(options.signal?.reason) }, { once: true })
        })
        return options?.forceRefresh ? 'Bearer fresh' : 'Bearer stale'
      },
    })

    it('aborts a project-token refresh that ignores its signal at the remaining budget, and sends no retry', async () => {
      const seen: (string | undefined)[] = []
      handle = (request, response) => {
        seen.push(request.headers.authorization)
        unauthorizedThenOk(request, response)
      }
      // The refresh ignores its signal and never settles until released, so the
      // rejection alone proves the call stopped waiting at its budget.
      let release: () => void = () => undefined
      const deaf = {
        authorizationHeader: async (options?: { readonly forceRefresh?: boolean }) => {
          if (options?.forceRefresh) await new Promise<void>((resolve) => { release = resolve })
          return options?.forceRefresh ? 'Bearer fresh' : 'Bearer stale'
        },
      }
      await expect(new HttpTransport(base, undefined, 150, { authProvider: deaf }).get('/thing')).rejects.toBeInstanceOf(ClientTimeoutError)
      release()
      await sleep(100)
      expect(seen).toEqual(['Bearer stale'])
    })

    it('times out at once, without waiting on the provider, when the budget is already spent', async () => {
      const never = { authorizationHeader: (): Promise<string> => new Promise<string>(() => undefined) }
      const client = createHttpClient(base, undefined, 150, { authProvider: never })
      const spent = { daytona: { deadlineAt: 0 } } as never
      await expect(client.get('/thing', spent)).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    })

    it.each(['get', 'fetch'] as const)('does not give a sandbox-token retry a fresh window (%s)', async (method) => {
      // bearer 40 ms + 401 after 50 ms + refresh 10 ms + a 950 ms retry is
      // about 1050 ms: past the 1000 ms deadline, though the retry alone fits,
      // and the retry is sent with about 900 ms to spare (a slow runner still sends it).
      let calls = 0
      handle = (request, response) => {
        request.resume()
        calls += 1
        const stale = request.headers.authorization === 'Bearer stale'
        setTimeout(() => {
          response.writeHead(stale ? 401 : 200, { 'Content-Type': 'application/json' })
          response.end(JSON.stringify(stale ? { code: 'UNAUTHORIZED', message: 'expired' } : { ok: true }))
        }, stale ? 50 : 950)
      }
      const auth = {
        bearer: async () => { await sleep(40); return 'stale' },
        onUnauthorized: async () => { await sleep(10); return 'fresh' },
      }
      const transport = new HttpTransport(base, undefined, 1000, { auth })
      const call = method === 'get' ? transport.get('/thing') : transport.fetch('/thing').then((response) => response.json())
      await expect(call).rejects.toBeInstanceOf(ClientTimeoutError)
      expect(calls).toBe(2)
    })

    it.each([
      ['a sandbox token on the axios path', (timeout: number) => new HttpTransport(base, undefined, timeout, { auth: slowSandboxAuth(80) }), 'get'],
      ['a sandbox token on the fetch path', (timeout: number) => new HttpTransport(base, undefined, timeout, { auth: slowSandboxAuth(80) }), 'fetch'],
      ['a project token on the axios path', (timeout: number) => new HttpTransport(base, undefined, timeout, { authProvider: slowProvider(80) }), 'get'],
      ['a project token on the fetch path', (timeout: number) => new HttpTransport(base, undefined, timeout, { authProvider: slowProvider(80) }), 'fetch'],
    ] as const)('with %s', async (_label, transport, method) => {
      handle = unauthorizedThenOk
      const call = (t: HttpTransport) => method === 'get' ? t.get('/thing') : t.fetch('/thing').then((response) => response.json())
      await expect(call(transport(150))).rejects.toBeInstanceOf(ClientTimeoutError)
      // The same steps complete under a budget far above them.
      await expect(call(transport(10_000))).resolves.toEqual({ ok: true })
    })
  })

  it('lets a per-call timeout bound opening a stream in place of the request timeout', async () => {
    // Headers after 400 ms, then a body over 300 ms (Python's test_client_config.py checks the same).
    handle = (request, response) => {
      request.resume()
      const timer = setTimeout(() => trickle(6, 50)(request, response), 400)
      response.on('close', () => clearTimeout(timer))
    }
    const transport = new HttpTransport(base, 'k', 100)
    expect(await (await transport.fetch('/logs', { timeout: 5_000 })).text()).toBe('xxxxxx')
    expect(await (await transport.fetch('/logs', { timeout: 0 })).text()).toBe('xxxxxx')
    await expect(new HttpTransport(base, 'k', 10_000).fetch('/logs', { timeout: 150 })).rejects.toBeInstanceOf(ClientTimeoutError)
    await expect(transport.fetch('/logs')).rejects.toBeInstanceOf(ClientTimeoutError)
  })

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY, '100'])('refuses %p as a per-call fetch timeout, as for the request timeout', async (timeout) => {
    handle = late(0)
    await expect(new HttpTransport(base, 'k', 100).fetch('/logs', { timeout: timeout as never })).rejects.toMatchObject({
      constructor: BadRequestError,
      message: 'timeout must be a non-negative finite number of milliseconds (0 = no limit)',
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    })
  })

  it('bounds only the stream headers under a short per-call timeout', async () => {
    // Headers at once, then a body over 300 ms: twice the 150 ms per-call timeout.
    handle = trickle(6, 50)
    const response = await new HttpTransport(base, 'k', 10_000).fetch('/logs', { timeout: 150 })
    expect(await response.text()).toBe('xxxxxx')
  })

  it('bounds a stream only until its headers arrive', async () => {
    const transport = new HttpTransport(base, 'k', 100)
    handle = trickle(6, 50)
    const response = await transport.fetch('/logs')
    expect(await response.text()).toBe('xxxxxx')
    handle = late(400)
    await expect(transport.fetch('/stalled')).rejects.toBeInstanceOf(ClientTimeoutError)
  })
})

describe('missing credentials', () => {
  it('throws AuthenticationError with the catalog status and code', () => {
    const saved = { key: process.env.DAYTONA_API_KEY, jwt: process.env.DAYTONA_JWT_TOKEN }
    delete process.env.DAYTONA_API_KEY
    delete process.env.DAYTONA_JWT_TOKEN
    try {
      expect(() => new DaytonaV2({})).toThrow(expect.objectContaining({ statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' }))
    } finally {
      if (saved.key !== undefined) process.env.DAYTONA_API_KEY = saved.key
      if (saved.jwt !== undefined) process.env.DAYTONA_JWT_TOKEN = saved.jwt
    }
  })

  it.each([{ apiKey: '' }, { apiKey: 'k', jwtToken: '' }, { jwtToken: '', projectId: 'p' }])('rejects the explicitly empty value in %j instead of reading the environment', (config) => {
    const saved = process.env.DAYTONA_API_KEY
    process.env.DAYTONA_API_KEY = 'env-key'
    try {
      expectBadRequest(() => new DaytonaV2(config), 'must be a non-empty string when provided')
    } finally {
      if (saved === undefined) delete process.env.DAYTONA_API_KEY
      else process.env.DAYTONA_API_KEY = saved
    }
  })
})
