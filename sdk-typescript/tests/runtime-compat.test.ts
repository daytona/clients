/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Transport behavior that differs between JavaScript runtimes. The
// runtime-tests/ suite proves these in the runtimes themselves.

import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ClientTimeoutError, TransferCancelledError } from '../src/v2/errors/DaytonaError'
import { FileSystemService } from '../src/v2/FileSystem'
import { createHttpClient, fetchCacheModeOptions, HttpTransport, resetFetchCacheModeProbeForTests } from '../src/v2/http'

// Remix's @remix-run/web-fetch (and other fetch polyfills) reject an aborted
// fetch with their own Error subclass named AbortError, not a DOMException.
class PolyfillAbortError extends Error {
  constructor() {
    super('The operation was aborted.')
    this.name = 'AbortError'
  }
}

/** Resolves with a response whose body errors with `PolyfillAbortError` once the request aborts. */
function polyfillFetch(firstChunk: string): jest.SpiedFunction<typeof fetch> {
  return jest.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    const signal = init?.signal
    if (!signal) throw new Error('expected an AbortSignal')
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(firstChunk))
        signal.addEventListener('abort', () => controller.error(new PolyfillAbortError()), { once: true })
      },
    })
    return new Response(body, { headers: { 'content-type': 'text/event-stream' } })
  })
}

/** Never answers; rejects with `PolyfillAbortError` once the request aborts. */
function pendingPolyfillFetch(): jest.SpiedFunction<typeof fetch> {
  return jest.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    const signal = init?.signal
    if (!signal) throw new Error('expected an AbortSignal')
    return new Promise<Response>((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new PolyfillAbortError()), { once: true })
    })
  })
}

describe('aborts rejected by a fetch polyfill', () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('treats watchDir().stop() as a clean exit', async () => {
    polyfillFetch('event: start\ndata: {}\n\nevent: fs\ndata: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}\n\n')
    const fs = new FileSystemService(new HttpTransport('http://toolbox.invalid/sandbox', 'key'))
    const onExit = jest.fn()
    const seen: string[] = []

    const handle = await fs.watchDir('/', (event) => {
      seen.push(event.name)
    }, { onExit })
    for (let i = 0; i < 50 && seen.length === 0; i++) await new Promise((resolve) => setTimeout(resolve, 10))
    await handle.stop()

    expect(seen).toEqual(['a.txt'])
    await expect(handle.waitForExit()).resolves.toBeUndefined()
    expect(handle.exitError).toBeUndefined()
    expect(onExit).toHaveBeenCalledTimes(1)
    expect(onExit).toHaveBeenCalledWith(undefined)
  })

  it('rethrows a caller abort as the abort error, not a connection error', async () => {
    pendingPolyfillFetch()
    const transport = new HttpTransport('http://toolbox.invalid/sandbox', 'key')
    const controller = new AbortController()

    const pending = transport.fetch('/files', { signal: controller.signal })
    controller.abort()

    await expect(pending).rejects.toBeInstanceOf(PolyfillAbortError)
  })

  it('maps an abort at the request deadline to ClientTimeoutError', async () => {
    pendingPolyfillFetch()
    const transport = new HttpTransport('http://toolbox.invalid/sandbox', 'key', 20)

    await expect(transport.fetch('/files')).rejects.toBeInstanceOf(ClientTimeoutError)
  })

  it('reports an aborted download as TransferCancelledError', async () => {
    const transport = new HttpTransport('http://toolbox.invalid/sandbox', 'key')
    const fs = new FileSystemService(transport)
    const dir = await mkdtemp(join(tmpdir(), 'daytona-sdk-polyfill-abort-'))
    jest.spyOn(transport, 'fetch').mockRejectedValue(new PolyfillAbortError())
    try {
      await expect(fs.downloadFile('/remote.bin', join(dir, 'local.bin'))).rejects.toBeInstanceOf(TransferCancelledError)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})

// workerd's Request: it accepts `no-store` but refuses the `cache: 'default'`
// axios 1.20 sends on every fetch.
class WorkerdRequest extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.cache === 'default') throw new TypeError('Unsupported cache mode: default')
    super(input, init)
  }
}

describe('fetch cache mode', () => {
  const NativeRequest = globalThis.Request

  afterEach(() => {
    globalThis.Request = NativeRequest
    resetFetchCacheModeProbeForTests()
  })

  it('leaves requests unchanged where Request accepts the default cache mode', () => {
    resetFetchCacheModeProbeForTests()
    expect(fetchCacheModeOptions()).toBeUndefined()
    expect(createHttpClient('http://api.invalid', 'key').defaults.fetchOptions).toBeUndefined()
  })

  it('sends cache: no-store where Request refuses the default cache mode', async () => {
    globalThis.Request = WorkerdRequest
    resetFetchCacheModeProbeForTests()
    const client = createHttpClient('http://api.invalid', 'key')
    expect(client.defaults.fetchOptions).toEqual({ cache: 'no-store' })

    const sent: Request[] = []
    const fetchStub = async (input: RequestInfo | URL): Promise<Response> => {
      if (input instanceof Request) sent.push(input)
      return new Response('{"ok":true}', { headers: { 'content-type': 'application/json' } })
    }
    const { data } = await client.get('/sandboxes', { adapter: 'fetch', env: { fetch: fetchStub, Request: WorkerdRequest } })

    expect(data).toEqual({ ok: true })
    expect(sent.map((request) => request.cache)).toEqual(['no-store'])
  })

  it('sets nothing where Request refuses every cache mode', () => {
    globalThis.Request = class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        if (init?.cache !== undefined) throw new TypeError("The 'cache' field on 'RequestInitializerDict' is not implemented.")
        super(input, init)
      }
    }
    resetFetchCacheModeProbeForTests()
    expect(fetchCacheModeOptions()).toBeUndefined()
  })
})
