/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import type { IncomingHttpHeaders, Server } from 'node:http'
import { once } from 'node:events'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImagesService } from '../src/v2/Images.js'
import { createHttpClient } from '../src/v2/http.js'
import { contextUploadRuntime } from '../src/v2/contextUpload.js'
import { Sha256 } from '../src/v2/sha256.js'
import { BadRequestError, ConflictError, ConnectionError, DaytonaError, ServiceUnavailableError } from '../src/v2/errors/DaytonaError.js'

const UPLOAD_ID = '0b6d2c8e-8a1f-4c1e-9d7a-3f0e5b2a9c41'
const SIGNATURE = 'X-Amz-Signature=fixture-signature'

interface Seen { method: string; url: string; headers: IncomingHttpHeaders; body: Buffer }

let server: Server
let baseUrl: string
let seen: Seen[]
let partSize: number
let ready: boolean
let putStatuses: number[]
let partStatuses: Map<number, number[]>
let onPut: ((part: number) => Promise<void>) | undefined
let rewriteParts: ((parts: Array<Record<string, unknown>>) => Array<Record<string, unknown>>) | undefined
let completeStatuses: number[]
let completeDelays: number[]
const defaultCompleteTimeoutMs = contextUploadRuntime.completeTimeoutMs
const defaultIsNode = contextUploadRuntime.isNode
const tempDirs: string[] = []

async function tempDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'ctx-'))
  tempDirs.push(dir)
  return dir
}

function service(requestTimeoutMs = 5_000): ImagesService {
  return new ImagesService(createHttpClient(baseUrl, 'fixture-key', requestTimeoutMs))
}

const partNumber = (url: string): number => Number(url.split('?')[0].split('/').pop())
const delay = (ms: number): Promise<void> => new Promise((resolve) => { setTimeout(resolve, ms).unref() })

function json(method: string, url: string): unknown {
  const matches = seen.filter((request) => request.method === method && request.url === url)
  expect(matches).toHaveLength(1)
  return JSON.parse(matches[0].body.toString())
}

const puts = (): Seen[] => seen.filter((request) => request.method === 'PUT')
const inPartOrder = (requests: Seen[]): Buffer => Buffer.concat([...requests].sort((a, b) => partNumber(a.url) - partNumber(b.url)).map((request) => request.body))
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
  partStatuses = new Map()
  onPut = undefined
  rewriteParts = undefined
  completeStatuses = []
  completeDelays = []
  contextUploadRuntime.sleep = async () => {}
  contextUploadRuntime.completeTimeoutMs = defaultCompleteTimeoutMs
  contextUploadRuntime.isNode = defaultIsNode
  server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', (chunk: Buffer) => chunks.push(chunk))
    request.on('end', async () => {
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
      if (request.method === 'PUT') {
        const part = partNumber(request.url ?? '')
        await onPut?.(part)
        return reply((partStatuses.get(part) ?? putStatuses).shift() ?? 200)
      }
      if (request.url === '/build-contexts') {
        const declared = JSON.parse(body.toString()) as { dockerfile: string; bytes: number; sha256: string }
        if (ready) return reply(200, upload('ready', declared, []))
        const parts: Array<Record<string, unknown>> = []
        for (let offset = 0, number = 1; offset < declared.bytes; offset += partSize, number++) {
          parts.push({ part_number: number, bytes: Math.min(partSize, declared.bytes - offset), url: `${baseUrl}/bucket/part/${number}?${SIGNATURE}` })
        }
        return reply(201, upload('uploading', declared, rewriteParts?.(parts) ?? parts))
      }
      if (request.url === `/build-contexts/${UPLOAD_ID}/complete`) {
        const status = completeStatuses.shift() ?? 200
        await delay(completeDelays.shift() ?? 0)
        if (response.destroyed) return
        if (status !== 200) return reply(status, { message: 'not now', code: 'UNAVAILABLE', source: 'API' })
        return reply(200, upload('ready', { dockerfile: 'Dockerfile', bytes: 1, sha256: '' }, []))
      }
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
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
  server.closeAllConnections()
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
})

it.each(['bytes', 'file'] as const)('uploads a %s context in parts, completes it and builds from it', async (kind) => {
  // Given a context larger than one part, as bytes or as a tar file.
  const data = archive(10_000)
  let context: Uint8Array | string = data
  if (kind === 'file') {
    context = join(await tempDir(), 'ctx.tar')
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
  expect(inPartOrder(puts())).toEqual(Buffer.from(data))
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

it.each(['bytes', 'file'] as const)('retries a %s part after a storage error', async (kind) => {
  partSize = 1 << 20
  putStatuses = [503]
  const data = archive()
  let context: Uint8Array | string = data
  if (kind === 'file') {
    context = join(await tempDir(), 'ctx.tar')
    await writeFile(context, data)
  }
  await service().buildFromContext(context)
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

it.each([
  ['out of order', (parts: Array<Record<string, unknown>>) => [parts[1], parts[0]]],
  ['with an empty part', (parts: Array<Record<string, unknown>>) => [...parts, { ...parts[parts.length - 1], part_number: parts.length + 1, bytes: 0 }]],
])('refuses parts %s before uploading any', async (_, rewrite) => {
  rewriteParts = rewrite
  const error = await service().buildFromContext(archive(10_000)).catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(DaytonaError)
  expect(String(error)).toContain('do not cover the archive')
  expect(puts()).toHaveLength(0)
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
    context = join(await tempDir(), 'ctx.tar')
    await writeFile(context, data)
  }
  await service().buildFromContext(context)
  expect(puts().length).toBeGreaterThan(1)
  expect(puts().every((request) => request.headers['content-length'] === String(request.body.byteLength))).toBe(true)
  expect(puts().every((request) => request.headers['transfer-encoding'] === undefined)).toBe(true)
  expect(inPartOrder(puts())).toEqual(Buffer.from(data))
})

it('refuses a file that changes while it is being uploaded', async () => {
  // Given a context file rewritten after it was declared, with parts longer
  // than the one chunk a part's stream reads before it is sent.
  partSize = 3 << 20
  const path = join(await tempDir(), 'ctx.tar')
  await writeFile(path, archive(5 << 20))
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

it.each(['bytes', 'blob', 'file'] as const)('uploads %s parts in parallel, up to the limit', async (kind) => {
  // Given storage that holds each PUT until four are in flight.
  const limit = 4
  let inFlight = 0
  let peak = 0
  let release!: () => void
  const full = new Promise<void>((resolve) => { release = resolve })
  onPut = async () => {
    inFlight++
    peak = Math.max(peak, inFlight)
    if (inFlight === limit) release()
    await Promise.race([full, delay(5_000)])
    inFlight--
  }
  partSize = 1024
  const data = archive(10_000)
  let context: Uint8Array | Blob | string = data
  if (kind === 'blob') context = new Blob([data as Uint8Array<ArrayBuffer>])
  if (kind === 'file') {
    context = join(await tempDir(), 'ctx.tar')
    await writeFile(context, data)
  }
  // When a context of more parts than the limit is uploaded.
  await service().buildFromContext(context)
  // Then exactly the limit was in flight, each part carries its own slice,
  // and the upload is completed once.
  expect(peak).toBe(limit)
  expect(puts().length).toBeGreaterThan(limit)
  for (const request of puts()) {
    const number = partNumber(request.url)
    expect(request.body).toEqual(Buffer.from(data.subarray((number - 1) * partSize, number * partSize)))
  }
  expect(completes()).toHaveLength(1)
})

it('throws the refusal of one part among parallel parts as itself', async () => {
  partSize = 1024
  partStatuses = new Map([[2, [403]]])
  const error = await service().buildFromContext(archive(10_000)).catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(BadRequestError)
  expect(String(error)).toContain('part 2 ')
  expect(String(error)).not.toContain(SIGNATURE)
  expect(completes()).toHaveLength(0)
})

it('retries a complete the server failed', async () => {
  completeStatuses = [503, 502]
  const image = await service().buildFromContext(archive())
  expect(completes()).toHaveLength(3)
  expect(image.id).toBe('img-ctx')
})

it('gives up on a complete that keeps failing', async () => {
  completeStatuses = [500, 500, 500]
  const error = await service().buildFromContext(archive()).catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(DaytonaError)
  expect((error as DaytonaError).statusCode).toBe(500)
  expect(completes()).toHaveLength(3)
  expect(seen.some((request) => request.url === '/images')).toBe(false)
})

it('does not retry a complete the server refused', async () => {
  completeStatuses = [409]
  await expect(service().buildFromContext(archive())).rejects.toBeInstanceOf(ConflictError)
  expect(completes()).toHaveLength(1)
})

it('waits for a complete longer than the request timeout', async () => {
  contextUploadRuntime.completeTimeoutMs = 5_000
  completeDelays = [500]
  await service(200).buildFromContext(archive())
  expect(completes()).toHaveLength(1)
})

it('retries a complete that timed out', async () => {
  contextUploadRuntime.completeTimeoutMs = 300
  completeDelays = [1_000]
  const image = await service(100).buildFromContext(archive())
  expect(completes()).toHaveLength(2)
  expect(image.id).toBe('img-ctx')
})

it('writes no part URL to the console or the standard streams', async () => {
  const written: string[] = []
  const capture = (...args: unknown[]): boolean => { written.push(args.map(String).join(' ')); return true }
  const spies = [
    ...(['log', 'info', 'warn', 'error', 'debug'] as const).map((method) => jest.spyOn(console, method).mockImplementation(capture)),
    jest.spyOn(process.stdout, 'write').mockImplementation(capture),
    jest.spyOn(process.stderr, 'write').mockImplementation(capture),
  ]
  try {
    partSize = 1024
    putStatuses = [503]
    await service().buildFromContext(archive(10_000))
  } finally {
    for (const spy of spies) spy.mockRestore()
  }
  expect(written.filter((line) => line.includes(SIGNATURE) || line.includes('/bucket/'))).toEqual([])
})

it('refuses a name with an idle expiry before any request', async () => {
  await expect(service().buildFromContext(archive(), { name: 'web', idleExpirySeconds: 60 })).rejects.toThrow(
    new BadRequestError('idleExpirySeconds applies only to an anonymous image; omit name or idleExpirySeconds', 400, 'BAD_REQUEST', 'SDK'),
  )
  expect(seen).toEqual([])
})

it('hashes incrementally to the same digest as node:crypto', () => {
  const data = new Uint8Array(200_003).map((_, i) => (i * 31 + 7) & 0xff)
  for (const size of [0, 1, 55, 56, 63, 64, 65, 119, 120, 128, 1000, data.byteLength]) {
    const slice = data.subarray(0, size)
    const expected = createHash('sha256').update(slice).digest('hex')
    expect(new Sha256().update(slice).digestHex()).toBe(expected)
    const split = new Sha256()
    for (let offset = 0; offset < size; offset += 37) split.update(slice.subarray(offset, offset + 37))
    expect(split.digestHex()).toBe(expected)
  }
})

it('declares the digest of a Blob without node:crypto', async () => {
  // Given a runtime that is not Node.js, such as a browser.
  const data = archive(10_000)
  contextUploadRuntime.isNode = () => false
  // When a Blob context is built, it is hashed incrementally and declared.
  await service().buildFromContext(new Blob([Buffer.from(data)]))
  expect(json('POST', '/build-contexts')).toMatchObject({ sha256: createHash('sha256').update(data).digest('hex') })
  expect(inPartOrder(puts())).toEqual(Buffer.from(data))
})
