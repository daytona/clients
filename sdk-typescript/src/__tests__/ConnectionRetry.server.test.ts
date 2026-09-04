// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as http from 'http'
import { AddressInfo } from 'net'

import { Daytona } from '../Daytona'
import { DaytonaConnectionError } from '../errors/DaytonaError'

/**
 * Real-socket regression for stale keep-alive handling: the server reads the
 * request, then closes the connection without writing a single response byte.
 * Node reports that as `socket hang up` (ECONNRESET) — the same signature a
 * client sees when a proxy/LB drops a kept-alive connection during a rollout.
 */
function startFlakyServer(dropFirst: number): Promise<{ url: string; seen: () => number; close: () => Promise<void> }> {
  let requests = 0
  const server = http.createServer((req, res) => {
    requests++
    req.on('data', () => undefined)
    req.on('end', () => {
      if (requests <= dropFirst) {
        req.socket.destroy()
        return
      }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ attempt: requests }))
    })
  })
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo
      resolve({
        url: `http://127.0.0.1:${port}`,
        seen: () => requests,
        close: () => new Promise((done) => server.close(() => done())),
      })
    })
  })
}

describe('Daytona.createAxiosInstance against a server that drops connections', () => {
  it('a POST whose connection is closed before any response bytes is retried transparently', async () => {
    const server = await startFlakyServer(1)
    try {
      const axios = Daytona.createAxiosInstance()

      const res = await axios.post(`${server.url}/process/execute`, { command: 'echo hi' })

      expect(res.status).toBe(200)
      expect(res.data).toEqual({ attempt: 2 })
      expect(server.seen()).toBe(2)
    } finally {
      await server.close()
    }
  })

  it('surfaces DaytonaConnectionError once the retry budget is exhausted', async () => {
    const server = await startFlakyServer(Infinity)
    try {
      const axios = Daytona.createAxiosInstance()

      await expect(axios.post(`${server.url}/process/execute`, { command: 'echo hi' })).rejects.toBeInstanceOf(
        DaytonaConnectionError,
      )

      expect(server.seen()).toBe(3)
    } finally {
      await server.close()
    }
  })
})
