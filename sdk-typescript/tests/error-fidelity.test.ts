/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Error responses always carry a message, the same as in the Python SDK
// (sdk-python/tests/test_error_fidelity.py), and a request that got no
// response keeps the runtime's network error as its cause, never the HTTP
// client's error, which carries the request headers.

import { createServer } from 'node:http'
import type { Server } from 'node:http'
import { once } from 'node:events'
import { inspect } from 'node:util'
import { ConnectionError, DaytonaError, NotFoundError } from '../src/v2/errors/DaytonaError'
import { HttpTransport, createPlatformTokenProvider } from '../src/v2/http'
import { forceFetchAdapterForTests } from '../src/v2/boundedAdapter'

const KEY = 'dtn_secret_key_value'

interface Planned {
  readonly status: number
  readonly body: string
  readonly type?: string
}

async function serve(planned: Planned): Promise<{ readonly url: string; readonly close: () => Promise<void> }> {
  const server: Server = createServer((request, response) => {
    request.resume()
    response.writeHead(planned.status, planned.type === undefined ? {} : { 'Content-Type': planned.type })
    response.end(planned.body)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: async () => {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

/** A port nothing listens on. */
async function refusedUrl(): Promise<string> {
  const { url, close } = await serve({ status: 200, body: '' })
  await close()
  return url
}

const JSON_TYPE = 'application/json'
const TEXT_TYPE = 'text/plain'

// [description, status, body, content type, message]
const MESSAGES: ReadonlyArray<readonly [string, number, string, string | undefined, string]> = [
  ['an empty JSON body', 503, '', JSON_TYPE, 'HTTP 503'],
  ['an empty text body', 500, '', TEXT_TYPE, 'HTTP 500'],
  ['an empty body with no content type', 502, '', undefined, 'HTTP 502'],
  ['a blank text body', 502, '  \n', TEXT_TYPE, 'HTTP 502'],
  ['a JSON null body', 400, 'null', JSON_TYPE, 'null'],
  ['a JSON array body', 400, '[1,2]', JSON_TYPE, '[1,2]'],
  ['an envelope without prose', 400, '{}', JSON_TYPE, 'HTTP 400'],
  ['an envelope with an empty message', 400, '{"message":""}', JSON_TYPE, 'HTTP 400'],
  ['an empty message and an error', 400, '{"message":"","error":"bad input"}', JSON_TYPE, 'bad input'],
  ['an envelope message', 404, '{"message":"no such sandbox","code":"NOT_FOUND"}', JSON_TYPE, 'no such sandbox'],
  ['a text body', 400, 'plain words', TEXT_TYPE, 'plain words'],
]

describe('error response messages', () => {
  it.each(MESSAGES)('%s through axios', async (_name, status, body, type, message) => {
    const server = await serve({ status, body, type })
    try {
      const error = await new HttpTransport(server.url, KEY, 5_000).get('/x').then(() => undefined, (caught: unknown) => caught)
      expect(error).toBeInstanceOf(DaytonaError)
      expect(error).toMatchObject({ message, statusCode: status })
    } finally {
      await server.close()
    }
  })

  it.each(MESSAGES)('%s through the SDK fetch adapter', async (_name, status, body, type, message) => {
    const server = await serve({ status, body, type })
    forceFetchAdapterForTests(true)
    try {
      const error = await new HttpTransport(server.url, KEY, 5_000).get('/x').then(() => undefined, (caught: unknown) => caught)
      expect(error).toMatchObject({ message, statusCode: status })
    } finally {
      forceFetchAdapterForTests(false)
      await server.close()
    }
  })

  it.each(MESSAGES)('%s through fetch', async (_name, status, body, type, message) => {
    const server = await serve({ status, body, type })
    try {
      const error = await new HttpTransport(server.url, KEY, 5_000).fetch('/x').then(() => undefined, (caught: unknown) => caught)
      expect(error).toMatchObject({ message, statusCode: status })
    } finally {
      await server.close()
    }
  })

  it('keeps the code of an envelope whose message is empty', async () => {
    const server = await serve({ status: 404, body: '{"message":"","code":"NOT_FOUND"}', type: JSON_TYPE })
    try {
      const error = await new HttpTransport(server.url, KEY, 5_000).get('/x').then(() => undefined, (caught: unknown) => caught)
      expect(error).toBeInstanceOf(NotFoundError)
      expect(error).toMatchObject({ message: 'HTTP 404', code: 'NOT_FOUND' })
    } finally {
      await server.close()
    }
  })
})

function expectSafeNetworkCause(error: unknown): void {
  expect(error).toBeInstanceOf(ConnectionError)
  const cause = (error as ConnectionError).cause
  // An Error, though possibly from Node's own realm rather than the test's.
  expect(Object.prototype.toString.call(cause)).toBe('[object Error]')
  // The runtime's own error, not the HTTP client's (which holds the request).
  expect(cause).not.toHaveProperty('config')
  expect(cause).not.toHaveProperty('isAxiosError')
  expect(inspect(error, { depth: 10 })).not.toContain(KEY)
  expect(inspect(cause, { depth: 10, showHidden: true })).toMatch(/ECONNREFUSED/)
}

describe('connection failures keep a safe cause', () => {
  it('through axios', async () => {
    const url = await refusedUrl()
    expectSafeNetworkCause(await new HttpTransport(url, KEY, 5_000).get('/x').then(() => undefined, (caught: unknown) => caught))
  })

  it('through the SDK fetch adapter', async () => {
    const url = await refusedUrl()
    forceFetchAdapterForTests(true)
    try {
      expectSafeNetworkCause(await new HttpTransport(url, KEY, 5_000).get('/x').then(() => undefined, (caught: unknown) => caught))
    } finally {
      forceFetchAdapterForTests(false)
    }
  })

  it('through fetch', async () => {
    const url = await refusedUrl()
    expectSafeNetworkCause(await new HttpTransport(url, KEY, 5_000).fetch('/x').then(() => undefined, (caught: unknown) => caught))
  })

  it('through the project token exchange', async () => {
    const url = await refusedUrl()
    const provider = createPlatformTokenProvider({ baseURL: url, jwtToken: KEY, projectId: 'project', requestTimeout: 5_000 })
    expectSafeNetworkCause(await provider.authorizationHeader().then(() => undefined, (caught: unknown) => caught))
  })
})
