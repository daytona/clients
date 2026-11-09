/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import type { Server } from 'node:http'
import { RequestTimeoutError } from '../src/v2/index'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'

// Artifact waits (images.get / snapshots.get with wait: 'ready'): a budget that
// runs out before the artifact settles throws RequestTimeoutError (SDK), never
// an unsettled observation; a settled artifact is returned; no budget is one
// hold whose answer is returned; a budget above the timer maximum waits
// without a deadline. The Python twin is tests/test_artifact_waits.py.
type Served = { readonly url: string; readonly server: Server; readonly reads: () => number; readonly askedHolds: () => readonly (string | null)[] }

/**
 * A server answering each read with the next status after `holdMs`, a delay
 * the test fixes (at most 5 s); the hold each request asked for
 * (`timeout_ms`) is only recorded.
 */
async function serve(statuses: readonly string[], holdMs: 0 | 1_000): Promise<Served> {
  let reads = 0
  const asked: (string | null)[] = []
  const server = createServer((request, response) => {
    request.resume()
    const status = statuses[Math.min(reads, statuses.length - 1)] ?? 'building'
    reads++
    asked.push(new URL(request.url ?? '/', 'http://x').searchParams.get('timeout_ms'))
    const timer = setTimeout(() => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(imageWire({ id: 'img', status })))
    }, holdMs)
    response.on('close', () => clearTimeout(timer))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  return { url: `http://127.0.0.1:${address.port}`, server, reads: () => reads, askedHolds: () => asked }
}

async function within<T>(url: string, server: Server, call: (client: ReturnType<typeof clientAt>) => Promise<T>): Promise<T> {
  const client = clientAt(url, { requestTimeoutSeconds: 30 })
  try {
    return await call(client)
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}

it('throws RequestTimeoutError when the budget is spent on a building image', async () => {
  // The server answers "building" at the end of a hold as long as the budget.
  const { url, server, askedHolds } = await serve(['building'], 1_000)
  const started = performance.now()
  const error = await within(url, server, (client) => client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 1 }).then(() => undefined, (failure: unknown) => failure))
  expect(error).toBeInstanceOf(RequestTimeoutError)
  expect(error).toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
  expect(performance.now() - started).toBeLessThan(3_000)
  // The read asked the server to hold for at most the 1 s budget.
  expect(Number(askedHolds()[0])).toBeGreaterThan(0)
  expect(Number(askedHolds()[0])).toBeLessThanOrEqual(1_000)
}, 10_000)

it('never returns the first answer of a wait that spans two holds', async () => {
  // Answered early (paced one second apart): a 2.5 s budget spans several holds.
  const { url, server, reads } = await serve(['building'], 0)
  const error = await within(url, server, (client) => client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 2.5 }).then(() => undefined, (failure: unknown) => failure))
  expect(error).toBeInstanceOf(RequestTimeoutError)
  expect(reads()).toBeGreaterThanOrEqual(2)
}, 10_000)

it('returns an image that becomes ready within the budget', async () => {
  const { url, server } = await serve(['ready'], 0)
  const image = await within(url, server, (client) => client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 5 }))
  expect(image.status).toBe('ready')
})

it("returns the server's answer when there is no budget", async () => {
  const { url, server } = await serve(['building'], 0)
  const image = await within(url, server, (client) => client.v2.images.get('img', { wait: 'ready' }))
  expect(image.status).toBe('building')
})

it('keeps waiting with a budget above the timer maximum', async () => {
  const { url, server, reads } = await serve(['pending'], 0)
  const client = clientAt(url, { requestTimeoutSeconds: 30 })
  try {
    const outcome = await Promise.race([
      client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 3_000_000 }).then(() => 'returned', () => 'threw'),
      new Promise((resolve) => setTimeout(() => resolve('still waiting'), 2_500)),
    ])
    expect(outcome).toBe('still waiting')
    expect(reads()).toBeGreaterThanOrEqual(3)
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}, 10_000)

it('accepts a valid timeoutSeconds without wait and leaves it unused', async () => {
  const { url, server } = await serve(['building'], 0)
  const image = await within(url, server, (client) => client.v2.images.get('img', { timeoutSeconds: 5 }))
  expect(image.status).toBe('building')
})
