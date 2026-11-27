/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Transfer, screenshot, create and image-builder inputs are refused before
// any request, and the messages of the shared checks match the Python and Go
// SDKs. The Python twin is tests/test_inputs_and_messages.py.

import { mkdtempSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { AuthenticationError, BadRequestError, ImageBuilder, PayloadTooLargeError, ReservationsService, SecretsService } from '../src/v2/index'
import { FileSystemService } from '../src/v2/FileSystem'
import { ComputerUseService } from '../src/v2/ComputerUse'
import type { ComputerUseTransport } from '../src/v2/ComputerUseNamespaces'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'
import { searchQueryParams } from '../src/v2/clientSearch'
import { clientConnectionConfig as resolveClientConfig } from '../src/v2/clientConfig'
import { nonOptionsDescription } from '../src/v2/errors/DaytonaError'
import { mapComputerUsePoint, mapComputerUseProcessLogsResponse } from '../src/v2/ComputerUseMappers'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { PlannedResponse } from './networkPolicyTestServer'

async function refused(call: () => Promise<unknown>, message: string): Promise<void> {
  const error: unknown = await call().then(() => undefined, (caught: unknown) => caught)
  expect(error).toBeInstanceOf(BadRequestError)
  expect(error).toMatchObject({ message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function fileSystem() {
  const transport = new HttpTransport('https://toolbox.invalid', 'token')
  const requests = [
    jest.spyOn(transport, 'get'), jest.spyOn(transport, 'post'),
    jest.spyOn(transport, 'postRaw'), jest.spyOn(transport, 'fetch'),
  ]
  for (const request of requests) request.mockRejectedValue(new Error('no request expected'))
  return { fs: new FileSystemService(INTERNAL, transport), requests }
}

describe('transfer inputs (#575)', () => {
  let dir: string
  beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'daytona-inputs-')) })
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
    jest.restoreAllMocks()
  })

  it.each([
    [{ signal: { aborted: false } }, 'signal must be an AbortSignal'],
    [{ signal: {} }, 'signal must be an AbortSignal'],
    [{ signal: null }, 'signal must be an AbortSignal'],
    [{ onProgress: 5 }, 'onProgress must be a function'],
  ])('refuses %j before any I/O', async (options, message) => {
    const source = join(dir, 'source.txt')
    writeFileSync(source, 'x')
    const { fs, requests } = fileSystem()
    await refused(() => fs.uploadFile(source, '/tmp/dest', options as never), message)
    await refused(() => fs.downloadFile('/tmp/src', join(dir, 'dest'), options as never), message)
    for (const request of requests) expect(request).not.toHaveBeenCalled()
    expect(existsSync(join(dir, 'dest'))).toBe(false)
  })

  it('accepts an AbortSignal and a signal-shaped object', async () => {
    const source = join(dir, 'source.txt')
    writeFileSync(source, 'x')
    const { fs, requests } = fileSystem()
    const foreign = { aborted: false, addEventListener: () => undefined, removeEventListener: () => undefined }
    for (const signal of [new AbortController().signal, foreign]) {
      await expect(fs.uploadFile(source, '/tmp/dest', { signal: signal as AbortSignal, onProgress: () => undefined })).rejects.toThrow('no request expected')
    }
    expect(requests[3]).toHaveBeenCalledTimes(2)
  })

  it('refuses a non-boolean atomic', async () => {
    const source = join(dir, 'source.txt')
    writeFileSync(source, 'x')
    const { fs, requests } = fileSystem()
    await refused(() => fs.uploadFile(source, '/tmp/dest', { atomic: 'yes' as never }), 'atomic must be a boolean')
    await refused(() => fs.writeBytes('/tmp/dest', new Uint8Array([1]), { atomic: 1 as never }), 'atomic must be a boolean')
    await refused(() => fs.writeText('/tmp/dest', 'x', { atomic: null as never }), 'atomic must be a boolean')
    await refused(() => fs.writeMany({ '/tmp/dest': new Uint8Array([1]) }, { atomic: 'no' as never }), 'atomic must be a boolean')
    for (const request of requests) expect(request).not.toHaveBeenCalled()
  })

  it('refuses to upload a local directory before any request', async () => {
    const { fs, requests } = fileSystem()
    await refused(() => fs.uploadFile(dir, '/tmp/dest'), 'localPath must be a regular file')
    for (const request of requests) expect(request).not.toHaveBeenCalled()
  })
})

describe('screenshot region (#575)', () => {
  function screen() {
    const get = jest.fn().mockResolvedValue({ screenshot: 'aGk=', sizeBytes: 2 })
    const transport: ComputerUseTransport = {
      get: get as ComputerUseTransport['get'],
      post: async () => { throw new Error('unexpected request') },
      delete: async () => { throw new Error('unexpected request') },
      getBytes: async () => { throw new Error('unexpected request') },
    }
    return { get, screen: new ComputerUseService(INTERNAL, transport).screen }
  }

  it.each([
    [{ x: 0, y: 0, width: 1 }, 'region.height must be an integer'],
    [{ x: 1.5, y: 0, width: 1, height: 1 }, 'region.x must be an integer'],
    [{ x: 0, y: true, width: 1, height: 1 }, 'region.y must be an integer'],
    [{ x: 0, y: 0, width: '1', height: 1 }, 'region.width must be an integer'],
    [{ x: 0, y: 0, width: 0, height: 1 }, 'region.width must be greater than 0'],
    [{ x: 0, y: 0, width: 1, height: -2 }, 'region.height must be greater than 0'],
    [{ x: 2 ** 53, y: 0, width: 1, height: 1 }, 'region.x must be between -9007199254740991 and 9007199254740991'],
  ])('refuses region %j before any request', async (region, message) => {
    const { get, screen: service } = screen()
    await refused(() => service.screenshot({ region: region as never }), message)
    expect(get).not.toHaveBeenCalled()
  })

  it('sends a valid region as integers', async () => {
    const { get, screen: service } = screen()
    await service.screenshot({ region: { x: -5, y: 0, width: 10, height: 20 } })
    expect(get).toHaveBeenCalledWith('/computeruse/screenshot/region', { params: { x: '-5', y: '0', width: '10', height: '20' } })
  })
})

describe('create inputs (#575)', () => {
  it.each([
    [{ 'A\ud800': 'x' }, 'env keys must not contain a lone surrogate'],
    [{ A: 'x\udc00' }, 'env values must not contain a lone surrogate'],
    [{ 'A=B': 'x' }, 'env key is not a valid variable name'],
    [{ '': 'x' }, 'env key is not a valid variable name'],
    [{ 'A\nB': 'x' }, 'env key is not a valid variable name'],
    [{ A: 'x\0y' }, 'env values must not contain NUL'],
  ])('refuses env %j as updateEnv does', async (env, message) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      await refused(() => client.v2.create('img', { env }), message)
      await refused(() => client.v2.createMacos('base-macos', { env }), message)
      await refused(() => client.v2.createFromBuild(ImageBuilder.fromRegistry('python:3.12'), { env }), message)
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('refuses an idempotency key over 256 bytes before any request', async () => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      const message = 'idempotencyKey must be at most 256 bytes'
      const key = 'x'.repeat(257)
      await refused(() => client.v2.create('img', { idempotencyKey: key }), message)
      await refused(() => client.v2.createFromBuild(ImageBuilder.fromRegistry('python:3.12'), { idempotencyKey: key }), message)
      await refused(() => client.v2.reservations.create({ region: 'r', idempotencyKey: key }), message)
      await refused(() => client.v2.reservations.renew('res', { idempotencyKey: key }), message)
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('sends the env it checked, not a later change to the caller object', async () => {
    const api = await startFakeApi([{ status: 200, body: { id: 'sbx', class: 'linux', status: 'running', region_id: 'region-a' } }])
    const client = clientAt(api.url)
    try {
      const env: Record<string, string> = { A: 'x' }
      // A non-ASCII name: its idempotency key is hashed asynchronously.
      const pending = client.v2.create('img', { name: 'm\u00e9t\u00e9o', env, waitSeconds: 0 })
      env.A = 'y\0'
      await pending
      expect(JSON.parse(api.requests[0].body).env).toEqual({ A: 'x' })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('sends the idempotency key as the header only; a random key is a UUID v4', async () => {
    const created = { status: 200, body: { id: 'sbx', class: 'linux', status: 'running', region_id: 'region-a' } }
    const api = await startFakeApi([created, created, created])
    const client = clientAt(api.url)
    try {
      await client.v2.create('img', { idempotencyKey: 'k'.repeat(256), waitSeconds: 0 })
      await client.v2.create('img', { waitSeconds: 0 })
      await client.v2.create('img', { name: 'box', waitSeconds: 0 })
      const [explicit, random, named] = api.requests
      for (const request of api.requests) expect(JSON.parse(request.body)).not.toHaveProperty('idempotency_key')
      expect(explicit.headers['idempotency-key']).toBe('k'.repeat(256))
      expect(random.headers['idempotency-key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
      expect(named.headers['idempotency-key']).toBe('name:box')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('sends a random reservation key as a UUID v4', async () => {
    const post = jest.fn().mockRejectedValue(new Error('stop'))
    const reservations = new ReservationsService(INTERNAL, { post } as never)
    await expect(reservations.renew('res')).rejects.toThrow('stop')
    expect(post.mock.calls[0][2].headers['Idempotency-Key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
})

describe('registry references (#575)', () => {
  it.each(['python 3', 'python:3.12 AS base', 'py\u00a0thon', 'py\u2003thon'])('refuses %j', (ref) => {
    expect(() => ImageBuilder.fromRegistry(ref)).toThrow('must not contain whitespace')
  })

  it.each([
    'python:3.12-slim',
    '  python:3.12  ',
    'registry.example.com:5000/team/app:v1',
    `ghcr.io/org/app@sha256:${'a'.repeat(64)}`,
    `localhost:5000/app:latest@sha256:${'b'.repeat(64)}`,
  ])('keeps %j', (ref) => {
    expect(ImageBuilder.fromRegistry(ref).toDockerfile()).toBe(`FROM ${ref.trim()}\n`)
  })
})

describe('shared messages (#571)', () => {
  const saved = { ...process.env }
  afterEach(() => {
    process.env = { ...saved }
  })

  it('names the config and the variables for missing or conflicting credentials', () => {
    delete process.env.DAYTONA_API_KEY
    delete process.env.DAYTONA_JWT_TOKEN
    delete process.env.DAYTONA_PROJECT_ID
    expect(() => resolveClientConfig({})).toThrow(AuthenticationError)
    expect(() => resolveClientConfig({})).toThrow('configure apiKey or jwtToken (in DaytonaConfig or as DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)')
    process.env.DAYTONA_API_KEY = 'key'
    process.env.DAYTONA_JWT_TOKEN = 'jwt'
    expect(() => resolveClientConfig({})).toThrow('configure exactly one of apiKey or jwtToken (in DaytonaConfig or as DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)')
    expect(() => resolveClientConfig({ jwtToken: 'jwt' })).toThrow('jwtToken requires projectId (in DaytonaConfig or as DAYTONA_PROJECT_ID)')
  })

  it.each([
    [5, 'states must be a sandbox state or a list of sandbox states'],
    [[], 'states must name at least one sandbox state'],
    ['unknown', 'states entries must be known sandbox states, got "unknown"'],
    [['started', 3], 'states entries must be strings'],
    [[true], 'states entries must be strings'],
    [[null], 'states entries must be strings'],
    [[Object.create(null)], 'states entries must be strings'],
    ['météo', 'states entries must be known sandbox states, got "météo"'],
  ])('search states %j', (states, message) => {
    expect(() => searchQueryParams({ states: states as never })).toThrow(new BadRequestError(message))
  })

  it('words every seconds option the same way', () => {
    expect(() => resolveClientConfig({ apiKey: 'k', requestTimeoutSeconds: -1 })).toThrow('requestTimeoutSeconds must be a non-negative finite number of seconds')
  })

  it.each([
    [() => mapComputerUsePoint({ x: 'a', y: 1 }, 'computer-use point response'), 'expected computer-use point response: x must be an integer'],
    [() => mapComputerUsePoint({ x: 1 }, 'computer-use point response'), 'expected computer-use point response: y is required'],
    [() => mapComputerUseProcessLogsResponse({ processName: 'xvfb' }), 'expected computer-use process logs response: logs is required'],
    [() => mapComputerUseProcessLogsResponse({ processName: 'xvfb', logs: 5 }), 'expected computer-use process logs response: logs must be a string'],
  ])('malformed response wording %#', (parse, message) => {
    expect(parse).toThrow(message)
  })

  it('says "got undefined" for a missing options object', async () => {
    expect(nonOptionsDescription(undefined)).toBe('undefined')
    const reservations = new ReservationsService(INTERNAL, { patch: jest.fn() } as never)
    await expect(reservations.update('res', undefined as never)).rejects.toThrow('UpdateReservationOptions must be an options object, got undefined')
    const secrets = new SecretsService(INTERNAL, { put: jest.fn(), post: jest.fn() } as never)
    await expect(secrets.set('name', 'value', undefined as never)).rejects.toThrow('SetSecretOptions must be an options object, got undefined')
  })

  it('names the reservation id parameter', async () => {
    const reservations = new ReservationsService(INTERNAL, { get: jest.fn() } as never)
    await expect(reservations.get('')).rejects.toThrow('reservationId must be a non-empty string')
  })

  it('gives each call the payload-too-large hint it supports', async () => {
    const cap = 2048
    const planned: PlannedResponse[] = []
    const api = await startFakeApi(planned)
    planned.push(
      { status: 200, body: { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', toolbox_proxy_url: `${api.url}/toolbox` } },
      { status: 200, body: { items: [{ name: 'x'.repeat(cap * 2) }] } },
      { status: 200, body: 'y'.repeat(cap * 2), headers: { 'content-type': 'application/octet-stream' } },
    )
    const client = clientAt(api.url, { maxResponseBytes: cap })
    try {
      const sandbox = await client.v2.get('sbx', { accessToken: 'tok' })
      const listed: unknown = await sandbox.fs.list('/etc').catch((error: unknown) => error)
      const read: unknown = await sandbox.fs.readBytes('/big').catch((error: unknown) => error)
      expect(listed).toBeInstanceOf(PayloadTooLargeError)
      expect(read).toBeInstanceOf(PayloadTooLargeError)
      expect((listed as Error).message).toBe(`the response body is larger than the ${cap}-byte maxResponseBytes limit; raise the client's maxResponseBytes (0 = no limit)`)
      expect((read as Error).message).toBe(`the response body is larger than the ${cap}-byte maxResponseBytes limit; pass a higher maxResponseBytes for this call (0 = no limit), or use downloadFile() (Node.js) or presignedDownloadUrl() for large files`)
      for (const error of [listed, read]) expect(error).toMatchObject({ statusCode: 413, code: 'PAYLOAD_TOO_LARGE', source: 'SDK' })
    } finally {
      await client.close()
      await api.close()
    }
  })
})
