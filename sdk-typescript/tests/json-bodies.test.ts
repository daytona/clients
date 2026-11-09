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
import { BadRequestError, errorFromFetchResponse, NotFoundError, PayloadTooLargeError } from '../src/v2/errors/DaytonaError'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'
import { parseBulkReadResult } from '../src/v2/filesystemWire'

const FILE_INFO = {
  name: 'a', path: '/d/a', size: 3, mode: '-rw-r--r--', modTime: 'x', modifiedAt: '2026-01-01T00:00:00Z',
  isDir: false, owner: 'u', group: 'g', permissions: '0644',
}

const BODIES: Record<string, Buffer> = {
  bad: Buffer.from('{bad'),
  deep600: Buffer.from('['.repeat(600) + ']'.repeat(600)),
  deep601: Buffer.from('['.repeat(601) + ']'.repeat(601)),
  bomtext: Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('hello')]),
  bigbad: Buffer.from(`{bad${'x'.repeat(4096)}`),
  utf8: Buffer.concat([Buffer.from('{"a":"'), Buffer.from([0xff]), Buffer.from('"}')]),
  nan: Buffer.from('{"a": NaN}'),
  valid: Buffer.from(JSON.stringify(FILE_INFO)),
  empty: Buffer.alloc(0),
  // An error envelope nesting 600 / 601 deep (the object is one level).
  errdeep600: Buffer.from(`{"code":"FILE_NOT_FOUND","message":"gone","details":${'['.repeat(599)}${']'.repeat(599)}}`),
  errdeep601: Buffer.from(`{"code":"FILE_NOT_FOUND","message":"gone","details":${'['.repeat(600)}${']'.repeat(600)}}`),
}

describe('JSON response bodies', () => {
  let server: Server
  let base: string

  beforeEach(async () => {
    server = createServer((request, response) => {
      request.resume()
      // /<body>/<content type: json | text>/...; an errdeep body answers 404
      const [, body = 'valid', type = 'json'] = (request.url ?? '').split('/')
      const contentType = type === 'json' ? 'application/json; charset=utf-8' : type === 'mixed' ? 'Application/JSON; charset=UTF-8' : type === 'jsonl' ? 'application/jsonl' : 'text/plain'
      response.writeHead(body.startsWith('errdeep') ? 404 : 200, { 'Content-Type': contentType })
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

  it('reads a mixed-case JSON content type as JSON', async () => {
    await expect(fs('bad', 'mixed').stat('/a')).rejects.toMatchObject({
      message: 'expected JSON response: the body is not valid JSON', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
    await expect(fs('valid', 'mixed').stat('/d/a')).resolves.toMatchObject({ size: 3 })
  })

  it('reads a mixed-case multipart Boundary parameter', () => {
    const body = new TextEncoder().encode(
      '--XyZ\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\nContent-Type: application/octet-stream\r\n\r\nhi\r\n--XyZ--\r\n',
    )
    const result = parseBulkReadResult(body, 'Multipart/Form-Data; Boundary="XyZ"')
    expect(new TextDecoder().decode(result.files['/a'])).toBe('hi')
  })

  // The client cap is checked by the adapter while it reads the bytes, before
  // decodeResponseBody runs: an undecodable JSON body over the cap is refused
  // as too large, never as invalid JSON.
  it('refuses a JSON body over the client cap before decoding it', async () => {
    for (const body of ['valid', 'bigbad']) {
      const capped = new FileSystemService(INTERNAL, new HttpTransport(`${base}/${body}/json`, 'k', 5_000, { maxResponseBytes: 64 }))
      await expect(capped.stat('/d/a')).rejects.toMatchObject({
        constructor: PayloadTooLargeError, statusCode: 413, code: 'PAYLOAD_TOO_LARGE', source: 'SDK',
      })
    }
  })

  // Shared with the Python SDK's test_toolbox_surface.py: JSON nests at most
  // 600 deep; the media type must be exactly application/json.
  it('accepts JSON nested 600 deep and refuses 601', async () => {
    const transport = (body: string) => new HttpTransport(`${base}/${body}/json`, 'k', 5_000)
    await expect(transport('deep600').get('/x')).resolves.toBeDefined()
    await expect(transport('deep601').get('/x')).rejects.toMatchObject({
      constructor: BadRequestError, message: 'expected JSON response: the JSON is nested too deeply', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
  })

  // Shared with the Python SDK's test_toolbox_surface.py: an error response
  // keeps its status's typed error; a body nested past the limit is
  // unreadable, so it gives no code or details, and the message says so.
  it('reads an error body nested 600 deep and not one nested 601 deep', async () => {
    await expect(fs('errdeep600').stat('/a')).rejects.toMatchObject({
      message: 'gone', statusCode: 404, code: 'FILE_NOT_FOUND',
    })
    const tooDeep = {
      message: 'HTTP 404: the response body was nested too deeply to read (over 600 levels of JSON)', statusCode: 404, code: undefined,
    }
    const caught = await fs('errdeep601').stat('/a').then(() => undefined, (error: unknown) => error)
    expect(caught).toBeInstanceOf(NotFoundError)
    expect(caught).toMatchObject(tooDeep)

    const fetched = (body: string) => errorFromFetchResponse(new Response(String(BODIES[body]), { status: 404, headers: { 'Content-Type': 'application/json' } }))
    await expect(fetched('errdeep600')).resolves.toMatchObject({ statusCode: 404, code: 'FILE_NOT_FOUND' })
    const fromFetch = await fetched('errdeep601')
    expect(fromFetch).toBeInstanceOf(NotFoundError)
    expect(fromFetch).toMatchObject(tooDeep)
  })

  it('does not read application/jsonl as JSON', async () => {
    await expect(fs('bad', 'jsonl').remove('/a')).resolves.toBeUndefined()
    await expect(new HttpTransport(`${base}/bad/jsonl`, 'k', 5_000).get('/x')).resolves.toBe('{bad')
  })

  it('decodes a text body as before, a leading byte-order mark dropped', async () => {
    await expect(new HttpTransport(`${base}/bomtext/text`, 'k', 5_000).get('/x')).resolves.toBe('hello')
  })
})
