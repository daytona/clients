/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Shared with the Python SDK's test_toolbox_surface.py: a 2xx response that
// declares JSON and does not decode (invalid JSON, invalid UTF-8, a
// non-standard constant) is the malformed-response error at the transport,
// whether or not the call reads the body. Served over real HTTP so the body
// goes through axios' own adapters.

import { createServer } from 'node:http'
import type { Server } from 'node:http'
import { once } from 'node:events'
import { FileSystemService } from '../src/v2/FileSystem'
import { BadRequestError } from '../src/v2/errors/DaytonaError'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'

const FILE_INFO = {
  name: 'a', path: '/d/a', size: 3, mode: '-rw-r--r--', modTime: 'x', modifiedAt: '2026-01-01T00:00:00Z',
  isDir: false, owner: 'u', group: 'g', permissions: '0644',
}

const BODIES: Record<string, Buffer> = {
  bad: Buffer.from('{bad'),
  utf8: Buffer.concat([Buffer.from('{"a":"'), Buffer.from([0xff]), Buffer.from('"}')]),
  nan: Buffer.from('{"a": NaN}'),
  valid: Buffer.from(JSON.stringify(FILE_INFO)),
  empty: Buffer.alloc(0),
}

describe('JSON response bodies', () => {
  let server: Server
  let base: string

  beforeEach(async () => {
    server = createServer((request, response) => {
      request.resume()
      // /<body>/<content type: json | text>/...
      const [, body = 'valid', type = 'json'] = (request.url ?? '').split('/')
      response.writeHead(200, { 'Content-Type': type === 'json' ? 'application/json; charset=utf-8' : 'text/plain' })
      response.end(BODIES[body])
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

  const fs = (body: string, type = 'json') => new FileSystemService(INTERNAL, new HttpTransport(`${base}/${body}/${type}`, 'k', 5_000))

  it.each(['bad', 'utf8', 'nan'].flatMap((body) => [[body, 'stat'], [body, 'remove']] as const))(
    'refuses a %s JSON body on a call that %s',
    async (body, call) => {
      const service = fs(body)
      const error = await (call === 'stat' ? service.stat('/a') : service.remove('/a')).then(() => undefined, (caught: unknown) => caught)
      expect(error).toBeInstanceOf(BadRequestError)
      expect(error).toMatchObject({
        message: 'expected JSON response: the body is not valid JSON', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
      })
    },
  )

  it('leaves a valid JSON body, an empty one and a non-JSON content type as they were', async () => {
    await expect(fs('valid').stat('/d/a')).resolves.toMatchObject({ size: 3 })
    await expect(fs('empty').remove('/a')).resolves.toBeUndefined()
    await expect(fs('bad', 'text').remove('/a')).resolves.toBeUndefined()
  })
})
