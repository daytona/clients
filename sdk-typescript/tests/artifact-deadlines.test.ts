/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import type { Server } from 'node:http'
import { ImageBuilder, RequestTimeoutError } from '../src/v2/index'
import { imageWire, snapshotWire } from './artifactFixtures'
import { clientAt } from './helpers/client'

// Each hold's read is capped at the wait's deadline, not at the hold plus its
// slack, as the sandbox waits are. Python's twin is tests/test_wait_deadlines.py.
async function stallingArtifacts(): Promise<{ readonly url: string; readonly server: Server }> {
  const server = createServer((request, response) => {
    request.resume()
    const reply = (body: unknown) => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(body))
    }
    if (request.method === 'POST') return reply({ id: 'img', status: 'pending' })
    if (request.url?.includes('wait=')) return // held: never answered
    reply(request.url?.startsWith('/snapshots') ? snapshotWire({ id: 'img', status: 'building' }) : imageWire({ id: 'img', status: 'building' }))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  return { url: `http://127.0.0.1:${address.port}`, server }
}

async function elapsedRejection(call: () => Promise<unknown>): Promise<{ readonly error: unknown; readonly seconds: number }> {
  const started = performance.now()
  const error = await call().then(() => undefined, (failure: unknown) => failure)
  return { error, seconds: (performance.now() - started) / 1000 }
}

it.each(['images', 'snapshots'] as const)('ends a stalled %s ready wait at its budget', async (kind) => {
  const { url, server } = await stallingArtifacts()
  const client = clientAt(url, { requestTimeoutSeconds: 30 })
  try {
    const service = kind === 'images' ? client.v2.images : client.v2.snapshots
    const { error, seconds } = await elapsedRejection(() => service.get('img', { wait: 'ready', timeoutSeconds: 1 }))
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
    expect(seconds).toBeGreaterThanOrEqual(0.9)
    expect(seconds).toBeLessThan(3)
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}, 20_000)

it('ends a stalled build wait at buildTimeoutSeconds', async () => {
  const { url, server } = await stallingArtifacts()
  const client = clientAt(url, { requestTimeoutSeconds: 30 })
  try {
    const { error, seconds } = await elapsedRejection(() => client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { buildTimeoutSeconds: 1 }))
    expect(error).toMatchObject({ constructor: RequestTimeoutError, message: 'image build did not finish before timeout', source: 'SDK' })
    expect(seconds).toBeGreaterThanOrEqual(0.9)
    expect(seconds).toBeLessThan(3)
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}, 20_000)

it('refuses a null artifact read with BadRequestError before reading its status', async () => {
  const server = createServer((request, response) => {
    request.resume()
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end('null')
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  const client = clientAt(`http://127.0.0.1:${address.port}`)
  try {
    await expect(client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 5 })).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'image response is not a JSON object',
    })
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
})

it.each([10n, (() => { const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic; return cyclic })()])('refuses an invalid wait value %# with BadRequestError', async (wait) => {
  const client = clientAt('http://127.0.0.1:9')
  try {
    // SAFETY: deliberately invalid plain-JavaScript values.
    await expect(client.v2.images.get('img', { wait: wait as never })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  } finally {
    await client.close()
  }
})
