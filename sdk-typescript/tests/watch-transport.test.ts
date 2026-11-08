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
import { ConnectionError, DaytonaError } from '../src/v2/errors/DaytonaError'
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

describe('watch stream drops', () => {
  let server: Server
  let base: string

  beforeEach(async () => {
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
