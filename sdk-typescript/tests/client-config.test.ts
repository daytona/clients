/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Client configuration: option keys, the environment, the request timeout and
// its meaning. sdk-python/tests/test_client_config.py asserts the same behaviour.

import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { Daytona } from '../src/index'
import { Daytona as DaytonaV2 } from '../src/v2/Daytona'
import { BadRequestError, AuthenticationError, ClientTimeoutError } from '../src/v2/index'
import { DEFAULT_REQUEST_TIMEOUT_MS, HttpTransport, createHttpClient } from '../src/v2/http'
import { RENAMED_OPTIONS, assertKnownOptions, optionKeys } from '../src/v2/options'
import { eventsHubConfig } from '../src/v2/events'
import { FileSystemService } from '../src/v2/FileSystem'

const ENV = ['DAYTONA_API_KEY', 'DAYTONA_JWT_TOKEN', 'DAYTONA_PROJECT_ID'] as const
const saved = Object.fromEntries(ENV.map((name) => [name, process.env[name]]))

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

  it('keeps the URL fields TypeError with its guidance', () => {
    expect(() => new Daytona({ apiKey: 'k', apiUrl: 'https://self.example' } as never)).toThrow(TypeError)
  })

  it('reports a renamed key only where its replacement exists', () => {
    const keys = optionKeys<{ readonly timeout?: number; readonly other?: string }>()({ timeout: true, other: true })
    expect(() => assertKnownOptions('X', { timeout: 1 }, keys)).not.toThrow()
    const plain = optionKeys<{ readonly other?: string }>()({ other: true })
    expectBadRequest(() => assertKnownOptions('X', { timeout: 1 } as never, plain), 'X has no option `timeout`')
    const seconds = optionKeys<{ readonly timeoutSeconds?: number }>()({ timeoutSeconds: true })
    expectBadRequest(() => assertKnownOptions('X', { timeout: 1 } as never, seconds), 'X: `timeout` was renamed to `timeoutSeconds` (seconds)')
  })

  it('carries the rename hints the duration rename needs', () => {
    expect(RENAMED_OPTIONS).toEqual({
      timeout: 'timeoutSeconds',
      executionTimeout: 'executionTimeoutSeconds',
      escalateAfter: 'escalateAfterSeconds',
      requestTimeout: 'requestTimeoutSeconds',
      reconcileIntervalMs: 'reconcileIntervalSeconds',
      idleCloseMs: 'idleCloseSeconds',
    })
    expect(Object.isFrozen(RENAMED_OPTIONS)).toBe(true)
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
      process.env.DAYTONA_V2_API_URL = previous.v2
      process.env.DAYTONA_API_URL = previous.v0sdk
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
    const started = performance.now()
    await expect(transport.get('/slow-body', { responseType: 'text' })).rejects.toBeInstanceOf(ClientTimeoutError)
    expect(performance.now() - started).toBeLessThan(350)
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

  it('does not cut file transfers', async () => {
    const transport = new HttpTransport(base, 'k', 100)
    const fs = new FileSystemService(transport)
    handle = trickle(6, 50)
    expect(new TextDecoder().decode(await fs.readBytes('/a'))).toBe('xxxxxx')
    handle = late(300)
    await expect(fs.writeBytes('/a', new Uint8Array([1, 2, 3]))).resolves.toBeUndefined()
    handle = trickle(6, 50)
    const response = await transport.fetch('/files/download?path=%2Fa', { transfer: true })
    expect(await response.text()).toBe('xxxxxx')
    handle = late(300)
    await expect(transport.fetch('/files/upload?path=%2Fa', { method: 'POST', body: 'data', transfer: true })).resolves.toMatchObject({ status: 200 })
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
