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
import { ConnectionError } from '../src/v2/errors/DaytonaError'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'

const EVENT = JSON.stringify({ type: 'create', name: 'a', isDir: false, timestamp: 1 })

describe('watch stream drops', () => {
  let server: Server
  let base: string

  beforeEach(async () => {
    server = createServer((request, response) => {
      request.resume()
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
})
