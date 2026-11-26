/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { formatWithOptions, inspect } from 'node:util'
import { Console } from 'node:console'
import { Writable } from 'node:stream'
import { Daytona } from '../src/Daytona'
import { createSandbox } from './helpers/sandbox'
import { createHttpClient, createPlatformTokenProvider, HttpTransport } from '../src/v2/http'
import { createServer } from 'node:http'
import axios from 'axios'
import type { AxiosInstance } from 'axios'
import { ImagesService } from '../src/v2/Images'
import { INTERNAL } from '../src/v2/internal'
import { SnapshotsService } from '../src/v2/Snapshots'
import { ReservationsService } from '../src/v2/Reservations'
import { MacModelsService } from '../src/v2/MacModels'
import { SecretsService } from '../src/v2/Secret'
import { PortsService } from '../src/v2/Ports'
import { WaitHub } from '../src/v2/events'

const API_KEY = 'dtn_inspection_api_secret'
const ACCESS_TOKEN = 'dtnv_inspection_sandbox_secret'
const JWT = 'eyJ.inspection_jwt_secret.signature'

const customNamespaces: readonly { readonly name: string; readonly create: (api: AxiosInstance) => object }[] = [
  { name: 'images', create: (api) => new ImagesService(INTERNAL, api) },
  { name: 'images with retained config', create: (api) => new ImagesService(INTERNAL, api, { apiKey: API_KEY }) },
  { name: 'snapshots', create: (api) => new SnapshotsService(INTERNAL, api) },
  { name: 'reservations', create: (api) => new ReservationsService(INTERNAL, api) },
  { name: 'Mac models', create: (api) => new MacModelsService(INTERNAL, api) },
  { name: 'secrets', create: (api) => new SecretsService(INTERNAL, api) },
  { name: 'ports', create: (api) => new PortsService(INTERNAL, undefined, api, 'sandbox') },
  { name: 'sandbox ports', create: (api) => createSandbox('sandbox', api, undefined, API_KEY).ports },
  { name: 'event hub', create: (api) => new WaitHub('https://api.invalid', API_KEY, {}, () => { throw new Error('unexpected socket') }, api) },
]

/**
 * Every path to a credential that a generic property walker (object spread,
 * `Object.entries`, deep-clone and error-reporting helpers) can follow from
 * `root`: own enumerable string and symbol keys, plus Map / Set entries.
 */
function enumerableCredentialPaths(root: unknown, label = 'value'): string[] {
  const found: string[] = []
  const seen = new Set<unknown>()
  const visit = (value: unknown, path: string): void => {
    if (typeof value === 'string') {
      if (['dtn_', 'dtnv_', API_KEY, ACCESS_TOKEN, JWT].some((credential) => value.includes(credential))) found.push(path)
      return
    }
    if ((typeof value !== 'object' && typeof value !== 'function') || value === null || seen.has(value)) return
    seen.add(value)
    if (value instanceof Map) {
      for (const [key, entry] of value) {
        visit(key, `${path}<key>`)
        visit(entry, `${path}<entry>`)
      }
    }
    if (value instanceof Set) for (const entry of value) visit(entry, `${path}<entry>`)
    for (const key of Reflect.ownKeys(value)) {
      if (!Object.prototype.propertyIsEnumerable.call(value, key)) continue
      let child: unknown
      try {
        child = Reflect.get(value, key)
      } catch {
        continue
      }
      visit(child, `${path}.${String(key)}`)
    }
  }
  visit(root, label)
  return found
}

function expectNotEnumerable<T extends object>(value: T): void {
  expect(enumerableCredentialPaths(value)).toEqual([])
  expect(JSON.stringify({ ...value })).not.toMatch(/dtn_|dtnv_|inspection_jwt_secret/)
  for (const key of ['apiKey', 'jwtToken', 'token', 'cachedToken', 'hubIdentityKey']) {
    expect(Object.keys(value)).not.toContain(key)
  }
}

function expectSafe(value: unknown, includeJSON = true, walk = includeJSON): void {
  if (walk && typeof value === 'object' && value !== null) expectNotEnumerable(value)
  let logged = ''
  const stdout = new Writable({ write(chunk, _encoding, callback) { logged += String(chunk); callback() } })
  new Console({ stdout, inspectOptions: { depth: null, showHidden: true } }).log(value)
  for (const output of [
    logged,
    inspect(value, { depth: null }),
    inspect(value, { depth: null, showHidden: true }),
    formatWithOptions({ depth: null }, '%O', value),
    ...(includeJSON ? [JSON.stringify(value)] : []),
  ]) {
    for (const credential of ['dtn_', 'dtnv_', API_KEY, ACCESS_TOKEN, JWT]) {
      expect(output ?? '').not.toContain(credential)
    }
  }
}

describe('credential-safe diagnostics', () => {
  it.each(customNamespaces)('omits custom-client credentials from $name', ({ create }) => {
    // Given
    const external = axios.create({ baseURL: 'https://api.invalid', headers: { Authorization: `Bearer ${API_KEY}` } })
    // When
    const namespace = create(external)
    // Then: serializers only; the caller-owned Axios instance keeps its own headers.
    expectSafe(namespace, true, false)
    expectSafe({ namespace }, true, false)
  })

  it.each(customNamespaces)('does not change caller-owned Axios diagnostics for $name', ({ create }) => {
    // Given
    const external = axios.create({ baseURL: 'https://api.invalid', headers: { Authorization: `Bearer ${API_KEY}` } })
    const descriptors = [external, external.defaults, external.defaults.headers].map(Object.getOwnPropertyDescriptors)
    const originalInspection = inspect(external, { depth: null, showHidden: true })
    // When
    create(external)
    // Then
    expect([external, external.defaults, external.defaults.headers].map(Object.getOwnPropertyDescriptors)).toEqual(descriptors)
    expect(inspect(external, { depth: null, showHidden: true })).toBe(originalInspection)
    expect(external.defaults.headers.Authorization).toBe(`Bearer ${API_KEY}`)
  })

  it('omits retained API credentials when the API client is already safe', () => {
    // Given
    const api = createHttpClient('https://api.invalid', API_KEY)
    const apiConfig = { apiKey: API_KEY, httpOptions: { authProvider: { token: ACCESS_TOKEN, authorizationHeader: async () => `Bearer ${ACCESS_TOKEN}` } } }
    // When
    const images = new ImagesService(INTERNAL, api, apiConfig)
    // SAFETY: getApiTransport is the service's private, argument-free resolver
    // that followBuildLogs() runs first.
    ;(images as unknown as { getApiTransport(): unknown }).getApiTransport()
    // Then
    expectSafe(images)
    expect(apiConfig.apiKey).toBe(API_KEY)
    expect(apiConfig.httpOptions.authProvider.token).toBe(ACCESS_TOKEN)
  })

  it('omits credentials from root and initialized v2 clients', async () => {
    // Given
    const root = new Daytona({ apiKey: API_KEY })
    const v2 = root.v2
    // When / Then
    for (const holder of [root, v2, Reflect.get(root, 'config'), { root, v2 }]) expectSafe(holder)
    await root.close()
  })

  it('omits JWTs from providers and client transports', async () => {
    // Given
    const root = new Daytona({ jwtToken: JWT, projectId: 'project' })
    const provider = createPlatformTokenProvider({ baseURL: 'https://api.invalid', jwtToken: JWT, projectId: 'project' })
    const transport = new HttpTransport('https://api.invalid', API_KEY)
    // When / Then
    for (const holder of [root, root.v2, provider, transport, transport.http, transport.http.defaults, transport.http.defaults.headers]) expectSafe(holder)
    expect(transport.token).toBe(API_KEY)
    await root.close()
  })

  it('omits credentials from sandbox namespaces and preserves metadata roundtrips', () => {
    // Given
    const api = createHttpClient('https://api.invalid', API_KEY)
    const sandbox = createSandbox('sandbox', api, undefined, API_KEY, undefined, {
      accessToken: ACCESS_TOKEN, toolboxUrl: 'https://toolbox.invalid',
    })
    // When
    const metadata = sandbox.toJSON()
    const restored = createSandbox(metadata.sandboxId, api, undefined, API_KEY)
    // Then
    for (const holder of [sandbox, { sandbox }, sandbox.fs, sandbox.process, Reflect.get(sandbox, 'toolboxTransport')]) expectSafe(holder)
    expect(metadata).toEqual({ sandboxId: 'sandbox' })
    expect(restored.toJSON()).toEqual(metadata)
    expect(sandbox.accessToken).toBe(ACCESS_TOKEN)
  })

  it('omits credentials from issued tokens and private preview links', async () => {
    // Given
    const api = createHttpClient('https://api.invalid', API_KEY)
    api.defaults.adapter = async (config) => ({
      config, status: 200, statusText: 'OK', headers: {},
      data: config.url?.endsWith('/access-token')
        ? { access_token: ACCESS_TOKEN, expires_at: '2099-01-01T00:00:00Z', token_type: 'Bearer', operations: [] }
        : config.url?.endsWith('/signed-preview-urls')
          ? { url: `https://${ACCESS_TOKEN}.preview.invalid`, token: ACCESS_TOKEN, expires_at: '2099-01-01T00:00:00Z' }
          : config.url?.endsWith('/preview-url')
            ? { url: 'https://preview.invalid', token: ACCESS_TOKEN }
            : { port: 8080, public: false, url: 'https://preview.invalid', created_at: '2026-01-01T00:00:00Z' },
    })
    const sandbox = createSandbox('sandbox', api, undefined, API_KEY)
    // When
    const token = await sandbox.createAccessToken()
    const preview = await sandbox.ports.previewLink(8080)
    const signed = await sandbox.ports.signedPreviewLink(8080)
    // Then
    for (const holder of [token, preview, signed]) expectSafe(holder, false)
    expect(token.accessToken).toBe(ACCESS_TOKEN)
    expect(preview.token).toBe(ACCESS_TOKEN)
    expect(signed.url).toContain(ACCESS_TOKEN)
    // These are explicit credential-return DTOs, not incidental client dumps.
    expect(JSON.parse(JSON.stringify(token))).toMatchObject({ accessToken: ACCESS_TOKEN })
    expect(JSON.parse(JSON.stringify(preview))).toMatchObject({ token: ACCESS_TOKEN })
    expect(JSON.parse(JSON.stringify(signed))).toMatchObject({ token: ACCESS_TOKEN, url: `https://${ACCESS_TOKEN}.preview.invalid` })
  })

  it('keeps exchanged tokens private without changing outgoing authorization', async () => {
    // Given
    const authorizations: (string | undefined)[] = []
    const server = createServer((request, response) => {
      authorizations.push(request.headers.authorization)
      response.setHeader('content-type', 'application/json')
      response.end(JSON.stringify(request.url?.includes('/token')
        ? { access_token: ACCESS_TOKEN, expires_in: 3600 }
        : { ok: true }))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('expected TCP listener')
    const baseURL = `http://127.0.0.1:${address.port}`
    const provider = createPlatformTokenProvider({ baseURL, jwtToken: JWT, projectId: 'project' })
    const transport = new HttpTransport(baseURL, undefined, undefined, { authProvider: provider })
    try {
      // When
      await transport.get('/resource')
      // Then
      expect(authorizations).toEqual([`Bearer ${JWT}`, `Bearer ${ACCESS_TOKEN}`])
      for (const holder of [provider, transport, Reflect.get(provider, 'cachedToken')]) expectSafe(holder)
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => error ? reject(error) : resolve())
        // Node 18 does not reap idle keep-alive connections in server.close().
        server.closeIdleConnections()
      })
    }
  })
  it('keeps credentials out of every enumerable property of the clients, handles and transports', async () => {
    // Given: a key client with its events hub attached, and a JWT client
    const root = new Daytona({ apiKey: API_KEY })
    const v2 = root.v2
    const hub = (v2 as unknown as { acquireHub(): object }).acquireHub()
    const jwtRoot = new Daytona({ jwtToken: JWT, projectId: 'project' })
    const api = createHttpClient('https://api.invalid', API_KEY)
    const sandbox = createSandbox('sandbox', api, undefined, API_KEY, undefined, {
      accessToken: ACCESS_TOKEN, toolboxUrl: 'https://toolbox.invalid',
    })
    const keyed = createSandbox('keyed', api, undefined, API_KEY, undefined, { toolboxUrl: 'https://toolbox.invalid' })
    try {
      // When: every lazily built namespace exists
      const namespaces = [sandbox, keyed].flatMap((handle) => [
        handle.fs, handle.git, handle.process, handle.interpreter, handle.pty,
        handle.computerUse, handle.docker, handle.ports, handle.volumes,
      ])
      // Then
      for (const holder of [
        root, v2, hub, jwtRoot, jwtRoot.v2, v2.images, v2.snapshots, v2.volumes, v2.secrets, v2.reservations, v2.macModels,
        sandbox, keyed, ...namespaces, { root, v2, sandbox, keyed },
      ]) {
        expectNotEnumerable(holder)
      }
      expect(Object.keys(sandbox)).not.toContain('apiKey')
      expect(Object.keys(Reflect.get(sandbox.fs, 'transport') as object)).not.toContain('apiKey')
      expect(sandbox.accessToken).toBe(ACCESS_TOKEN)
    } finally {
      await root.close()
      await jwtRoot.close()
    }
  })

  it('keeps a minted sandbox token out of enumerable properties', async () => {
    // Given: a handle that mints its own toolbox token
    const api = createHttpClient('https://api.invalid', API_KEY)
    const sent: string[] = []
    api.defaults.adapter = async (config) => {
      sent.push(String(config.headers.get('Authorization')))
      return {
        config, status: 200, statusText: 'OK', headers: {},
        data: { access_token: ACCESS_TOKEN, expires_at: '2099-01-01T00:00:00Z', token_type: 'Bearer', operations: [] },
      }
    }
    const sandbox = createSandbox('sandbox', api, undefined, API_KEY, undefined, { toolboxUrl: 'https://toolbox.invalid' })
    const toolbox = Reflect.get(sandbox, 'toolboxTransport') as HttpTransport
    toolbox.http.defaults.adapter = async (config) => {
      sent.push(String(config.headers.get('Authorization')))
      return { config, status: 200, statusText: 'OK', headers: {}, data: { ok: true } }
    }
    // When
    await toolbox.get('/files/info')
    // Then
    expect(sent).toEqual([`Bearer ${API_KEY}`, `Bearer ${ACCESS_TOKEN}`])
    expect(sandbox.accessToken).toBe(ACCESS_TOKEN)
    for (const holder of [sandbox, Reflect.get(sandbox, 'auth') as object, toolbox]) expectNotEnumerable(holder)
  })

  it('sends the API key on each request without storing it in the client defaults', async () => {
    // Given
    const api = createHttpClient('https://api.invalid', API_KEY)
    const sent: string[] = []
    api.defaults.adapter = async (config) => {
      sent.push(String(config.headers.get('Authorization')))
      return { config, status: 200, statusText: 'OK', headers: {}, data: {} }
    }
    // When
    await api.get('/ping')
    await api.get('/ping', { headers: { Authorization: `Bearer ${ACCESS_TOKEN}` } })
    await api.get('/ping', { headers: { Authorization: null } })
    // Then: the default bearer, a per-request one that replaces it, and none
    // when the request removes the header
    expect(sent).toEqual([`Bearer ${API_KEY}`, `Bearer ${ACCESS_TOKEN}`, 'null'])
    expect(enumerableCredentialPaths(api)).toEqual([])
  })
})
