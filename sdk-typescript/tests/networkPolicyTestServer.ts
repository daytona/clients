/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'

export type PlannedResponse = {
  readonly status: number
  readonly body: unknown
  readonly headers?: Readonly<Record<string, string>>
}

export type RecordedRequest = {
  readonly method: string
  readonly path: string
  readonly headers: Readonly<Record<string, string | string[] | undefined>>
  readonly body: string
}

export class FakeApi {
  public readonly requests: RecordedRequest[] = []
  public url = ''
  private readonly server

  constructor(private readonly responses: PlannedResponse[]) {
    this.server = createServer(async (request, response) => {
      const chunks: Buffer[] = []
      for await (const chunk of request) {
        chunks.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk)
      }
      this.requests.push({
        method: request.method ?? 'GET',
        path: request.url ?? '/',
        headers: request.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      })
      const next = this.responses.shift()
      if (!next) {
        response.statusCode = 500
        response.end('unexpected request')
        return
      }
      response.statusCode = next.status
      for (const [key, value] of Object.entries(next.headers ?? {})) response.setHeader(key, value)
      if (!response.hasHeader('content-type')) response.setHeader('content-type', 'application/json')
      response.end(typeof next.body === 'string' ? next.body : JSON.stringify(next.body))
    })
  }

  public async start(): Promise<FakeApi> {
    await new Promise<void>((resolve, reject) => {
      this.server.listen(0, '127.0.0.1', () => resolve())
      this.server.once('error', reject)
    })
    const address = this.server.address()
    if (!address || typeof address === 'string') throw new Error('fake api failed to bind')
    this.url = `http://127.0.0.1:${(address as AddressInfo).port}`
    return this
  }

  public async close(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => error ? reject(error) : resolve())
    })
  }
}

export async function startFakeApi(responses: PlannedResponse[]): Promise<FakeApi> {
  return new FakeApi(responses).start()
}

/**
 * The request at `index`, after asserting that exactly `count` were sent, so an
 * assertion about it (a negative one included) cannot pass because nothing was sent.
 */
export function sentRequest(api: FakeApi, count: number, index = 0): RecordedRequest {
  expect(api.requests).toHaveLength(count)
  const request = api.requests[index]
  if (request === undefined) throw new Error(`no request at index ${index}`)
  return request
}

export function jsonBody(request: RecordedRequest): unknown {
  return JSON.parse(request.body)
}
