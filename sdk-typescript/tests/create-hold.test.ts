/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import type { Server } from 'node:http'
import { SandboxCreateRequestTimeoutError } from '../src/v2/index'
import { clientAt } from './helpers/client'

// A create POST that asks the server to hold is a long poll: a request timeout
// shorter than the hold does not cut it. Python's twin is
// tests/test_wait_deadlines.py.
async function holdingServer(holdMs: number): Promise<{ readonly url: string; readonly server: Server }> {
  const server = createServer((request, response) => {
    request.resume()
    const timer = setTimeout(() => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ id: 'sbx-1', status: 'pending', wait_cap_seconds: 60 }))
    }, holdMs)
    response.on('close', () => clearTimeout(timer))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  return { url: `http://127.0.0.1:${address.port}`, server }
}

async function closeServer(server: Server): Promise<void> {
  server.closeAllConnections()
  server.close()
  await once(server, 'close')
}

it('returns the handle when the create hold outlasts the request timeout', async () => {
  const { url, server } = await holdingServer(2_500)
  const client = clientAt(url, { requestTimeoutSeconds: 1 })
  try {
    const sandbox = await client.v2.create('img_1', { waitSeconds: 3 })
    expect([sandbox.id, sandbox.state]).toEqual(['sbx-1', 'creating'])
  } finally {
    await client.close()
    await closeServer(server)
  }
}, 15_000)

it('keeps a create without a hold an ordinary request', async () => {
  const { url, server } = await holdingServer(2_500)
  const client = clientAt(url, { requestTimeoutSeconds: 1 })
  try {
    await expect(client.v2.create('img_1', { waitSeconds: 0 })).rejects.toBeInstanceOf(SandboxCreateRequestTimeoutError)
  } finally {
    await client.close()
    await closeServer(server)
  }
}, 15_000)
