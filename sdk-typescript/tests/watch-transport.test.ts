/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Directory watches over a real HTTP transport: a watch whose stream ends on
// its own, or stops short of its Content-Length, ends with ConnectionError
// (source SDK), as in the Python SDK.

import { createServer } from 'node:http'
import type { Server } from 'node:http'
import { once } from 'node:events'
import { FileSystemService } from '../src/v2/FileSystem'
import { BadRequestError, ConnectionError, DaytonaError } from '../src/v2/errors/DaytonaError'
import { ProcessService } from '../src/v2/Process'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'

const EVENT = JSON.stringify({ type: 'create', name: 'a', isDir: false, timestamp: 1 })

/** A URL nothing listens on: an ephemeral port reserved, then released. */
async function refusedUrl(): Promise<string> {
  const reserved = createServer()
  reserved.listen(0, '127.0.0.1')
  await once(reserved, 'listening')
  const address = reserved.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  await new Promise<void>((resolve) => reserved.close(() => resolve()))
  return `http://127.0.0.1:${address.port}`
}

/** Two fs event payloads (`e0`, `e1` padded) of `total` UTF-8 bytes together (as Python's test). */
function eventDataTotalling(total: number): string[] {
  const payload = (name: string) => JSON.stringify({ type: 'create', name, isDir: false, timestamp: 1 })
  const first = payload('e0')
  return [first, payload(`e1${'x'.repeat(total - first.length - payload('e1').length)}`)]
}

describe('watch stream drops', () => {
  let server: Server
  let base: string
  let clientClosed: { promise: Promise<void>; resolve: () => void }

  beforeEach(async () => {
    let resolve!: () => void
    const promise = new Promise<void>((done) => { resolve = done })
    clientClosed = { promise, resolve }
    server = createServer((request, response) => {
      request.resume()
      if (request.url?.startsWith('/w404') === true) {
        const body = JSON.stringify({ code: 'FILE_NOT_FOUND', message: 'no such path', source: 'DAYTONA_DAEMON' })
        response.writeHead(404, { 'Content-Type': 'application/json', 'Content-Length': String(body.length) })
        response.end(body)
        return
      }
      if (request.url?.startsWith('/nostart') === true) {
        response.writeHead(200, { 'Content-Type': 'text/event-stream', Connection: 'close' })
        response.end(': no start frame\n\n')
        return
      }
      if (request.url?.includes('/processes/p/logs') === true) {
        response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Content-Length': '100000' })
        response.write('event: log\ndata: {"cursor":"c1","channel":"stdout","data":"x","timestamp":1}\n\n')
        setTimeout(() => response.socket?.destroy(), 20)
        return
      }
      const bytes = /^\/bytes-(\d+)/.exec(request.url ?? '')
      if (bytes !== null) {
        // Two fs events before start whose data adds up to exactly N UTF-8
        // bytes, then start; the stream stays open as for flood-.
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.write(`${eventDataTotalling(Number(bytes[1])).map((data) => `event: fs\ndata: ${data}\n\n`).join('')}event: start\ndata: {}\n\n`)
        response.on('close', () => clientClosed.resolve())
        return
      }
      const flood = /^\/(o?)flood-(\d+)/.exec(request.url ?? '')
      if (flood !== null) {
        // N fs (oflood: overflow) events before start, then start; the stream
        // stays open until the client closes it, which the test is told about.
        const events = Array.from({ length: Number(flood[2]) }, (_, index) => flood[1] === 'o'
          ? 'event: overflow\ndata: {}\n\n'
          : `event: fs\ndata: ${JSON.stringify({ type: 'create', name: `e${index}`, isDir: false, timestamp: 1 })}\n\n`)
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.write(`${events.join('')}event: start\ndata: {}\n\n`)
        response.on('close', () => clientClosed.resolve())
        return
      }
      if (request.url?.startsWith('/burst') === true || request.url?.startsWith('/early') === true) {
        // start and ten events in ONE write (early: two events before start);
        // the stream stays open until the client closes it.
        const events = Array.from({ length: 10 }, (_, index) =>
          `event: fs\ndata: ${JSON.stringify({ type: 'create', name: `e${index}`, isDir: false, timestamp: 1 })}\n\n`)
        const start = 'event: start\ndata: {}\n\n'
        const frames = request.url.startsWith('/burst') ? [start, ...events] : [...events.slice(0, 2), start, ...events.slice(2)]
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.write(frames.join(''))
        return
      }
      const truncated = request.url?.startsWith('/truncated') === true
      response.writeHead(200, truncated
        ? { 'Content-Type': 'text/event-stream', 'Content-Length': '100000' }
        : { 'Content-Type': 'text/event-stream', Connection: 'close' })
      response.write(`event: start\ndata: {}\n\nevent: fs\ndata: ${EVENT}\n\n`)
      if (truncated) setTimeout(() => response.socket?.destroy(), 20)
      else response.end()
    })
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

  it.each([
    ['eof', 'filesystem watch stream ended unexpectedly'],
    ['truncated', undefined],
  ] as const)('ends a watch whose stream drops (%s) with a ConnectionError', async (path, message) => {
    const fs = new FileSystemService(INTERNAL, new HttpTransport(`${base}/${path}`, 'k', 5_000))
    const seen: string[] = []
    const handle = await fs.watchDir('/', (event) => {
      seen.push(event.name)
    })
    const error = await handle.waitForExit()
    expect(seen).toEqual(['a'])
    expect(error).toBeInstanceOf(ConnectionError)
    expect(error).toMatchObject({ source: 'SDK' })
    if (message !== undefined) expect(error?.message).toBe(message)
  })

  it.each(['burst', 'early'] as const)('runs no callback before watchDir() returns, so a callback can stop its own watch (%s)', async (path) => {
    const fs = new FileSystemService(INTERNAL, new HttpTransport(`${base}/${path}`, 'k', 5_000))
    for (let run = 0; run < 20; run++) {
      const seen: string[] = []
      const early: string[] = []
      let returned = false
      const handle: Awaited<ReturnType<typeof fs.watchDir>> = await fs.watchDir('/', async (event) => {
        if (!returned) early.push(event.name)
        seen.push(event.name)
        await handle.stop()
      })
      returned = true
      await expect(handle.waitForExit()).resolves.toBeUndefined()
      expect({ early, seen }).toEqual({ early: [], seen: ['e0'] })
    }
  })

  it('holds 4096 events before start and delivers them in order after it', async () => {
    const fs = new FileSystemService(INTERNAL, new HttpTransport(`${base}/flood-4096`, 'k', 5_000))
    const seen: string[] = []
    let allSeen!: () => void
    const done = new Promise<void>((resolve) => { allSeen = resolve })
    const handle = await fs.watchDir('/', (event) => {
      seen.push(event.name)
      if (seen.length === 4096) allSeen()
    })
    await done
    await handle.stop()
    expect(seen).toEqual(Array.from({ length: 4096 }, (_, index) => `e${index}`))
  })

  it('refuses a stream with 4097 events before start without calling onExit, and closes it', async () => {
    const fs = new FileSystemService(INTERNAL, new HttpTransport(`${base}/flood-4097`, 'k', 5_000))
    const exits: unknown[] = []
    const seen: unknown[] = []
    const error = await fs.watchDir('/', (event) => { seen.push(event) }, { onExit: (exit) => { exits.push(exit) } })
      .then(() => undefined, (caught: unknown) => caught)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({
      message: 'expected filesystem watch stream: it sent more than 4096 events before start',
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
    await clientClosed.promise
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect({ exits, seen }).toEqual({ exits: [], seen: [] })
  })

  it('holds at most 1 MiB of event data before start, whatever the count', async () => {
    const seen: string[] = []
    let both!: () => void
    const done = new Promise<void>((resolve) => { both = resolve })
    const handle = await new FileSystemService(INTERNAL, new HttpTransport(`${base}/bytes-1048576`, 'k', 5_000)).watchDir('/', (event) => {
      seen.push(event.name.slice(0, 2))
      if (seen.length === 2) both()
    })
    await done
    await handle.stop()
    expect(seen).toEqual(['e0', 'e1'])
  })

  it('refuses a stream with more than 1 MiB of event data before start, without onExit, and closes it', async () => {
    const exits: unknown[] = []
    const error = await new FileSystemService(INTERNAL, new HttpTransport(`${base}/bytes-1048577`, 'k', 5_000))
      .watchDir('/', () => undefined, { onExit: (exit) => { exits.push(exit) } })
      .then(() => undefined, (caught: unknown) => caught)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ message: 'expected filesystem watch stream: it sent more than 1048576 bytes of events before start', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await clientClosed.promise
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(exits).toEqual([])
  })

  it('counts overflow events before start toward the cap without an onOverflow callback', async () => {
    const exits: unknown[] = []
    const onExit = (exit: unknown) => { exits.push(exit) }
    const refused = await new FileSystemService(INTERNAL, new HttpTransport(`${base}/oflood-4097`, 'k', 5_000))
      .watchDir('/', () => undefined, { onExit })
      .then(() => undefined, (caught: unknown) => caught)
    expect(refused).toBeInstanceOf(BadRequestError)
    expect(refused).toMatchObject({ message: 'expected filesystem watch stream: it sent more than 4096 events before start' })
    await clientClosed.promise
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(exits).toEqual([])

    const handle = await new FileSystemService(INTERNAL, new HttpTransport(`${base}/oflood-4096`, 'k', 5_000))
      .watchDir('/', () => undefined, { onExit })
    expect(handle.exitError).toBeUndefined()
    await handle.stop()
    expect(exits).toEqual([undefined])
  })

  it.each(['refused', 'w404', 'nostart'] as const)('throws a watch that fails before start (%s) from watchDir() without calling onExit', async (kind) => {
    const url = kind === 'refused' ? await refusedUrl() : `${base}/${kind}`
    const fs = new FileSystemService(INTERNAL, new HttpTransport(url, 'k', 5_000))
    const exits: unknown[] = []
    const pending = fs.watchDir('/', () => undefined, { onExit: (error) => { exits.push(error) } })
    await expect(pending).rejects.toBeInstanceOf(kind === 'w404' ? DaytonaError : ConnectionError)
    if (kind === 'w404') await expect(pending).rejects.toMatchObject({ statusCode: 404 })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(exits).toEqual([])
  })

  it('ends a log stream cut short with a ConnectionError, as the Python SDK does', async () => {
    const handle = new ProcessService(INTERNAL, new HttpTransport(base, 'k', 5_000), 'sbx').connect('p')
    const seen: string[] = []
    let thrown: unknown
    try {
      for await (const event of handle.streamLogs()) seen.push(event.type)
    } catch (error) {
      thrown = error
    }
    expect(seen).toEqual(['log'])
    expect(thrown).toBeInstanceOf(ConnectionError)
    expect(thrown).toMatchObject({ source: 'SDK' })
    expect((thrown as { cause?: { name?: string } }).cause?.name).toBe('TypeError')
  })
})

describe('watch stop() while onExit runs', () => {
  const deferred = () => {
    let resolve!: () => void
    const promise = new Promise<void>((done) => { resolve = done })
    return { promise, resolve }
  }
  // A watch whose stream sends start and ends when `end()` is called, which
  // runs onExit.
  const controlledWatch = () => {
    let end!: () => void
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: start\ndata: {}\n\n'))
        end = () => controller.close()
      },
    })
    const transport = new HttpTransport('http://fake.invalid', 'k', 5_000)
    transport.fetch = async () => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
    return { fs: new FileSystemService(INTERNAL, transport), end: () => end() }
  }
  // Runs watchDir() with or without AsyncLocalStorage, as a browser has none.
  const withoutScope = async <T>(withScope: boolean, run: () => Promise<T>): Promise<T> => {
    const runtime = process as { getBuiltinModule?: unknown }
    const builtin = runtime.getBuiltinModule
    if (!withScope) runtime.getBuiltinModule = undefined
    try {
      return await run()
    } finally {
      runtime.getBuiltinModule = builtin
    }
  }

  it.each([true, false])('resolves an external stop() only after an async onExit finishes (AsyncLocalStorage: %p)', async (withScope) => {
    const { fs, end } = controlledWatch()
    const entered = deferred()
    const release = deferred()
    let exitDone = false
    let stopDone = false
    const handle = await withoutScope(withScope, () => fs.watchDir('/', () => undefined, {
      onExit: async () => {
        entered.resolve()
        await release.promise
        exitDone = true
      },
    }))
    end()
    await entered.promise
    const stopping = handle.stop().then(() => { stopDone = true })
    for (let tick = 0; tick < 10; tick++) await Promise.resolve()
    expect({ stopDone, exitDone }).toEqual({ stopDone: false, exitDone: false })
    release.resolve()
    await stopping
    expect({ stopDone, exitDone }).toEqual({ stopDone: true, exitDone: true })
  })

  it.each([true, false])('returns a stop() that onExit makes synchronously without waiting for itself (AsyncLocalStorage: %p)', async (withScope) => {
    const { fs, end } = controlledWatch()
    const ownStop = deferred()
    let exitDone = false
    const box: { handle?: Awaited<ReturnType<FileSystemService['watchDir']>> } = {}
    box.handle = await withoutScope(withScope, () => fs.watchDir('/', () => undefined, {
      onExit: async () => {
        // Synchronous part: the watch's own stop(), awaited after onExit's
        // first await; it must resolve while onExit is still running.
        const stopping = box.handle?.stop()
        await stopping
        ownStop.resolve()
        exitDone = true
      },
    }))
    end()
    await ownStop.promise
    await box.handle.stop()
    expect(exitDone).toBe(true)
  })
})
