/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { once } from 'node:events'
import { createServer } from 'node:http'
import type { Server } from 'node:http'
import { Daytona } from '../src/v2/Daytona'
import { UnprocessableEntityError, UnsupportedOperationError } from '../src/v2/errors/DaytonaError'
import { lazySandbox } from './lazySandbox'

let server: Server
let base: string
let reported: string | undefined
let statusCode: number
let requests: string[]

beforeAll(async () => {
  server = createServer((request, response) => {
    requests.push(request.url ?? '')
    request.resume()
    response.setHeader('Content-Type', 'application/json')
    if (request.method === 'POST') {
      response.statusCode = statusCode
      response.end(JSON.stringify(statusCode === 422
        ? { message: 'unknown field', code: 'UNPROCESSABLE_ENTITY', source: 'DAYTONA_DAEMON' }
        : { message: 'started', status: request.url === '/computeruse/start' ? {} : 'active', desktop: 'ready' }))
      return
    }
    response.end(JSON.stringify({
      status: 'active', desktop: 'ready', environment: reported, resolution: { width: 1920, height: 1080 },
    }))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  base = `http://127.0.0.1:${address.port}`
})

beforeEach(() => {
  requests = []
  reported = undefined
  statusCode = 200
})

afterAll(async () => {
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
})

describe.each(['start', 'restart'] as const)('%s environment read-back', (verb) => {
  describe.each([false, true])('with resolution: %p', (withResolution) => {
    it.each([undefined, 'xfce', 'future-desktop'])('refuses success when the guest reports %p', async (environment) => {
      // Given a guest that accepts the body but does not apply GNOME.
      reported = environment
      const client = new Daytona({ apiKey: 'k' })
      const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: base, accessToken: 't' })
      try {
        // When selecting GNOME, then the existing SDK error refuses success.
        const call = sandbox.computerUse[verb]({
          environment: 'gnome', ...(withResolution ? { resolution: { width: 1920, height: 1080 } } : {}),
        })
        await expect(call).rejects.toBeInstanceOf(UnsupportedOperationError)
        await expect(call).rejects.toMatchObject({ statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
        expect(requests).toEqual([`/computeruse/${verb}`, '/computeruse/status'])
      } finally {
        await client.close()
      }
    })

    it.each(['xfce', 'gnome'] as const)('succeeds when one status confirms %s', async (environment) => {
      // Given a guest that confirms the requested settings.
      reported = environment
      const client = new Daytona({ apiKey: 'k' })
      const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: base, accessToken: 't' })
      try {
        // When selecting the settings.
        const result = await sandbox.computerUse[verb]({
          environment, ...(withResolution ? { resolution: { width: 1920, height: 1080 } } : {}),
        })
        // Then success needs exactly one status request.
        expect(result.message).toBe('started')
        expect(requests).toEqual([`/computeruse/${verb}`, '/computeruse/status'])
      } finally {
        await client.close()
      }
    })

    it('preserves a guest 422 when the environment field is unknown', async () => {
      // Given a Phase-1 guest that refuses the unknown field.
      statusCode = 422
      const client = new Daytona({ apiKey: 'k' })
      const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: base, accessToken: 't' })
      try {
        // When selecting GNOME, then the original daemon refusal surfaces.
        const call = sandbox.computerUse[verb]({
          environment: 'gnome', ...(withResolution ? { resolution: { width: 1920, height: 1080 } } : {}),
        })
        await expect(call).rejects.toBeInstanceOf(UnprocessableEntityError)
        await expect(call).rejects.toMatchObject({ statusCode: 422, code: 'UNPROCESSABLE_ENTITY', source: 'DAYTONA_DAEMON' })
        expect(requests).toEqual([`/computeruse/${verb}`])
      } finally {
        await client.close()
      }
    })
  })
})
