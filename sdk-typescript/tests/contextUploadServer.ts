/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { once } from 'node:events'
import { createServer } from 'node:http'
import type { IncomingHttpHeaders } from 'node:http'
import { ImagesService } from '../src/v2/Images.js'
import { createHttpClient } from '../src/v2/http.js'
import { INTERNAL } from '../src/v2/internal.js'
import { checkResponse } from '../src/v2/responseFields.js'
import type * as api from '../src/v2/gen/api-types.js'
import { imageWire } from './artifactFixtures.js'

export const CONTEXT_ID = '0b6d2c8e-8a1f-4c1e-9d7a-3f0e5b2a9c41'

interface SeenRequest {
  readonly method: string
  readonly url: string
  readonly headers: IncomingHttpHeaders
  readonly body: Buffer
}

interface ServerControls { failure?: 'declare' | 'put' | 'complete' | 'image' }
interface FixtureError { readonly message: string; readonly code: string; readonly source: string }
type FixtureReply = api.BuildContextUpload | api.CreateImageResponse | api.CreateSandboxResponse | api.ImageListItem | FixtureError

function declaration(body: Buffer): api.CreateBuildContextUploadRequest {
  return checkResponse<api.CreateBuildContextUploadRequest>(JSON.parse(body.toString()), {
    dockerfile: 'string', bytes: 'integer', sha256: 'string', region_id: 'string?',
  }, 'build context declaration')
}

export async function contextUploadServer() {
  const seen: SeenRequest[] = []
  const controls: ServerControls = {}
  let baseUrl = ''
  const server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', (chunk: Buffer) => chunks.push(chunk))
    request.on('end', () => {
      const body = Buffer.concat(chunks)
      const url = request.url ?? ''
      seen.push({ method: request.method ?? '', url, headers: request.headers, body })
      const reply = (status: number, payload: FixtureReply): void => {
        response.writeHead(status, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify(payload))
      }
      const refusal = (): void => reply(409, { message: 'fixture refusal', code: 'CONFLICT', source: 'API' })
      if (request.method === 'PUT') {
        if (controls.failure === 'put') return refusal()
        response.writeHead(200)
        return response.end()
      }
      if (url === '/build-contexts') {
        if (controls.failure === 'declare') return refusal()
        const declared = declaration(body)
        return reply(201, {
          ...declared, dockerfile: declared.dockerfile ?? 'Dockerfile', id: CONTEXT_ID, state: 'uploading', region_id: 'region-c',
          parts: [{ part_number: 1, bytes: declared.bytes, url: `${baseUrl}/storage/1?signature=fixture` }],
        })
      }
      if (url === `/build-contexts/${CONTEXT_ID}/complete`) {
        if (controls.failure === 'complete') return refusal()
        return reply(200, { id: CONTEXT_ID, state: 'ready', region_id: 'region-c', dockerfile: 'Dockerfile', bytes: 1, sha256: '', parts: [] })
      }
      if (url === '/images') {
        if (controls.failure === 'image') return refusal()
        return reply(202, { id: 'img-local', status: 'pending' })
      }
      if (url.startsWith('/images/img-local')) return reply(200, imageWire({ id: 'img-local', region_id: 'region-c', origin: 'dockerfile' }))
      if (url.startsWith('/sandboxes') && request.method === 'POST') {
        return reply(200, { id: 'sbx-local', status: 'pending', wait_cap_seconds: 60 })
      }
      reply(404, { message: 'not found', code: 'NOT_FOUND', source: 'API' })
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  // oxlint-disable-next-line anti-slop/no-runtime-typeof -- Node's server.address() distinguishes TCP addresses from pipe names by representation.
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  baseUrl = `http://127.0.0.1:${address.port}`
  return {
    baseUrl, seen, controls,
    images: new ImagesService(INTERNAL, createHttpClient(baseUrl, 'fixture-key', 5_000)),
    requests: (method: string, path: string) => seen.filter((request) => request.method === method && request.url.split('?')[0] === path),
    declarations: () => seen.filter((request) => request.method === 'POST' && request.url === '/build-contexts').map((request) => declaration(request.body)),
    // oxlint-disable-next-line anti-slop/no-unknown-returns -- Wire-level assertions must inspect unexpected request keys as well as known fields.
    json: (method: string, path: string): unknown => {
      const requests = seen.filter((request) => request.method === method && request.url.split('?')[0] === path)
      expect(requests).toHaveLength(1)
      return JSON.parse(requests[0].body.toString())
    },
    uploads: (): readonly SeenRequest[] => seen.filter((request) => request.method === 'PUT'),
    close: async (): Promise<void> => {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
    },
  }
}
