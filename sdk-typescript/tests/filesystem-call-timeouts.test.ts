/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// `remove()`, `grep()`, `glob()`, `replace()` and `chmod()` take a per-call
// `timeoutSeconds` that replaces the client's request timeout for that call,
// against a delayed response from a real HTTP server.
// sdk-python/tests/test_filesystem_call_timeouts.py asserts the same behaviour.

import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { INTERNAL } from '../src/v2/internal'
import { BadRequestError, ClientTimeoutError } from '../src/v2/index'
import { HttpTransport } from '../src/v2/http'
import { FileSystemService } from '../src/v2/FileSystem'

type Call = (fs: FileSystemService, timeoutSeconds?: unknown) => Promise<unknown>
type Options = { timeoutSeconds?: number }
const options = (timeoutSeconds: unknown): Options => (timeoutSeconds === undefined ? {} : { timeoutSeconds: timeoutSeconds as number })

// name, request line prefix, call, JSON response body
const CALLS: ReadonlyArray<readonly [string, string, Call, string]> = [
  ['remove', 'DELETE /files?', (fs, t) => fs.remove('/tree', { recursive: true, ...options(t) }), ''],
  ['grep', 'GET /files/find?', (fs, t) => fs.grep('/tree', 'needle', options(t)), '{"items":[]}'],
  ['glob', 'GET /files/search?', (fs, t) => fs.glob('/tree', '*.ts', options(t)), '{"files":[]}'],
  ['replace', 'POST /files/replace', (fs, t) => fs.replace(['/tree/a'], 'old', 'new', options(t)), '{"items":[]}'],
  ['chmod', 'POST /files/permissions?', (fs, t) => fs.chmod('/tree', { mode: '0755', ...options(t) }), ''],
]

describe('filesystem per-call timeoutSeconds', () => {
  let server: Server
  let base: string
  let requests: string[]
  let body: string

  beforeEach(async () => {
    requests = []
    body = ''
    server = createServer((request: IncomingMessage, response: ServerResponse) => {
      requests.push(`${request.method} ${request.url}`)
      request.resume()
      const timer = setTimeout(() => {
        response.writeHead(200, body === '' ? {} : { 'content-type': 'application/json' })
        response.end(body)
      }, 400)
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

  const fileSystem = (requestTimeoutMs: number): FileSystemService => new FileSystemService(INTERNAL, new HttpTransport(base, 'k', requestTimeoutMs))

  describe.each(CALLS)('%s', (_name, route, call, reply) => {
    beforeEach(() => { body = reply })

    it('keeps the request timeout when omitted', async () => {
      await expect(call(fileSystem(100))).rejects.toBeInstanceOf(ClientTimeoutError)
      expect(requests).toHaveLength(1)
      expect(requests[0]?.startsWith(route)).toBe(true)
    })

    it.each([2, 0, 3_000_000])('outlasts a shorter request timeout with timeoutSeconds %s', async (timeoutSeconds) => {
      // The call answers after 0.4 s, past the 0.1 s request timeout: a longer
      // per-call timeout, 0 and a value above the timer maximum all let it finish.
      await call(fileSystem(100), timeoutSeconds)
      expect(requests).toHaveLength(1)
    })

    it('ends the call at a shorter timeoutSeconds', async () => {
      await expect(call(fileSystem(30_000), 0.1)).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      expect(requests).toHaveLength(1)
    })

    it.each([-1, Number.NaN, Number.POSITIVE_INFINITY, null, '60', true])('refuses timeoutSeconds %p before any request', async (bad) => {
      await expect(call(fileSystem(30_000), bad)).rejects.toBeInstanceOf(BadRequestError)
      expect(requests).toEqual([])
    })
  })

  it('refuses an unknown or renamed option before any request', async () => {
    const fs = fileSystem(30_000)
    await expect(fs.grep('/tree', 'x', { timeout: 5 } as unknown as Options)).rejects.toThrow('FileSearchOptions: `timeout` was renamed to `timeoutSeconds` (seconds)')
    await expect(fs.glob('/tree', 'x', { recursive: true } as unknown as Options)).rejects.toBeInstanceOf(BadRequestError)
    await expect(fs.replace(['/a'], 'x', 'y', { timeout: 5 } as unknown as Options)).rejects.toThrow('ReplaceOptions: `timeout` was renamed to `timeoutSeconds` (seconds)')
    await expect(fs.remove('/a', { timeout: 5 } as unknown as Options)).rejects.toThrow('RemoveOptions: `timeout` was renamed')
    await expect(fs.chmod('/a', { timeout: 5 } as unknown as Options)).rejects.toThrow('FilePermissionsOptions: `timeout` was renamed')
    expect(requests).toEqual([])
  })
})
