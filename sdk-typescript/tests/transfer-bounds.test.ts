/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// File transfers are idle-bounded, and buffered response bodies are capped.
// Every test runs against a real HTTP server. sdk-python/tests/test_transfer_bounds.py
// holds the Python transports to the same rules.

import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Daytona } from '../src/v2/Daytona'
import { BadRequestError, ClientTimeoutError, NotFoundError, PayloadTooLargeError } from '../src/v2/errors/DaytonaError'
import { HttpTransport, forceFetchAdapterForTests } from '../src/v2/http'
import { attachHub, detachHub, eventsHubConfig } from '../src/v2/events'
import { BodyBuffer } from '../src/v2/bodyBuffer'
import { lazySandbox } from './lazySandbox'

jest.setTimeout(30_000)

const IDLE_SECONDS = 0.5
const STALL_MS = 5_000

type Handler = (request: IncomingMessage, response: ServerResponse) => void

let server: Server
let base: string
let route: Handler = () => undefined
let dir: string
const savedApiUrl = process.env.DAYTONA_V2_API_URL

beforeAll(async () => {
  server = createServer((request, response) => route(request, response))
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  base = `http://127.0.0.1:${address.port}`
  process.env.DAYTONA_V2_API_URL = base
  dir = await mkdtemp(join(tmpdir(), 'daytona-bounds-'))
})

afterAll(async () => {
  if (savedApiUrl === undefined) delete process.env.DAYTONA_V2_API_URL
  else process.env.DAYTONA_V2_API_URL = savedApiUrl
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
  await rm(dir, { recursive: true, force: true })
})

/** Hang up after a stall, so a client with no idle bound fails (not with a timeout) instead of hanging the suite. */
function stallThenHangUp(response: ServerResponse): void {
  const timer = setTimeout(() => response.destroy(), STALL_MS)
  response.on('close', () => clearTimeout(timer))
}

const stallBeforeHeaders: Handler = (request, response) => {
  request.resume()
  stallThenHangUp(response)
}

const stallMidBody: Handler = (request, response) => {
  request.resume()
  response.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': '2000' })
  response.write('x'.repeat(1000))
  stallThenHangUp(response)
}

// Reads nothing of the upload: the client's writes stall once the buffers fill.
const stopReading: Handler = (_request, response) => {
  stallThenHangUp(response)
}

function trickle(chunks: number, gapMs: number, chunk = 'x'): Handler {
  return (request, response) => {
    request.resume()
    response.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': String(chunks * chunk.length) })
    response.flushHeaders()
    let sent = 0
    const timer = setInterval(() => {
      response.write(chunk)
      if (++sent === chunks) {
        clearInterval(timer)
        response.end()
      }
    }, gapMs)
    response.on('close', () => clearInterval(timer))
  }
}

function body(payload: string, init: { status?: number; contentType?: string; chunked?: boolean } = {}): Handler {
  return (request, response) => {
    request.resume()
    const headers: Record<string, string> = { 'Content-Type': init.contentType ?? 'application/octet-stream' }
    if (!init.chunked) headers['Content-Length'] = String(Buffer.byteLength(payload))
    response.writeHead(init.status ?? 200, headers)
    if (init.chunked) {
      for (let i = 0; i < payload.length; i += 512) response.write(payload.slice(i, i + 512))
      response.end()
    } else {
      response.end(payload)
    }
  }
}

/** Declares `size` bytes, then sends a byte every 500 ms and hangs up after 3 s. */
function declaredThenSlow(size: number): Handler {
  return (request, response) => {
    request.resume()
    response.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': String(size) })
    response.flushHeaders()
    let sent = 0
    const timer = setInterval(() => {
      response.write('x')
      if (++sent === 6) {
        clearInterval(timer)
        response.destroy()
      }
    }, 500)
    response.on('close', () => clearInterval(timer))
  }
}

function sandboxFor(config: { requestTimeoutSeconds?: number; maxResponseBytes?: number | null } = {}) {
  const client = new Daytona({ apiKey: 'k', ...config })
  return { client, sandbox: lazySandbox(client, 'sbx', { toolboxUrl: base, accessToken: 't' }) }
}

async function timesOut(call: () => Promise<unknown>): Promise<number> {
  const started = performance.now()
  await expect(call()).rejects.toMatchObject({ constructor: ClientTimeoutError, statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
  return performance.now() - started
}

describe.each(['http', 'fetch'] as const)('the transfer idle bound (%s adapter)', (adapter) => {
  beforeAll(() => forceFetchAdapterForTests(adapter === 'fetch'))
  afterAll(() => forceFetchAdapterForTests(false))

  it.each([
    ['before the response headers', stallBeforeHeaders],
    ['mid body', stallMidBody],
  ])('fails a download stalled %s after the idle bound', async (_label, handler) => {
    route = handler
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: IDLE_SECONDS })
    try {
      for (const call of [
        () => sandbox.fs.readBytes('/a'),
        () => sandbox.fs.readMany(['/a']),
        () => sandbox.computerUse.recording.download('rec'),
        () => sandbox.fs.downloadFile('/a', join(dir, 'out.bin')),
      ]) {
        const elapsed = await timesOut(call)
        expect(elapsed).toBeGreaterThanOrEqual(IDLE_SECONDS * 800)
        expect(elapsed).toBeLessThan(STALL_MS - 1000)
      }
    } finally {
      await client.close()
    }
  })

  it('fails an upload the server stops reading after the idle bound', async () => {
    route = stopReading
    const big = new Uint8Array(64 << 20)
    const local = join(dir, 'big.bin')
    await writeFile(local, big)
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: IDLE_SECONDS })
    try {
      // uploadFile streams its body, so every runtime sees its progress. The
      // axios uploads report progress through Node's http adapter only; with
      // fetch their bound starts at the response headers (next test).
      const calls = adapter === 'http'
        ? [() => sandbox.fs.writeBytes('/a', big), () => sandbox.fs.writeMany({ '/a': big }), () => sandbox.fs.uploadFile(local, '/a')]
        : [() => sandbox.fs.uploadFile(local, '/a')]
      for (const call of calls) {
        expect(await timesOut(call)).toBeLessThan(STALL_MS - 1000)
      }
    } finally {
      await client.close()
    }
  })

  it('bounds a small upload from the start and a large one from its response headers on, in every runtime', async () => {
    // A small body is handed to the socket at once: a stall is a wait for the response.
    route = stallBeforeHeaders
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: IDLE_SECONDS })
    try {
      expect(await timesOut(() => sandbox.fs.writeBytes('/a', new Uint8Array(1024)))).toBeLessThan(STALL_MS - 1000)
      // A large one where fetch reports no upload progress: bounded once the
      // server answers (here it reads the upload, answers, then stalls mid body).
      route = (request, response) => {
        request.on('end', () => stallMidBody(request, response))
        request.resume()
      }
      expect(await timesOut(() => sandbox.fs.writeBytes('/a', new Uint8Array(1 << 20)))).toBeLessThan(STALL_MS - 1000)
    } finally {
      await client.close()
    }
  })

  it('bounds a buffered upload to a server that stops reading, small from the start and large from its answer', async () => {
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: IDLE_SECONDS })
    const transport = new HttpTransport(base, 't', IDLE_SECONDS * 1000)
    // Small (1 KiB): the server never reads and never answers.
    route = stopReading
    const small = new Uint8Array(1024)
    try {
      for (const call of [
        () => sandbox.fs.writeBytes('/a', small),
        () => sandbox.fs.writeText('/a', 'x'.repeat(1024)),
        () => sandbox.fs.writeMany({ '/a': small }),
        () => transport.fetch('/files/upload?path=%2Fa', { method: 'POST', body: small, transfer: true }),
        () => transport.fetch('/files/upload?path=%2Fa', { method: 'POST', body: new Blob([small]), transfer: true }),
      ]) {
        expect(await timesOut(call)).toBeLessThan(STALL_MS - 1000)
      }
      // Large (4 MiB): the server stops reading and answers; the answer
      // stalls mid body. Where the runtime reports no upload progress (the
      // fetch transports) the bound starts at that answer; with Node's http
      // adapter it was running all along.
      route = (request, response) => {
        response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '2000', Connection: 'close' })
        response.write('{')
        stallThenHangUp(response)
        request.pause()
      }
      const large = new Uint8Array(4 << 20)
      for (const call of [
        () => sandbox.fs.writeBytes('/a', large),
        () => sandbox.fs.writeMany({ '/a': large }),
        () => transport.fetch('/files/upload?path=%2Fa', { method: 'POST', body: new Blob([large]), transfer: true }).then((response) => response.text()),
      ]) {
        expect(await timesOut(call)).toBeLessThan(STALL_MS - 1000)
      }
    } finally {
      await client.close()
    }
  })

  it('ends a call whose credential never resolves at the request timeout, ordinary or transfer', async () => {
    route = body('{}', { contentType: 'application/json' })
    // Providers that ignore their signal and never settle.
    const authProvider = { authorizationHeader: (): Promise<string> => new Promise<string>(() => undefined) }
    const auth = { bearer: (): Promise<string> => new Promise<string>(() => undefined), onUnauthorized: async (): Promise<undefined> => undefined }
    for (const options of [{ authProvider }, { auth }]) {
      const transport = new HttpTransport(base, undefined, IDLE_SECONDS * 1000, options)
      for (const call of [
        () => transport.get('/thing'),
        () => transport.fetch('/thing'),
        () => transport.getBytes('/files/download?path=a'),
        () => transport.fetch('/files/download?path=a', { transfer: true }),
      ]) {
        const elapsed = await timesOut(call)
        expect(elapsed).toBeLessThan(STALL_MS - 1000)
      }
    }
  })

  it('bounds a 401 refresh whose provider never resolves by the same deadline', async () => {
    route = body(JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired' }), { status: 401, contentType: 'application/json' })
    const authProvider = {
      authorizationHeader: (options?: { readonly forceRefresh?: boolean }): Promise<string> =>
        options?.forceRefresh ? new Promise<string>(() => undefined) : Promise.resolve('Bearer stale'),
    }
    const transport = new HttpTransport(base, undefined, IDLE_SECONDS * 1000, { authProvider })
    for (const call of [() => transport.get('/thing'), () => transport.fetch('/thing'), () => transport.fetch('/thing', { method: 'POST', body: new ReadableStream(), duplex: 'half' } as never)]) {
      expect(await timesOut(call)).toBeLessThan(STALL_MS - 1000)
    }
  })

  it('keeps a transfer that keeps moving bytes going past the idle bound', async () => {
    // 12 bytes 200 ms apart: 2.4 s in all, five times the idle bound.
    route = trickle(12, 200)
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: IDLE_SECONDS })
    try {
      expect(new TextDecoder().decode(await sandbox.fs.readBytes('/a'))).toBe('x'.repeat(12))
      expect(new TextDecoder().decode(await sandbox.computerUse.recording.download('rec'))).toBe('x'.repeat(12))
      await sandbox.fs.downloadFile('/a', join(dir, 'moving.bin'))
      expect(await readFile(join(dir, 'moving.bin'), 'utf8')).toBe('x'.repeat(12))
    } finally {
      await client.close()
    }
  })

  it('leaves a transfer unbounded with request timeout 0 (until the caller cancels)', async () => {
    route = stallMidBody
    const { client, sandbox } = sandboxFor({ requestTimeoutSeconds: 0 })
    try {
      const controller = new AbortController()
      const started = performance.now()
      const download = sandbox.fs.downloadFile('/a', join(dir, 'unbounded.bin'), { signal: controller.signal })
      setTimeout(() => controller.abort(), 1_500)
      // Well past an idle bound of the other tests, ended only by the cancel.
      await expect(download).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' })
      expect(performance.now() - started).toBeGreaterThanOrEqual(1_400)
    } finally {
      await client.close()
    }
  })
})

function expectTooLarge(error: unknown, limit: number): void {
  expect(error).toBeInstanceOf(PayloadTooLargeError)
  expect(error).toMatchObject({ statusCode: 413, code: 'PAYLOAD_TOO_LARGE', source: 'SDK' })
  const message = (error as Error).message
  expect(message).toContain(`${limit}-byte`)
  expect(message).toContain('maxResponseBytes')
  expect(message).toContain('downloadFile')
}

async function rejection(call: () => Promise<unknown>): Promise<unknown> {
  try {
    await call()
  } catch (error) {
    return error
  }
  throw new Error('expected a rejection')
}

describe.each(['http', 'fetch'] as const)('the buffered body cap (%s adapter)', (adapter) => {
  beforeAll(() => forceFetchAdapterForTests(adapter === 'fetch'))
  afterAll(() => forceFetchAdapterForTests(false))

  it('refuses a declared length over the cap before reading the body', async () => {
    route = declaredThenSlow(10_000)
    const { client, sandbox } = sandboxFor({ maxResponseBytes: 1000 })
    try {
      const started = performance.now()
      expectTooLarge(await rejection(() => sandbox.fs.readBytes('/a')), 1000)
      // The body would take hours at that rate.
      expect(performance.now() - started).toBeLessThan(400)
    } finally {
      await client.close()
    }
  })

  it('refuses a chunked body once it passes the cap', async () => {
    route = body('y'.repeat(5120), { chunked: true })
    const { client, sandbox } = sandboxFor({ maxResponseBytes: 2000 })
    try {
      expectTooLarge(await rejection(() => sandbox.fs.readBytes('/a')), 2000)
      expect((await sandbox.fs.readBytes('/a', { maxResponseBytes: 5120 })).byteLength).toBe(5120)
    } finally {
      await client.close()
    }
  })

  it('lets the per-call option raise, lower and disable the cap', async () => {
    const payload = 'z'.repeat(5000)
    const multipart = `--b\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\nContent-Type: application/octet-stream\r\n\r\n${payload}\r\n--b--\r\n`
    route = (request, response) => (request.url?.startsWith('/files/bulk-download')
      ? body(multipart, { contentType: 'multipart/form-data; boundary=b' })
      : body(payload))(request, response)
    const small = sandboxFor({ maxResponseBytes: 1000 })
    const large = sandboxFor({ maxResponseBytes: 100_000 })
    try {
      const tight = small.sandbox
      expect(new TextDecoder().decode(await tight.fs.readBytes('/a', { maxResponseBytes: 10_000 }))).toBe(payload)
      expect(await tight.fs.readText('/a', { maxResponseBytes: 0 })).toBe(payload)
      expect(new TextDecoder().decode((await tight.fs.readMany(['/a'], { maxResponseBytes: 0 })).files['/a'])).toBe(payload)
      expect((await tight.computerUse.recording.download('r', { maxResponseBytes: 10_000 })).byteLength).toBe(5000)
      expect(await rejection(() => tight.fs.readBytes('/a'))).toBeInstanceOf(PayloadTooLargeError)
      expect(await rejection(() => tight.fs.readMany(['/a']))).toBeInstanceOf(PayloadTooLargeError)
      // null keeps the client's cap.
      expect(await rejection(() => tight.fs.readBytes('/a', { maxResponseBytes: null }))).toBeInstanceOf(PayloadTooLargeError)
      const roomy = large.sandbox
      expect((await roomy.fs.readBytes('/a')).byteLength).toBe(5000)
      for (const call of [
        () => roomy.fs.readBytes('/a', { maxResponseBytes: 100 }),
        () => roomy.fs.readText('/a', { maxResponseBytes: 100 }),
        () => roomy.fs.readMany(['/a'], { maxResponseBytes: 100 }),
        () => roomy.computerUse.recording.download('r', { maxResponseBytes: 100 }),
      ]) {
        expectTooLarge(await rejection(call), 100)
      }
    } finally {
      await small.client.close()
      await large.client.close()
    }
  })

  const invalid: readonly unknown[] = [-1, 1.5, true, '10', Number.MAX_SAFE_INTEGER + 1, Number.NaN]

  it.each(invalid)('refuses %p as the client cap', (value) => {
    expect(() => new Daytona({ apiKey: 'k', maxResponseBytes: value as never })).toThrow(expect.objectContaining({
      constructor: BadRequestError,
      message: `maxResponseBytes must be an integer from 0 (no limit) to ${Number.MAX_SAFE_INTEGER}`,
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    }))
  })

  it('takes undefined / null (the default), 0 and the safe-integer maximum as the client cap', () => {
    for (const value of [undefined, null, 0, Number.MAX_SAFE_INTEGER]) {
      expect(() => new Daytona({ apiKey: 'k', maxResponseBytes: value })).not.toThrow()
    }
  })

  it.each(invalid)('refuses %p as the per-call cap before any request', async (value) => {
    let hits = 0
    route = (request, response) => {
      hits += 1
      body('ok')(request, response)
    }
    const { client, sandbox } = sandboxFor()
    try {
      for (const call of [
        () => sandbox.fs.readBytes('/a', { maxResponseBytes: value as never }),
        () => sandbox.fs.readText('/a', { maxResponseBytes: value as never }),
        () => sandbox.fs.readMany(['/a'], { maxResponseBytes: value as never }),
        () => sandbox.computerUse.recording.download('r', { maxResponseBytes: value as never }),
      ]) {
        await expect(call()).rejects.toMatchObject({ constructor: BadRequestError, statusCode: 400, code: 'BAD_REQUEST' })
      }
      // An unknown option key is refused too.
      await expect(sandbox.fs.readBytes('/a', { maxBytes: 1 } as never)).rejects.toBeInstanceOf(BadRequestError)
    } finally {
      await client.close()
    }
    expect(hits).toBe(0)
  })

  it('refuses a JSON response over a small client cap', async () => {
    route = body(JSON.stringify({ id: 'sbx', state: 'started', pad: 'p'.repeat(4000) }), { contentType: 'application/json', chunked: true })
    const client = new Daytona({ apiKey: 'k', maxResponseBytes: 1000 })
    try {
      expectTooLarge(await rejection(() => client.get('sbx')), 1000)
    } finally {
      await client.close()
    }
  })

  it('keeps the typed error of an error status whose body is over the cap', async () => {
    const big = JSON.stringify({ code: 'NOT_FOUND', message: 'm'.repeat(5000) })
    route = body(big, { status: 404, contentType: 'application/json' })
    const { client, sandbox } = sandboxFor({ maxResponseBytes: 1000 })
    try {
      for (const call of [() => client.get('sbx'), () => sandbox.fs.readBytes('/a'), () => sandbox.fs.downloadFile('/a', join(dir, 'never.bin'))]) {
        const error = await rejection(call)
        expect(error).toBeInstanceOf(NotFoundError)
        expect(error).toMatchObject({ statusCode: 404 })
        expect((error as Error).message).toContain('too large to read')
      }
    } finally {
      await client.close()
    }
  })

  it('caps the events hub\'s ticket mint and long-poll fallback by the client\'s limit', async () => {
    route = body(JSON.stringify({ id: 'sbx', status: 'started', pad: 'p'.repeat(4000) }), { contentType: 'application/json' })
    // The hub a client attaches uses the client's own API instance...
    const client = new Daytona({ apiKey: 'k', maxResponseBytes: 1000, sharedHub: false })
    const hub = (client as unknown as { acquireHub(): { api: { get(path: string): Promise<unknown> } } }).acquireHub()
    try {
      expectTooLarge(await rejection(() => hub.api.get('/sandboxes/sbx')), 1000)
    } finally {
      await client.close()
    }
    // ...and one that builds its own takes the cap it is given.
    const own = attachHub({ apiUrl: base, apiKey: 'k', target: undefined, shared: false, config: eventsHubConfig(undefined), maxResponseBytes: 1000 })
    try {
      expectTooLarge(await rejection(() => (own as unknown as { api: { get(path: string): Promise<unknown> } }).api.get('/sandboxes/sbx')), 1000)
    } finally {
      await detachHub(own, { apiUrl: base, apiKey: 'k', target: undefined, shared: false })
    }
  })

  it('never caps downloadFile', async () => {
    route = body('d'.repeat(50_000))
    const { client, sandbox } = sandboxFor({ maxResponseBytes: 1000 })
    try {
      await sandbox.fs.downloadFile('/a', join(dir, 'uncapped.bin'))
      expect((await readFile(join(dir, 'uncapped.bin'))).byteLength).toBe(50_000)
    } finally {
      await client.close()
    }
  })

  it('keeps the client working after a refused response', async () => {
    let calls = 0
    route = (request, response) => {
      calls += 1
      body('b'.repeat(request.url?.includes('big') ? 5000 : 10))(request, response)
    }
    const transport = new HttpTransport(base, 't', 5_000, { maxResponseBytes: 1000 })
    for (let i = 0; i < 3; i++) {
      await expect(transport.getBytes('/files/download?path=big')).rejects.toBeInstanceOf(PayloadTooLargeError)
      expect(new TextDecoder().decode(await transport.getBytes('/files/download?path=small'))).toBe('b'.repeat(10))
    }
    expect(calls).toBe(6)
  })
})

describe('BodyBuffer', () => {
  const bytes = (...values: number[]): Uint8Array => new Uint8Array(values)

  it('fills one allocation of the declared length in place', () => {
    const body = new BodyBuffer(100, 4)
    expect(body.add(bytes(1, 2))).toBe(true)
    expect(body.add(bytes(3, 4))).toBe(true)
    const out = body.bytes()
    expect([...out]).toEqual([1, 2, 3, 4])
    // The one allocation, not a copy of it.
    expect(out.buffer.byteLength).toBe(4)
    expect(body.bytes()).toBe(out)
  })

  it('keeps a body that outgrows or falls short of its declared length', () => {
    const longer = new BodyBuffer(100, 2)
    longer.add(bytes(1, 2))
    longer.add(bytes(3))
    expect([...longer.bytes()]).toEqual([1, 2, 3])
    const shorter = new BodyBuffer(100, 8)
    shorter.add(bytes(1))
    expect([...shorter.bytes()]).toEqual([1])
  })

  it('refuses a chunk that would pass the limit and keeps none of it', () => {
    const body = new BodyBuffer(3)
    expect(body.add(bytes(1, 2))).toBe(true)
    expect(body.add(bytes(3, 4))).toBe(false)
    expect([...body.bytes()]).toEqual([1, 2])
    // A declared length over the limit is not allocated.
    expect(new BodyBuffer(3, 1 << 30).add(bytes(1, 2, 3, 4))).toBe(false)
    expect(new BodyBuffer(0).add(new Uint8Array(10))).toBe(true)
  })
})
