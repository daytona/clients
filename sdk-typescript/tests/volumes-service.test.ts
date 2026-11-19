/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer, type Server } from 'node:http'
import { VolumesService } from '../src/v2/Volume.js'
import { INTERNAL } from '../src/v2/internal'
import { createHttpClient } from '../src/v2/http.js'
import { NotFoundError } from '../src/v2/errors/DaytonaError.js'

const volume = {
  id: 'vol_1', name: 'data', type: 'disk', status: 'ready',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
}

describe('platform volumes', () => {
  let server: Server
  let service: VolumesService
  let requests: { method?: string; path?: string; body?: unknown }[]
  let responses: { status: number; body: object }[]

  beforeEach(async () => {
    requests = []
    responses = []
    server = createServer((request, response) => {
      let body = ''
      request.setEncoding('utf8')
      request.on('data', (chunk: string) => { body += chunk })
      request.on('end', () => {
        requests.push({ method: request.method, path: request.url, body: body ? JSON.parse(body) : undefined })
        const next = responses.shift() ?? { status: 500, body: { message: 'unexpected request' } }
        response.writeHead(next.status, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify(next.body))
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('missing TCP listener')
    service = new VolumesService(INTERNAL, createHttpClient(`http://127.0.0.1:${address.port}`, 'key'))
  })

  afterEach(async () => {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  })

  it('creates a volume of the server-chosen type when an optional name lookup returns 404', async () => {
    // Given
    responses.push({ status: 404, body: { code: 'NOT_FOUND', message: 'absent' } }, { status: 201, body: volume })
    // When
    const result = await service.get('data/name', { create: true })
    // Then
    expect(result.id).toBe('vol_1')
    expect(requests).toEqual([
      { method: 'GET', path: '/volumes/data%2Fname', body: undefined },
      { method: 'POST', path: '/volumes', body: { name: 'data/name' } },
    ])
  })

  it('refuses the former positional create flag instead of ignoring it', async () => {
    // Given the 0.x-style call get(name, true).
    // SAFETY: a plain-JS caller can still pass the old positional boolean.
    const pending = service.get('data', true as never)
    // Then it names the options form and sends nothing, rather than a lookup that 404s.
    await expect(pending).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'volumes.get takes options; pass { create: true }',
    })
    expect(requests).toEqual([])
  })

  it('keeps a missing ID missing when create and idOnly are both requested', async () => {
    // Given
    responses.push({ status: 404, body: { code: 'NOT_FOUND', message: 'absent' } })
    // When
    const result = service.get('vol_missing', { create: true, idOnly: true })
    // Then
    await expect(result).rejects.toBeInstanceOf(NotFoundError)
    expect(requests).toEqual([{ method: 'GET', path: '/volumes/vol_missing?id_only=true', body: undefined }])
  })

  it('sends no type when the caller names none, so the server chooses it', async () => {
    // Given
    responses.push({ status: 201, body: { ...volume, type: 'bucket' } })
    // When
    const result = await service.create({ name: 'data' })
    // Then
    expect([result.type, result.rawType]).toEqual(['bucket', 'bucket'])
    expect(requests).toEqual([{ method: 'POST', path: '/volumes', body: { name: 'data' } }])
  })

  it.each(['disk', 'bucket'] as const)('sends an explicit %s type as given', async (type) => {
    // Given
    responses.push({ status: 201, body: { ...volume, type, size_gib: 8 } })
    // When
    const result = await service.create({ name: 'data', sizeGib: 8, type })
    // Then
    expect(result.type).toBe(type)
    expect(requests).toEqual([{ method: 'POST', path: '/volumes', body: { name: 'data', type, size_gib: 8 } }])
  })

  it.each(['', 'DISK', 'unknown', 'nfs', 1, ['disk'], null])('refuses type %j before any request', async (type) => {
    // SAFETY: a plain-JS caller can pass any value.
    // An invalid size too: the type is checked first, as in Python.
    const pending = service.create({ name: 'data', sizeGib: 0, type: type as never })
    await expect(pending).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'type must be one of disk, bucket',
    })
    expect(requests).toEqual([])
  })

  it('preserves nested JSON options while normalizing nullable metadata and unknown enums', async () => {
    // Given
    const options = { bucket: 'data', nested: { flags: [true, null, 7, { mode: 'read' }] } }
    responses.push({ status: 200, body: { ...volume, type: 'future_type', status: 'future_status', options,
      size_gib: null, error: null, last_used_at: null, used_bytes: 0, used_bytes_at: null, future_field: true } })
    // When
    const result = await service.get('vol_1')
    // Then
    expect(result).toEqual({ id: 'vol_1', name: 'data', type: 'unknown', rawType: 'future_type',
      status: 'unknown', rawStatus: 'future_status', createdAt: volume.created_at, updatedAt: volume.updated_at,
      options, sizeGib: undefined, error: undefined, lastUsedAt: undefined, usedBytes: 0, usedBytesAt: undefined,
      pendingFinalCommits: [] })
  })

  it('deletes by encoded name with an explicit ID-only lookup', async () => {
    // Given
    responses.push({ status: 200, body: {} })
    // When
    await service.delete('vol/name', { idOnly: true })
    // Then
    expect(requests).toEqual([{ method: 'DELETE', path: '/volumes/vol%2Fname?id_only=true', body: undefined }])
  })
})
