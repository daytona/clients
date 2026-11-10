/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/Daytona'
import { ClientClosedError, NotFoundError, ImageBuildFailedError, SandboxTerminalStateError } from '../src/v2/index'
import { codes } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

describe('client input and lifecycle', () => {
  afterEach(() => jest.restoreAllMocks())

  it.each(['404', true, 404.5, NaN, null])('refuses invalid statusCode %p', (statusCode) => {
    // Given untyped caller input, when constructing an error, then refuse it.
    expect(() => Reflect.construct(NotFoundError, ['missing', { statusCode }])).toThrow(TypeError)
  })

  it.each([
    { status_code: 404 },
    Object.create({ misspelled: true }),
  ])('refuses unknown own and inherited options %p', (options: unknown) => {
    // Given unknown keys, when constructing an error, then refuse them.
    expect(() => Reflect.construct(NotFoundError, ['missing', options])).toThrow(TypeError)
  })

  it('preserves specialized error options when recognized', () => {
    // Given the specialized option shapes, when constructing their errors,
    const image = new ImageBuildFailedError('failed', { imageId: 'img', statusCode: 409 })
    const terminal = new SandboxTerminalStateError('sb', 'error', { rawState: 'lost', reason: 'gone' })
    // Then their specific fields survive boundary parsing.
    expect(image.imageId).toBe('img')
    expect(terminal.rawState).toBe('lost')
  })

  it.each([Symbol('extra'), 'extra'])('keeps constructing errors with the unknown enumerable key %p on Object.prototype', (key) => {
    // Given an unknown enumerable key a library put on Object.prototype,
    Object.defineProperty(Object.prototype, key, { value: true, enumerable: true, configurable: true })
    try {
      // When constructing an error, then it still works: the check stops before Object.prototype.
      expect(new NotFoundError('missing', { statusCode: 404 })).toMatchObject({ statusCode: 404, message: 'missing' })
    } finally {
      Reflect.deleteProperty(Object.prototype, key)
    }
  })

  it('warns once for a custom legacy URL without disclosing it', () => {
    // Given a custom legacy endpoint and a fresh process latch,
    const previous = process.env.DAYTONA_API_URL
    process.env.DAYTONA_API_URL = 'https://custom.invalid/api'
    Reflect.deleteProperty(globalThis, Symbol.for('@daytona/sdk/legacy-api-url-warned'))
    const warning = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    try {
      // When constructing multiple roots,
      new Daytona()
      new Daytona()
      // Then one non-sensitive warning is emitted.
      expect(warning).toHaveBeenCalledTimes(1)
      expect(String(warning.mock.calls[0]?.[0])).not.toContain('custom.invalid')
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_API_URL
      else process.env.DAYTONA_API_URL = previous
      Reflect.deleteProperty(globalThis, Symbol.for('@daytona/sdk/legacy-api-url-warned'))
    }
  })

  it.each([undefined, '', '  ', ' HTTPS://API.DAYTONA.COM/// ', 'https://APP.DAYTONA.IO/api/'])('does not warn for Cloud or blank legacy URL %p', (value) => {
    // Given a Cloud, blank or absent legacy URL,
    const previous = process.env.DAYTONA_API_URL
    if (value === undefined) delete process.env.DAYTONA_API_URL
    else process.env.DAYTONA_API_URL = value
    Reflect.deleteProperty(globalThis, Symbol.for('@daytona/sdk/legacy-api-url-warned'))
    const warning = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    try {
      // When building a root, then no warning is emitted.
      new Daytona()
      expect(warning).not.toHaveBeenCalled()
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_API_URL
      else process.env.DAYTONA_API_URL = previous
    }
  })

  it.each(['list', 'service', 'toolbox', 'stream', 'wait'] as const)('refuses %s requests after its root closes', async (entry) => {
    // Given a stored client and handle whose root has closed,
    const api = await startFakeApi([{ status: 200, body: { id: 'sandbox-id', class: 'linux', status: 'running', toolbox_proxy_url: 'https://sandbox.invalid', name: 'test', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const root = clientAt(api.url, { events: { enabled: false } })
    const client = root.v2
    const sandbox = await client.get('sandbox-id', { toolboxUrl: 'https://sandbox.invalid', accessToken: 'test-token' })
    await root.close()
    // When starting a new request through each surface,
    const operation = () => {
      switch (entry) {
        case 'list': return client.list()
        case 'service': return client.volumes.list()
        case 'toolbox': return sandbox.fs.readText('/test')
        case 'stream': return sandbox.fs.watchDir('/test', () => undefined)
        case 'wait': return sandbox.wait({ until: 'started', timeoutSeconds: 1 })
        default: throw new TypeError('unknown test entry')
      }
    }
    // Then all requests fail with the exported local error.
    try {
      await expect(operation()).rejects.toBeInstanceOf(ClientClosedError)
      expect(api.requests).toHaveLength(1)
    } finally {
      await root.close()
      await api.close()
    }
  })

  // Shared with the Python SDK's test_core.py: a create whose request completes
  // while the client closes still returns its handle (the sandbox exists).
  it('returns the handle of a create that completes during close', async () => {
    const body = { id: 'sbx', class: 'linux', status: 'running', name: 'test', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
    const api = await startFakeApi([{ status: 200, body }])
    const root = clientAt(api.url, { events: { enabled: true } })
    const client = root.v2
    try {
      // SAFETY: the client's private API instance, spied to close the client
      // while the create request is in flight.
      const instance = (client as unknown as { api: { post: (...args: unknown[]) => Promise<unknown> } }).api
      const post = instance.post.bind(instance)
      jest.spyOn(instance, 'post').mockImplementationOnce(async (...args: unknown[]) => {
        const response = await post(...args)
        await client.close()
        return response
      })
      await expect(client.create('py312', { waitSeconds: 0 })).resolves.toMatchObject({ id: 'sbx' })
    } finally {
      await root.close()
      await api.close()
    }
  })

  it('builds a working fresh namespace when reaccessed after closing the root', async () => {
    // Given a closed namespace and a server ready for the next client,
    const api = await startFakeApi([{ status: 200, body: { sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq' } }])
    const root = clientAt(api.url)
    const previous = root.v2
    await root.close()
    const previousUrl = process.env.DAYTONA_V2_API_URL
    process.env.DAYTONA_V2_API_URL = api.url
    try {
      // When requesting through the root again,
      const page = await root.v2.list()
      // Then the fresh namespace works.
      expect(root.v2).not.toBe(previous)
      expect(page.items).toEqual([])
    } finally {
      if (previousUrl === undefined) delete process.env.DAYTONA_V2_API_URL
      else process.env.DAYTONA_V2_API_URL = previousUrl
      await root.close()
      await api.close()
    }
  })

  it('recognizes the local error without assigning an HTTP status', () => {
    // Given a local closed error, when reading its catalog metadata,
    const error = new ClientClosedError()
    // Then only SDK code and source apply.
    expect(error).toMatchObject({ code: 'CLIENT_CLOSED', source: 'SDK', statusCode: undefined })
    expect(codes.isKnownErrorCode(error.code)).toBe(true)
    expect(codes.CODE_STATUS.CLIENT_CLOSED).toBeUndefined()
  })

  it('refuses waits when another client keeps the shared hub alive', async () => {
    // Given two handles whose clients share an events hub,
    const body = { id: 'sbx', class: 'linux', status: 'running', toolbox_proxy_url: 'https://sandbox.invalid', name: 'test', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
    const api = await startFakeApi([{ status: 200, body }, { status: 200, body }])
    const first = clientAt(api.url, { events: { enabled: true } })
    const second = clientAt(api.url, { events: { enabled: true } })
    try {
      const sandbox = await first.v2.get('sbx')
      await second.v2.get('sbx')
      await first.close()
      // When the closed client's handle waits, then refuse before using the hub.
      await expect(sandbox.wait({ until: 'started', timeoutSeconds: 0.01 })).rejects.toBeInstanceOf(ClientClosedError)
      expect(api.requests).toHaveLength(2)
    } finally {
      await first.close()
      await second.close()
      await api.close()
    }
  })

  it('keeps the shared hub working for the other client after one client closes', async () => {
    // Given two clients sharing the events hub the first one created,
    const body = { id: 'sbx', class: 'linux', status: 'running', name: 'test', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
    const api = await startFakeApi([{ status: 200, body }])
    const first = clientAt(api.url, { events: { enabled: true } })
    const second = clientAt(api.url, { events: { enabled: true } })
    try {
      // SAFETY: acquireHub is the client's private hub accessor (no arguments).
      const acquire = (client: Daytona): unknown => (client.v2 as unknown as { acquireHub(): unknown }).acquireHub()
      const hub = acquire(first)
      expect(acquire(second)).toBe(hub)
      // When the first client closes,
      await first.close()
      // then the hub's own requests (ticket mints, reads) still work for the second.
      const hubApi = Reflect.get(hub as object, 'api') as { get: (path: string) => Promise<{ data: unknown }> }
      await expect(hubApi.get('/sandboxes/sbx')).resolves.toMatchObject({ data: { id: 'sbx' } })
    } finally {
      await first.close()
      await second.close()
      await api.close()
    }
  })
})
