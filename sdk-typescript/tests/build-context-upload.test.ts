/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import type { IncomingHttpHeaders, Server } from 'node:http'
import { once } from 'node:events'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImagesService } from '../src/v2/Images.js'
import { createHttpClient } from '../src/v2/http.js'
import { contextUploadRuntime } from '../src/v2/contextUpload.js'
import { BadRequestError, ConnectionError, ServiceUnavailableError } from '../src/v2/errors/DaytonaError.js'

const UPLOAD_ID = '0b6d2c8e-8a1f-4c1e-9d7a-3f0e5b2a9c41'
const SIGNATURE = 'X-Amz-Signature=fixture-signature'

interface Seen { method: string; url: string; headers: IncomingHttpHeaders; body: Buffer }

let server: Server
let baseUrl: string
let seen: Seen[]
let partSize: number
let ready: boolean
let putStatuses: number[]

function service(): ImagesService {
  return new ImagesService(createHttpClient(baseUrl, 'fixture-key', 5_000))
}

function json(method: string, url: string): unknown {
  const matches = seen.filter((request) => request.method === method && request.url === url)
  expect(matches).toHaveLength(1)
  return JSON.parse(matches[0].body.toString())
}

const puts = (): Seen[] => seen.filter((request) => request.method === 'PUT')
const completes = (): Seen[] => seen.filter((request) => request.url.endsWith('/complete'))

function archive(size = 0): Uint8Array {
  // Content is opaque to the client; the API verifies the tar.
  return new Uint8Array(Buffer.concat([Buffer.from('FROM alpine\n'), Buffer.alloc(size, 0x78)]))
}

beforeEach(async () => {
  seen = []
  partSize = 4096
  ready = false
  putStatuses = []
  contextUploadRuntime.sleep = async () => {}
  server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', (chunk: Buffer) => chunks.push(chunk))
    request.on('end', () => {
      const body = Buffer.concat(chunks)
      seen.push({ method: request.method ?? '', url: request.url ?? '', headers: request.headers, body })
      const reply = (status: number, payload?: unknown): void => {
        response.statusCode = status
        response.setHeader('Content-Type', 'application/json')
        response.end(payload === undefined ? '' : JSON.stringify(payload))
      }
      const upload = (state: string, declared: { dockerfile: string; bytes: number; sha256: string }, parts: unknown[]) => ({
        id: UPLOAD_ID, state, region_id: 'region-c', dockerfile: declared.dockerfile, bytes: declared.bytes, sha256: declared.sha256, parts,
      })
      if (request.method === 'PUT') return reply(putStatuses.shift() ?? 200)
      if (request.url === '/build-contexts') {
        const declared = JSON.parse(body.toString()) as { dockerfile: string; bytes: number; sha256: string }
        if (ready) return reply(200, upload('ready', declared, []))
        const parts: unknown[] = []
        for (let offset = 0, number = 1; offset < declared.bytes; offset += partSize, number++) {
          parts.push({ part_number: number, bytes: Math.min(partSize, declared.bytes - offset), url: `${baseUrl}/bucket/part/${number}?${SIGNATURE}` })
        }
        return reply(201, upload('uploading', declared, parts))
      }
      if (request.url === `/build-contexts/${UPLOAD_ID}/complete`) return reply(200, upload('ready', { dockerfile: 'Dockerfile', bytes: 1, sha256: '' }, []))
      if (request.url === '/images') return reply(202, { id: 'img-ctx', status: 'pending' })
      if (request.url === '/images/img-ctx') return reply(200, { id: 'img-ctx', status: 'pending', region_id: 'region-c', region_ids: [], origin: 'dockerfile' })
      reply(404, { message: 'not found', code: 'NOT_FOUND', source: 'API' })
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  baseUrl = `http://127.0.0.1:${address.port}`
})

afterEach(async () => {
  server.closeAllConnections()
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
})

it.each(['bytes', 'file'] as const)('uploads a %s context in parts, completes it and builds from it', async (kind) => {
  // Given a context larger than one part, as bytes or as a tar file.
  const data = archive(10_000)
  let context: Uint8Array | string = data
  if (kind === 'file') {
    context = join(await mkdtemp(join(tmpdir(), 'ctx-')), 'ctx.tar')
    await writeFile(context, data)
  }
  // When it is built from the context.
  const image = await service().buildFromContext(context, { dockerfile: 'Dockerfile', name: 'web', region: 'region-c' })
  // Then the declaration names the archive, the parts carry it whole and
  // unauthenticated, the upload is completed and the build names it.
  expect(json('POST', '/build-contexts')).toEqual({
    dockerfile: 'Dockerfile', bytes: data.byteLength, sha256: createHash('sha256').update(data).digest('hex'), region_id: 'region-c',
  })
  expect(puts()).toHaveLength(Math.ceil(data.byteLength / partSize))
  expect(Buffer.concat(puts().map((request) => request.body))).toEqual(Buffer.from(data))
  expect(puts().every((request) => request.headers.authorization === undefined)).toBe(true)
  expect(json('POST', `/build-contexts/${UPLOAD_ID}/complete`)).toEqual({ region_id: 'region-c' })
  expect(json('POST', '/images')).toEqual({ context_upload_id: UPLOAD_ID, region_id: 'region-c', name: 'web' })
  expect(image.id).toBe('img-ctx')
})

it('builds from an already uploaded context without uploading it again', async () => {
  ready = true
  await service().buildFromContext(new Blob([archive() as Uint8Array<ArrayBuffer>]))
  expect(puts()).toHaveLength(0)
  expect(completes()).toHaveLength(0)
  expect(json('POST', '/images')).toMatchObject({ context_upload_id: UPLOAD_ID })
})

it('retries a part after a storage error', async () => {
  partSize = 1 << 20
  putStatuses = [503]
  const data = archive()
  await service().buildFromContext(data)
  expect(puts().map((request) => request.body)).toEqual([Buffer.from(data), Buffer.from(data)])
  expect(completes()).toHaveLength(1)
})

it('fails a refused part without revealing its URL, and completes nothing', async () => {
  // Given storage that refuses a part, as it does once its URL expired.
  partSize = 1 << 20
  putStatuses = [403]
  // When it is built from the context.
  const error = await service().buildFromContext(archive()).catch((caught: unknown) => caught)
  // Then the error names the part but not the URL or its signature.
  expect(error).toBeInstanceOf(BadRequestError)
  const text = `${String(error)} ${JSON.stringify(error)} ${String((error as { cause?: unknown }).cause)}`
  expect(text).toContain('part 1')
  expect(text).not.toContain(SIGNATURE)
  expect(text).not.toContain('/bucket/')
  expect(completes()).toHaveLength(0)
  expect(seen.some((request) => request.url === '/images')).toBe(false)
})

it('reports storage that keeps failing as unavailable', async () => {
  partSize = 1 << 20
  putStatuses = [500, 502, 503]
  await expect(service().buildFromContext(archive())).rejects.toBeInstanceOf(ServiceUnavailableError)
  expect(puts()).toHaveLength(3)
})

it('reports unreachable storage as a connection error without its URL', async () => {
  const original = contextUploadRuntime.fetch
  contextUploadRuntime.fetch = async () => { throw new TypeError(`fetch failed for ${baseUrl}/bucket/part/1?${SIGNATURE}`) }
  try {
    partSize = 1 << 20
    const error = await service().buildFromContext(archive()).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ConnectionError)
    expect(`${String(error)} ${String((error as { cause?: unknown }).cause)}`).not.toContain(SIGNATURE)
  } finally {
    contextUploadRuntime.fetch = original
  }
})

it.each([
  { context: new Uint8Array(), message: 'build context is empty' },
  { context: '/nonexistent/ctx.tar', message: 'is not a file' },
])('refuses an unusable context before any request ($message)', async ({ context, message }) => {
  await expect(service().buildFromContext(context)).rejects.toThrow(message)
  expect(seen).toHaveLength(0)
})

it.each(['bytes', 'blob', 'file'] as const)('sends each %s part with its own length, not chunked', async (kind) => {
  const data = archive(10_000)
  let context: Uint8Array | Blob | string = data
  if (kind === 'blob') context = new Blob([data as Uint8Array<ArrayBuffer>])
  if (kind === 'file') {
    context = join(await mkdtemp(join(tmpdir(), 'ctx-')), 'ctx.tar')
    await writeFile(context, data)
  }
  await service().buildFromContext(context)
  expect(puts().length).toBeGreaterThan(1)
  expect(puts().every((request) => request.headers['content-length'] === String(request.body.byteLength))).toBe(true)
  expect(puts().every((request) => request.headers['transfer-encoding'] === undefined)).toBe(true)
  expect(Buffer.concat(puts().map((request) => request.body))).toEqual(Buffer.from(data))
})

it('refuses a file that changes while it is being uploaded', async () => {
  // Given a context file rewritten after it was declared.
  const path = join(await mkdtemp(join(tmpdir(), 'ctx-')), 'ctx.tar')
  await writeFile(path, archive(10_000))
  const original = contextUploadRuntime.fetch
  contextUploadRuntime.fetch = async (input, init) => {
    await writeFile(path, 'short')
    return original(input, init)
  }
  try {
    // When it is built from the context, it fails as a changed context.
    const error = await service().buildFromContext(path).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(String(error)).toContain('changed while it was being uploaded')
    expect(String(error)).not.toContain(SIGNATURE)
    expect(completes()).toHaveLength(0)
  } finally {
    contextUploadRuntime.fetch = original
  }
})
