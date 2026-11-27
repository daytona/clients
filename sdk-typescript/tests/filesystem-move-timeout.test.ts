/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// `fs.move(..., { timeoutSeconds })` replaces the client's request timeout for
// one move, against a delayed response from a real HTTP server; a move is
// never retried. sdk-python/tests/test_filesystem_move_timeout.py asserts the
// same behaviour.

import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { INTERNAL } from '../src/v2/internal'
import { BadRequestError, ClientTimeoutError } from '../src/v2/index'
import type { MoveOptions } from '../src/v2/index'
import { HttpTransport } from '../src/v2/http'
import { FileSystemService } from '../src/v2/FileSystem'

describe('fs.move timeoutSeconds', () => {
  let server: Server
  let base: string
  let requests: string[]
  let delayMs: number

  beforeEach(async () => {
    requests = []
    delayMs = 400
    server = createServer((request: IncomingMessage, response: ServerResponse) => {
      requests.push(`${request.method} ${request.url}`)
      request.resume()
      const timer = setTimeout(() => {
        response.writeHead(200)
        response.end()
      }, delayMs)
      response.on('close', () => clearTimeout(timer))
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

  const move = (requestTimeoutMs: number, options?: MoveOptions): Promise<void> =>
    new FileSystemService(INTERNAL, new HttpTransport(base, 'k', requestTimeoutMs)).move('/big', '/mnt/big', options)

  it('keeps the request timeout when omitted, and never retries a move', async () => {
    await expect(move(100)).rejects.toBeInstanceOf(ClientTimeoutError)
    expect(requests).toEqual(['POST /files/move?source=%2Fbig&destination=%2Fmnt%2Fbig'])
  })

  it.each([2, 2.5, 0, 3_000_000])('lets a move outlast a shorter request timeout with timeoutSeconds %s', async (timeoutSeconds) => {
    // The move answers after 0.4 s, past the 0.1 s request timeout: a longer
    // per-call timeout, 0 and a value above the timer maximum all let it finish.
    await expect(move(100, { timeoutSeconds })).resolves.toBeUndefined()
    expect(requests).toHaveLength(1)
  })

  it('ends the call once at a shorter timeoutSeconds, without a retry', async () => {
    await expect(move(30_000, { timeoutSeconds: 0.1 })).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    expect(requests).toHaveLength(1)
  })

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY, null, '60', true])('refuses timeoutSeconds %p before any request', async (bad) => {
    await expect(move(30_000, { timeoutSeconds: bad as number })).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toEqual([])
  })

  it('refuses an unknown or renamed option before any request', async () => {
    await expect(move(30_000, { timeout: 5 } as unknown as MoveOptions)).rejects.toThrow('MoveOptions: `timeout` was renamed to `timeoutSeconds` (seconds)')
    await expect(move(30_000, { recursive: true } as unknown as MoveOptions)).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toEqual([])
  })
})
