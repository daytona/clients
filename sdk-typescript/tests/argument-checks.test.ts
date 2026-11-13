/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { once } from 'node:events'
import { BadRequestError, ImageBuilder, RequestTimeoutError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { Image } from '../src/v2/Image'
import { INTERNAL } from '../src/v2/internal'
import { pathSegment } from '../src/v2/pathSegment'
import { PortsService } from '../src/v2/Ports'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { FakeApi } from './networkPolicyTestServer'

// The argument rule for the client, lifecycle and platform-service area: a
// wrong type or value is a BadRequestError (400, BAD_REQUEST, SDK) before any
// request, as in Python (tests/test_argument_checks.py).

type Call = (client: Daytona, api: FakeApi) => Promise<unknown>
// The fake's own 500 ({ message: 'stop' }, naming no source): the request was sent, so it is not the SDK's refusal.
const SERVER_STOP = { statusCode: 500, message: 'stop', source: undefined }
const handle = (api: FakeApi) => createSandbox({ id: 'sbx', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))
const withToolbox = (api: FakeApi) => createSandbox({ id: 'sbx', class: 'linux', status: 'started' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })
const unknownClass = (api: FakeApi) => createSandbox('sbx', createHttpClient(api.url, 'key'))

// SAFETY: every `as never` below is a deliberately invalid plain-JavaScript argument.
const REFUSED: readonly (readonly [string, Call])[] = [
  ['wait until a non-state', (_client, api) => handle(api).wait({ until: 'bogus' as never })],
  ['wait until a list with a non-state', (_client, api) => handle(api).wait({ until: ['started', 'bogus'] as never })],
  ['wait until a number', (_client, api) => handle(api).wait({ until: 5 as never })],
  // Only an omitted `until` defaults to `started`; Python refuses `until=None` too.
  ['wait until null', (_client, api) => handle(api).wait({ until: null as never })],
  ['wait until an empty list', (_client, api) => handle(api).wait({ until: [] })],
  ['docker timeout as a string', (_client, api) => withToolbox(api).docker.waitUntilReady({ timeoutSeconds: '5' as never })],
  ['stop(123)', (client) => client.v2.stop(123 as never)],
  ['delete(null)', (client) => client.v2.delete(null as never)],
  ['start(blank id)', (client) => client.v2.start('  ')],
  ['create region as a number', (client) => client.v2.create({ region: 5 as never })],
  ['findByName region as a number', (client) => client.v2.findByName('x', { region: 5 as never })],
  ['create waitSeconds null', (client) => client.v2.create({ waitSeconds: null as never })],
  ['fork waitSeconds null', (_client, api) => handle(api).fork({ waitSeconds: null as never })],
  ['memory 0', (client) => client.v2.create({ resources: { memory: 0 } })],
  ['disk -1', (client) => client.v2.create({ resources: { disk: -1 } })],
  ['memoryMax 0', (client) => client.v2.create({ resources: { memoryMax: 0 } })],
  ['diskMax 0', (client) => client.v2.create({ resources: { diskMax: 0 } })],
  ['create autoDelete as a string', (client) => client.v2.create({ autoDelete: 'yes' as never })],
  ['get autoDelete as a number', (client) => client.v2.get('sbx', { autoDelete: 1 as never })],
  ['fork autoDelete as a string', (_client, api) => handle(api).fork({ autoDelete: 'yes' as never })],
  ['fork blank name before reading the class', (_client, api) => unknownClass(api).fork({ name: '  ' })],
  ['fork memory before reading the class', (_client, api) => unknownClass(api).fork({ memory: 'no' as never })],
  ['reservations.create()', (client) => client.v2.reservations.create(undefined as never)],
  ['reservations.createBulk()', (client) => client.v2.reservations.createBulk(undefined as never)],
  ['reservations.update(id)', (client) => client.v2.reservations.update('res', undefined as never)],
  ['sandbox.volumes.pull(id)', (_client, api) => withToolbox(api).volumes.pull('vol', undefined as never)],
  ['updateLifecyclePolicy()', (_client, api) => handle(api).updateLifecyclePolicy(undefined as never)],
  ['images.followBuildLogs(id)', (client) => client.v2.images.followBuildLogs('img', undefined as never)],
  ['get(blank id)', (client) => client.v2.get('  ')],
  ['reservations.create region as a number', (client) => client.v2.reservations.create({ region: 5 as never })],
  ['reservations.create model as a number', (client) => client.v2.reservations.create({ region: 'r', model: 5 as never })],
  ['reservations.create autoRenew as a string', (client) => client.v2.reservations.create({ region: 'r', autoRenew: 'yes' as never })],
  ['reservations.createBulk count as a string', (client) => client.v2.reservations.createBulk({ region: 'r', count: '2' as never })],
  ['reservations.createBulk minCount 0', (client) => client.v2.reservations.createBulk({ region: 'r', count: 2, minCount: 0 })],
  ['reservations.createBulk count 0', (client) => client.v2.reservations.createBulk({ region: 'r', count: 0 })],
  ['reservations.createBulk count 101', (client) => client.v2.reservations.createBulk({ region: 'r', count: 101 })],
  ['reservations.createBulk minCount above count', (client) => client.v2.reservations.createBulk({ region: 'r', count: 2, minCount: 3 })],
  ['createAccessToken ports null', (_client, api) => handle(api).createAccessToken({ ports: null as never })],
  ['createAccessToken ports a number', (_client, api) => handle(api).createAccessToken({ ports: 3000 as never })],
  ['createAccessToken ports with a string', (_client, api) => handle(api).createAccessToken({ ports: ['3000'] as never })],
  // The sandbox daemon's ports are never preview ports (the Python twin refuses the same calls).
  ['createAccessToken reserved port 2280', (_client, api) => handle(api).createAccessToken({ ports: [3000, 2280] })],
  ['createAccessToken reserved port 2281', (_client, api) => handle(api).createAccessToken({ ports: [2281] })],
  ['createAccessToken reserved port 22220', (_client, api) => handle(api).createAccessToken({ ports: [22220] })],
  ['ports.expose reserved port', (_client, api) => handle(api).ports.expose(2280)],
  ['ports.previewLink reserved port', (_client, api) => handle(api).ports.previewLink(2281)],
  ['ports.signedPreviewLink reserved port', (_client, api) => handle(api).ports.signedPreviewLink(22220)],
  ['setLabels empty key', (_client, api) => handle(api).setLabels({ team: 'a', '': 'x' })],
  ['create labels empty key', (client) => client.v2.create({ labels: { '': 'x' }, waitSeconds: 0 })],
  ['createFromSnapshot labels empty key', (client) => client.v2.createFromSnapshot('snap', { labels: { '': 'x' }, waitSeconds: 0 })],
  ['createMacos labels empty key', (client) => client.v2.createMacos('img', { labels: { '': 'x' }, waitSeconds: 0 })],
  ['createFromBuild labels empty key', (client) => client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { labels: { '': 'x' }, waitSeconds: 0 })],
  ['search labels empty key', (client) => client.v2.search({ labels: { team: 'a', '': 'x' } })],
  ['create waitSeconds 1e300 is fine, createTimeoutSeconds above 2^53-1 is not', (client) => client.v2.create({ createTimeoutSeconds: 2 ** 53 })],
  ['create memory above int32 MiB', (client) => client.v2.create({ resources: { memory: 2_097_152 } })],
  ['create diskMax above int32 MiB', (client) => client.v2.create({ resources: { diskMax: 2_097_152 } })],
  ['reservations.update without autoRenew', (client) => client.v2.reservations.update('res', {} as never)],
  ['reservations.update autoRenew as a string', (client) => client.v2.reservations.update('res', { autoRenew: 'yes' as never })],
  ['volumes.delete(a Volume object)', (client) => client.v2.volumes.delete({ id: 'vol' } as never)],
  ['volumes.delete(blank)', (client) => client.v2.volumes.delete(' ')],
  // The contract's size_gib is an int32 (twin in test_argument_checks.py).
  ['volumes.create sizeGib above int32', (client) => client.v2.volumes.create({ name: 'v', sizeGib: 2 ** 31 })],
  // The API rejects an empty allowedDomains (twin in test_argument_checks.py).
  ['secrets.set allowedDomains empty', (client) => client.v2.secrets.set('K', 'v', { allowedDomains: [] })],
  ['ports.expose public as a string', (_client, api) => handle(api).ports.expose(8080, { public: 'false' as never })],
  ['createAccessToken operations not an array', (_client, api) => handle(api).createAccessToken({ operations: 'toolbox' as never })],
  ['createAccessToken operations with a number', (_client, api) => handle(api).createAccessToken({ operations: [1] as never })],
  ['ports[].proto sctp', (client) => client.v2.create({ ports: [{ port: 3000, proto: 'sctp' as never }] })],
  ['createFromBuild idleExpirySeconds as a string', (client) => client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { idleExpirySeconds: '60' as never })],
  ['images.build cpuArch as a number', (client) => client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { cpuArch: 5 as never })],
  ['captureImage idleExpirySeconds 1.5', (_client, api) => handle(api).captureImage({ idleExpirySeconds: 1.5 })],
  // Argument types the SDK used to forward: the Python twin pins the same rows.
  ['secrets.set ifNotExists a string', (client) => client.v2.secrets.set('K', 'v', { allowedDomains: ['*'], ifNotExists: 'false' as never })],
  ['secrets.set value a number', (client) => client.v2.secrets.set('K', 5 as never, { allowedDomains: ['*'] })],
  ['secrets.set description a number', (client) => client.v2.secrets.set('K', 'v', { allowedDomains: ['*'], description: 5 as never })],
  ['images.pull wait a string', (client) => client.v2.images.pull('alpine', { wait: 'x' as never })],
  ['images.pull cpuArch bogus', (client) => client.v2.images.pull('alpine', { cpuArch: 'bogus' as never })],
  ['images.build idleExpirySeconds 0', (client) => client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { idleExpirySeconds: 0 })],
  ['images.build idleExpirySeconds -2', (client) => client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { idleExpirySeconds: -2 })],
  ['list limit a string', (client) => client.v2.list({ limit: 'x' as never })],
  ['list limit 0', (client) => client.v2.list({ limit: 0 })],
  ['list limit 201', (client) => client.v2.list({ limit: 201 })],
  ['list cursor a number', (client) => client.v2.list({ cursor: 5 as never })],
  ['search namePrefix a number', (client) => client.v2.search({ namePrefix: 5 as never })],
  ['search limit a string', (client) => client.v2.search({ limit: 'x' as never })],
  ['search minCores a string', (client) => client.v2.search({ minCores: 'x' as never })],
  ['search minMemoryMib 1.5', (client) => client.v2.search({ minMemoryMib: 1.5 })],
  ['search spot a string', (client) => client.v2.search({ spot: 'yes' as never })],
  ['search sort bogus', (client) => client.v2.search({ sort: 'x' as never })],
  ['search order bogus', (client) => client.v2.search({ order: 'up' as never })],
  ['images.list limit a string', (client) => client.v2.images.list({ limit: 'x' as never })],
  ['images.list name a number', (client) => client.v2.images.list({ name: 5 as never })],
  ['images.list platform bogus', (client) => client.v2.images.list({ platform: 'yes' as never })],
  ['snapshots.list name a number', (client) => client.v2.snapshots.list({ name: 5 as never })],
  ['secrets.list limit -1', (client) => client.v2.secrets.list({ limit: -1 })],
  ['volumes.list cursor a number', (client) => client.v2.volumes.list({ cursor: 5 as never })],
  ['reservations.list limit 0', (client) => client.v2.reservations.list({ limit: 0 })],
  ['ports.listEndpoints limit 0', (_client, api) => handle(api).ports.listEndpoints({ limit: 0 })],
  // A lone surrogate in a query value, and a header value outside printable
  // ASCII (the Python twin pins the same rows).
  ['list cursor with a lone surrogate', (client) => client.v2.list({ cursor: 'a\ud800b' })],
  ['macModels.catalog region a number', (client) => client.v2.macModels.catalog({ region: 5 as never })],
  ['macModels.catalog region a list', (client) => client.v2.macModels.catalog({ region: ['a', 'b'] as never })],
  ['macModels.catalog region with a lone surrogate', (client) => client.v2.macModels.catalog({ region: 'a\ud800b' })],
  ['volumes.get create a string', (client) => client.v2.volumes.get('v', { create: 'false' as never })],
  ['volumes.get create a list', (client) => client.v2.volumes.get('v', { create: [] as never })],
  ['volumes.get idOnly a string', (client) => client.v2.volumes.get('v', { idOnly: 'no' as never })],
  ['volumes.get create null', (client) => client.v2.volumes.get('v', { create: null as never })],
  ['search sandboxClass with a lone surrogate', (client) => client.v2.search({ sandboxClass: 'a\ud800b' as never })],
  ['search region with a lone surrogate', (client) => client.v2.search({ region: 'a\ud800b' })],
  ['search createdStartAt with a lone surrogate', (client) => client.v2.search({ createdStartAt: '2026-01-01T00:00:00Z\ud800' })],
  ['search region a number', (client) => client.v2.search({ region: 5 as never })],
  ['search region an object', (client) => client.v2.search({ region: { r: 'x' } as never })],
  ['search region [1]', (client) => client.v2.search({ region: [1] as never })],
  ['search sandboxClass a number', (client) => client.v2.search({ sandboxClass: 5 as never })],
  ['search sandboxClass an object', (client) => client.v2.search({ sandboxClass: { linux: 'x' } as never })],
  ['search sandboxClass [1]', (client) => client.v2.search({ sandboxClass: [1] as never })],
  ['search sandboxClass bogus', (client) => client.v2.search({ sandboxClass: 'bogus' as never })],
  ['search createdStartAt not RFC 3339', (client) => client.v2.search({ createdStartAt: 'yesterday' })],
  ['volumes.create name U+001C', (client) => client.v2.volumes.create({ name: '\u001c' })],
  ['create volumes[].volumeId U+001C', (client) => client.v2.create({ volumes: [{ volumeId: '\u001c', mountPath: '/m' }] })],
  ['search namePrefix with a lone surrogate', (client) => client.v2.search({ namePrefix: 'a\ud800b' })],
  ['images.list name with a lone surrogate', (client) => client.v2.images.list({ name: 'a\ud800b' })],
  ['create idempotencyKey non-ASCII', (client) => client.v2.create({ idempotencyKey: 'k\u00e9y' })],
  ['create region non-ASCII', (client) => client.v2.create({ region: 'r\u00e9gion' })],
  ['findByName region with a newline', (client) => client.v2.findByName('x', { region: 'a\nb' })],
  ['reservations.renew idempotencyKey non-ASCII', (client) => client.v2.reservations.renew('res', { idempotencyKey: 'k\u00e9y' })],
  ['resetNetworkPolicy ifMatch non-ASCII', (_client, api) => handle(api).resetNetworkPolicy({ ifMatch: '\u00e9' })],
  // Python's `anonymous` default is False and it refuses None.
  ['images.list anonymous null', (client) => client.v2.images.list({ anonymous: null as never })],
  ['images.get timeoutSeconds -1 without wait', (client) => client.v2.images.get('img', { timeoutSeconds: -1 })],
  ['images.get timeoutSeconds -1 with wait', (client) => client.v2.images.get('img', { wait: 'ready', timeoutSeconds: -1 })],
  ['images.get timeoutSeconds NaN without wait', (client) => client.v2.images.get('img', { timeoutSeconds: Number.NaN })],
  ['snapshots.get timeoutSeconds -1 without wait', (client) => client.v2.snapshots.get('snap', { timeoutSeconds: -1 })],
  ['snapshots.get timeoutSeconds -1 with wait', (client) => client.v2.snapshots.get('snap', { wait: 'ready', timeoutSeconds: -1 })],
  ['snapshots.get timeoutSeconds as a string without wait', (client) => client.v2.snapshots.get('snap', { timeoutSeconds: '5' as never })],
]

it.each(REFUSED)('refuses %s with BadRequestError before any request', async (_name, call) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const error = await call(client, api).then(() => undefined, (failure: unknown) => failure)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it('takes createTimeoutSeconds null as omitted, as Python takes None', async () => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await client.v2.create({ createTimeoutSeconds: null as never, waitSeconds: 0 })
    expect(JSON.parse(sentRequest(api, 1).body)).not.toHaveProperty('create_timeout_seconds')
  } finally {
    await client.close()
    await api.close()
  }
})

// Python's str.isspace() set: U+001C and U+0085 are whitespace there and not
// to JavaScript's trim(); U+FEFF is the reverse. Both SDKs follow Python: a
// blank name is refused, any other is stamped.
it('refuses the name "\u001c\u0085" as blank and stamps "\ufeff", as Python does', async () => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.create({ name: '\u001c\u0085', waitSeconds: 0 })).rejects.toMatchObject({ message: 'name must be a non-empty string when given', code: 'BAD_REQUEST' })
    expect(api.requests).toHaveLength(0)
    await client.v2.create({ name: '\ufeff', waitSeconds: 0 })
    const request = sentRequest(api, 1)
    const body = JSON.parse(request.body) as { labels?: Record<string, string> }
    expect(body.labels?.name).toBe('\ufeff')
    expect(String(request.headers['idempotency-key']).startsWith('name')).toBe(true)
  } finally {
    await client.close()
    await api.close()
  }
})

it('refuses a name that needs Web Crypto where there is none, before any request', async () => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  const crypto = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  const versions = Object.getOwnPropertyDescriptor(process, 'versions')
  // A browser page on a non-secure origin: no crypto.subtle, and not Node.js.
  Object.defineProperty(globalThis, 'crypto', { value: undefined, configurable: true })
  Object.defineProperty(process, 'versions', { value: {}, configurable: true })
  try {
    await expect(client.v2.create({ name: 'ünïcode', waitSeconds: 0 })).rejects.toMatchObject({
      statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK', message: expect.stringContaining('Web Crypto'),
    })
    expect(api.requests).toEqual([])
  } finally {
    if (crypto) Object.defineProperty(globalThis, 'crypto', crypto)
    if (versions) Object.defineProperty(process, 'versions', versions)
    await client.close()
    await api.close()
  }
})

it.each([
  ['listEndpoints', (_client: Daytona, api: FakeApi) => handle(api).ports.listEndpoints(Object.create({ limit: 5 }) as never), { ports: [], next_cursor: null }, 'limit=5'],
  ['list', (client: Daytona) => client.v2.list(Object.create({ limit: 3 }) as never), { sandboxes: [], next_cursor: null }, 'limit=3'],
  ['search', (client: Daytona) => client.v2.search(Object.create({ namePrefix: 'ci' }) as never), { sandboxes: [], total: 0, total_relation: 'eq' }, 'name_prefix=ci'],
  ['secrets.list', (client: Daytona) => client.v2.secrets.list(Object.create({ limit: 2 }) as never), { secrets: [], next_cursor: null }, 'limit=2'],
])('forwards an inherited %s option, as it validates it', async (_name, call, body, query) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await call(client, api)
    expect(sentRequest(api, 1).path).toContain(query)
  } finally {
    await client.close()
    await api.close()
  }
})

it('keeps a server 408 during the build wait as the server error, as Python does', async () => {
  const client = clientAt('http://127.0.0.1:9')
  try {
    const images = client.v2.images
    jest.spyOn(images, 'build').mockResolvedValue(new Image(INTERNAL, imageWire({ status: 'building' })))
    jest.spyOn(images, 'get').mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80))
      throw new RequestTimeoutError('server timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'DAYTONA_API' })
    })
    await expect(client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { buildTimeoutSeconds: 0.02 })).rejects.toMatchObject({
      message: 'server timeout', source: 'DAYTONA_API',
    })
  } finally {
    jest.restoreAllMocks()
    await client.close()
  }
})

it('pauses between early non-terminal answers of an image pull instead of polling hot', async () => {
  const api = await startFakeApi([
    { status: 200, body: { id: 'img-one', status: 'pending' } },
    ...Array.from({ length: 50 }, () => ({ status: 200, body: imageWire({ status: 'building' }) })),
  ])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.images.pull('alpine', { timeoutSeconds: 1.5 })).rejects.toBeInstanceOf(RequestTimeoutError)
    // The submission, the first read, then one hold about every second.
    expect(api.requests.length).toBeLessThanOrEqual(5)
  } finally {
    await client.close()
    await api.close()
  }
}, 10_000)

it('ends a ready wait whose first hold outlives the deadline with RequestTimeoutError', async () => {
  const server = createServer(() => { /* never answers */ })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  const client = clientAt(`http://127.0.0.1:${address.port}`)
  try {
    await expect(client.v2.images.get('img', { wait: 'ready', timeoutSeconds: 0.05 })).rejects.toMatchObject({
      constructor: RequestTimeoutError, statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK',
    })
  } finally {
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}, 15_000)

const RESERVATION = {
  id: 'res', org_id: 'o', project_id: 'p', region_id: 'r', status: 'active', origin: 'explicit', auto_renew: true,
  slots_used: 0, period_n: 1, period_seconds: 86400, renewal_pending: false, created_at: '2026-01-01T00:00:00Z',
  resources_used: { cores: 0, memory_mib: 0, disk_mib: 0 },
}

it.each([
  ['a blank region and model', (client: Daytona) => client.v2.reservations.create({ region: '', model: '' }), RESERVATION, { region_id: '', model: '' }],
  ['count 100 and minCount equal to count', (client: Daytona) => client.v2.reservations.createBulk({ region: 'r', count: 100, minCount: 100 }), { requested: 100, reserved: 0, reservations: [] }, { count: 100, min_count: 100 }],
  ['count 1', (client: Daytona) => client.v2.reservations.createBulk({ region: ' ', count: 1 }), { requested: 1, reserved: 0, reservations: [] }, { region_id: ' ', count: 1 }],
] as const)('sends a reservation with %s, as the contract allows', async (_name, call, response, expected) => {
  const api = await startFakeApi([{ status: 200, body: response }])
  const client = clientAt(api.url)
  try {
    await call(client)
    expect(JSON.parse(sentRequest(api, 1).body)).toMatchObject(expected)
  } finally {
    await client.close()
    await api.close()
  }
})

const CAPPED_WAITS = [
  ['create', 1e300], ['create', 2_147_484],
  ['createFromSnapshot', 1e300], ['createFromSnapshot', 2_147_484],
  ['createMacos', 1e300], ['createMacos', 2_147_484],
] as const

it.each(CAPPED_WAITS)('%s sends a wait of %d as the timer maximum, with no client deadline on the POST', async (route, waitSeconds) => {
  // The POST is answered after 1.5 s: past the 1 s request timeout.
  const bodies: string[] = []
  const timers = new Set<ReturnType<typeof setTimeout>>()
  const server = createServer((request, response) => {
    let body = ''
    request.on('data', (chunk: Buffer) => { body += chunk.toString('utf8') })
    request.on('end', () => {
      bodies.push(body)
      timers.add(setTimeout(() => {
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ id: 'sbx', status: 'running' }))
      }, 1_500))
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  const client = clientAt(`http://127.0.0.1:${address.port}`, { requestTimeoutSeconds: 1 })
  try {
    const source = route === 'create' ? 'img_1' : route === 'createFromSnapshot' ? 'snap_1' : 'sonoma'
    const sandbox = await client.v2[route](source, { waitSeconds })
    expect(sandbox.id).toBe('sbx')
    expect(bodies).toHaveLength(1)
    expect(JSON.parse(bodies[0] ?? '')).toMatchObject({ wait_seconds: 2_147_483 })
  } finally {
    for (const timer of timers) clearTimeout(timer)
    await client.close()
    server.closeAllConnections()
    server.close()
  }
}, 10_000)

// Every id or name a caller puts into a request path in the platform services
// (the Python twin pins the same calls): a string that is not blank by
// Python's whitespace set, refused with BadRequestError naming the argument.
const PATH_CALLS: ReadonlyArray<readonly [string, string, (client: Daytona, api: FakeApi, value: never) => Promise<unknown>]> = [
  ['images.get', 'imageId', (client, _api, value) => client.v2.images.get(value)],
  ['images.get wait', 'imageId', (client, _api, value) => client.v2.images.get(value, { wait: 'ready', timeoutSeconds: 1 })],
  ['images.delete', 'imageId', (client, _api, value) => client.v2.images.delete(value)],
  ['images.promote id', 'imageId', (client, _api, value) => client.v2.images.promote(value, 'named')],
  ['images.promote name', 'name', (client, _api, value) => client.v2.images.promote('img', value)],
  ['images.followBuildLogs', 'imageId', (client, _api, value) => client.v2.images.followBuildLogs(value, { onLogs: () => undefined })],
  ['snapshots.get', 'snapshotId', (client, _api, value) => client.v2.snapshots.get(value)],
  ['snapshots.delete', 'snapshotId', (client, _api, value) => client.v2.snapshots.delete(value)],
  ['volumes.get', 'nameOrId', (client, _api, value) => client.v2.volumes.get(value)],
  ['volumes.delete', 'nameOrId', (client, _api, value) => client.v2.volumes.delete(value)],
  ['secrets.get', 'name', (client, _api, value) => client.v2.secrets.get(value)],
  ['secrets.set', 'name', (client, _api, value) => client.v2.secrets.set(value, 'value', { allowedDomains: ['*'] })],
  ['secrets.delete', 'name', (client, _api, value) => client.v2.secrets.delete(value)],
  ['reservations.get', 'id', (client, _api, value) => client.v2.reservations.get(value)],
  ['reservations.renew', 'id', (client, _api, value) => client.v2.reservations.renew(value)],
  ['reservations.update', 'id', (client, _api, value) => client.v2.reservations.update(value, { autoRenew: true })],
  ['getLimits', 'organizationId', (client, _api, value) => client.v2.getLimits({ organizationId: value })],
  ['createSnapshot', 'name', (_client, api, value) => handle(api).createSnapshot(value)],
  ['sandbox.volumes.pull', 'volumeId', (_client, api, value) => withToolbox(api).volumes.pull(value, { requestId: 'r' })],
]
// Dot segments too: URL resolution removes them, so the request would reach another route.
const PATH_VALUES: ReadonlyArray<readonly [string, unknown]> = [['null', null], ['undefined', undefined], ['a number', 5], ['empty', ''], ['blank', ' \t\u2028'], ['a dot', '.'], ['a dot-dot', '..']]
// `getLimits({ organizationId: undefined | null })` is the key's own
// organization (Python's `None`), not a refusal; the names `promote()` and
// `createSnapshot()` send go in a body, not a path, so a dot segment is a
// valid name there.
const BODY_NAMES = new Set(['images.promote name', 'createSnapshot'])
const PATH_CASES = PATH_CALLS.flatMap(([name, argument, call]) => PATH_VALUES
  .filter(([label]) => !(name === 'getLimits' && (label === 'undefined' || label === 'null')))
  .filter(([label]) => !(BODY_NAMES.has(name) && label.includes('dot')))
  .map(([label, value]) => [`${name} with ${label}`, argument, call, value] as const))

it.each(PATH_CASES)('refuses %s as the path id or name before any request', async (_name, argument, call, value) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const error = await call(client, api, value as never).then(() => undefined, (failure: unknown) => failure)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(new RegExp(`^${argument} must `)) })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['images.get', 'imageId', (client: Daytona) => client.v2.images.get('a\ud800b')],
  ['secrets.set', 'name', (client: Daytona) => client.v2.secrets.set('a\ud800b', 'value', { allowedDomains: ['*'] })],
] as const)('refuses a %s path value with a lone surrogate before any request', async (_name, argument, call) => {
  // It has no UTF-8 form: refused as Python refuses it, never a raw URIError.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: `${argument} must not contain a surrogate code point` })
    expect(api.requests).toEqual([])
    // A valid surrogate pair is text, and passes the check.
    expect(() => pathSegment('a\ud83d\ude00b', argument)).not.toThrow()
  } finally {
    await client.close()
    await api.close()
  }
})

// SAFETY: every `as never` below is a deliberately invalid plain-JavaScript argument.
it.each([
  ['images', 'onLogs', {}],
  ['images', 'onLogs', { onLogs: 'x' }],
  ['images', 'onEvent', { onLogs: (): void => undefined, onEvent: 5 }],
  ['sandbox', 'onLogs', {}],
  ['sandbox', 'onLogs', { onLogs: 'x' }],
  ['sandbox', 'onEvent', { onLogs: (): void => undefined, onEvent: 5 }],
] as const)('%s.followBuildLogs refuses a non-function %s before any request', async (form, argument, options) => {
  // The Python twin covers the same calls.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const call = form === 'images' ? client.v2.images.followBuildLogs('img', options as never) : handle(api).followBuildLogs(options as never)
    await expect(call).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(new RegExp(`^${argument} must be a function`)) })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it('accepts the contract bounds: limit 200, idleExpirySeconds -1, cpuArch arm64, ifNotExists true', async () => {
  // The Python twin sends the same.
  const secret = { name: 'K', allowed_domains: ['*'], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
  const api = await startFakeApi([
    { status: 200, body: { sandboxes: [], next_cursor: null } },
    { status: 200, body: { id: 'img', status: 'pending' } },
    { status: 500, body: { message: 'stop' } },
    { status: 200, body: secret },
  ])
  const client = clientAt(api.url)
  try {
    await client.v2.list({ limit: 200, cursor: 'c' })
    await expect(client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { idleExpirySeconds: -1, cpuArch: 'arm64' })).rejects.toMatchObject(SERVER_STOP)
    await client.v2.secrets.set('K', 'v', { allowedDomains: ['*'], ifNotExists: true })
    expect(sentRequest(api, 4, 0).path).toBe('/sandboxes/search?cursor=c&limit=200')
    expect(JSON.parse(sentRequest(api, 4, 1).body)).toMatchObject({ idle_expiry_seconds: -1, cpu_arch: 'arm64' })
    expect(sentRequest(api, 4, 3).headers['if-none-match']).toBe('*')
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['volumes.create name U+FEFF', (client: Daytona) => client.v2.volumes.create({ name: '\ufeff' })],
  ['create volumes[].mountPath U+FEFF', (client: Daytona) => client.v2.create({ volumes: [{ volumeId: 'v', mountPath: '\ufeff' }] })],
] as const)('sends %s: U+FEFF is not Python whitespace', async (_name, call) => {
  // Python's whitespace set (U+FEFF is not in it, U+001C is); Python tests the same set.
  const api = await startFakeApi([{ status: 500, body: { message: 'stop' } }])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject(SERVER_STOP)
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it('sends valid search classes and timestamps', async () => {
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: 0, total_relation: 'eq', next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    await client.v2.search({ sandboxClass: ['linux', 'macos'], createdStartAt: '2026-01-01T00:00:00.5+02:00' })
    const query = new URL(sentRequest(api, 1).path, 'http://x').searchParams
    expect([query.get('class'), query.get('created_start_at')]).toEqual(['linux,macos', '2026-01-01T00:00:00.5+02:00'])
  } finally {
    await client.close()
    await api.close()
  }
})

it('sends a search label with a lone surrogate JSON-escaped', async () => {
  // Labels are sent as JSON, which escapes a lone surrogate (as a body does).
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: 0, total_relation: 'eq', next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    await client.v2.search({ labels: { team: 'a\ud800b' } })
    expect(sentRequest(api, 1).path).toContain('%5Cud800')
  } finally {
    await client.close()
    await api.close()
  }
})

const TIMESTAMPS = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'search-timestamps.json'), 'utf8')) as { readonly refused: readonly string[]; readonly sent: readonly string[]; readonly refusedAsDates: readonly string[] }
const EMPTY_SEARCH = { sandboxes: [], total: 0, total_relation: 'eq', next_cursor: null }

it.each(TIMESTAMPS.refused)('refuses the search timestamp %s before any request, as Python does', async (bound) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.search({ createdStartAt: bound })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', message: expect.stringContaining('RFC 3339') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(TIMESTAMPS.sent)('sends the search timestamp %s', async (bound) => {
  const api = await startFakeApi([{ status: 200, body: EMPTY_SEARCH }])
  const client = clientAt(api.url)
  try {
    await client.v2.search({ createdStartAt: bound })
    expect(new URL(sentRequest(api, 1).path, 'http://x').searchParams.get('created_start_at')).toBe(bound)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([[2048, true], [2049, false]] as const)('bounds a build-log cursor of %d astral characters in code points', async (count, accepted) => {
  // 2048 astral characters (4096 UTF-16 units) pass in both SDKs; 2049 do not.
  const api = await startFakeApi([{ status: 404, body: { code: 'NOT_FOUND', message: 'stop here' } }])
  const client = clientAt(api.url)
  try {
    const error = await client.v2.images.followBuildLogs('img', { onLogs: () => undefined, cursor: '\u{1F600}'.repeat(count) }).then(() => undefined, (failure: unknown) => failure)
    expect(error instanceof BadRequestError).toBe(!accepted)
    expect(api.requests).toHaveLength(accepted ? 1 : 0)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['getLimits', (client: Daytona) => client.v2.getLimits({ organizationId: 'a\ud800b' })],
  ['get', (client: Daytona) => client.v2.get('a\ud800b')],
  ['stop', (client: Daytona) => client.v2.stop('a\ud800b')],
] as const)('refuses a lone surrogate in the %s id before any request', async (_name, call) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', message: expect.stringContaining('must not contain a surrogate code point') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['secrets.delete ..', 'name', (client: Daytona) => client.v2.secrets.delete('..')],
  ['volumes.get .', 'nameOrId', (client: Daytona) => client.v2.volumes.get('.')],
  ['stop ..', 'sandbox', (client: Daytona) => client.v2.stop('..')],
  ['delete .', 'sandbox', (client: Daytona) => client.v2.delete('.')],
  ['get ..', 'sandboxId', (client: Daytona) => client.v2.get('..')],
] as const)('refuses the dot segment id of %s before any request', async (_name, argument, call) => {
  // `secrets.delete('..')` would otherwise go to another route: URL
  // resolution removes the dot segment.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ name: 'BadRequestError', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: `${argument} must not be '.' or '..'` })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([['create', 'x86_64'], ['create', 5], ['create', ''], ['createFromBuild', 'x86_64'], ['createFromBuild', 5], ['createFromBuild', '']] as const)('%s refuses cpuArch %j before any request', async (route, arch) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const call = route === 'create' ? client.v2.create({ cpuArch: arch as never }) : client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { cpuArch: arch as never })
    await expect(call).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', message: expect.stringContaining('cpuArch must be') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['amd64', 'arm64'] as const)('sends a create cpuArch of %s', async (arch) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await client.v2.create('img_1', { cpuArch: arch, waitSeconds: 0 })
    expect(JSON.parse(sentRequest(api, 1).body)).toMatchObject({ cpu_arch: arch })
  } finally {
    await client.close()
    await api.close()
  }
})

// One header rule in both SDKs (the Python twin pins the same rows): a padded
// value is refused, never stripped; a blank idempotency key or ETag is
// refused; a blank region selects the default region, as create does.
const PADDED_HEADERS = [
  ['create region', 'region', (client: Daytona, _api: FakeApi, value: string) => client.v2.create({ region: value })],
  ['findByName region', 'region', (client: Daytona, _api: FakeApi, value: string) => client.v2.findByName('x', { region: value })],
  ['create idempotencyKey', 'idempotencyKey', (client: Daytona, _api: FakeApi, value: string) => client.v2.create({ idempotencyKey: value })],
  ['reservations.create idempotencyKey', 'idempotencyKey', (client: Daytona, _api: FakeApi, value: string) => client.v2.reservations.create({ region: 'r', idempotencyKey: value })],
  ['resetNetworkPolicy ifMatch', 'ifMatch', (_client: Daytona, api: FakeApi, value: string) => handle(api).resetNetworkPolicy({ ifMatch: value })],
] as const
const PADDED_CASES = PADDED_HEADERS.flatMap(([name, argument, call]) => [' us', 'us ', ' ']
  .filter((value) => !(argument === 'region' && value === ' '))
  .map((value) => [`${name} ${JSON.stringify(value)}`, argument, call, value] as const))

it.each(PADDED_CASES)('refuses a padded header value: %s', async (_name, argument, call, value) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(call(client, api, value)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(new RegExp(`^${argument} must `)) })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([['create', ''], ['create', '  '], ['create', ' LOCAL '], ['findByName', ''], ['findByName', '  '], ['findByName', ' LOCAL ']] as const)('%s with region %j selects the default region', async (call, region) => {
  const body = call === 'create' ? { id: 'sbx', status: 'running' } : { sandboxes: [], total: 0, total_relation: 'eq', next_cursor: null }
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    if (call === 'create') await client.v2.create('img_1', { region, waitSeconds: 0 })
    else await client.v2.findByName('x', { region })
    const request = sentRequest(api, 1)
    expect(request.headers['x-daytona-region']).toBeUndefined()
    if (call === 'create') expect(JSON.parse(request.body)).not.toHaveProperty('region_id')
  } finally {
    await client.close()
    await api.close()
  }
})

it('expireSignedPreviewLinks takes no arguments, as Python does', () => {
  expect(PortsService.prototype.expireSignedPreviewLinks.length).toBe(0)
})

it.each([['year 0', new Date('0000-06-01T00:00:00Z')], ['year 10000', new Date('+010000-01-01T00:00:00Z')]] as const)('refuses a Date bound in %s, as the string rule does', async (_name, bound) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.search({ createdStartAt: bound })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('without a leap second') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(TIMESTAMPS.refusedAsDates)('refuses %s as a Date: its UTC instant falls outside years 0001-9999', async (bound) => {
  // The shared table, as Python reads it with datetime.fromisoformat().
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.search({ createdStartAt: new Date(bound) })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('without a leap second') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

const imageCall = (client: Daytona, route: 'build' | 'pull', region: unknown) => route === 'build'
  ? client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { region: region as never })
  : client.v2.images.pull('alpine', { region: region as never, wait: false })

it.each((['build', 'pull'] as const).flatMap((route) => ['', '  ', 'local', ' LOCAL '].map((region) => [route, region] as const)))('images.%s with region %j sends no region_id', async (route, region) => {
  const api = await startFakeApi([{ status: 500, body: { message: 'stop' } }])
  const client = clientAt(api.url)
  try {
    // The request went out and the fake's own 500 ended the call (not a refusal).
    await expect(imageCall(client, route, region)).rejects.toMatchObject(SERVER_STOP)
    expect(JSON.parse(sentRequest(api, 1).body)).not.toHaveProperty('region_id')
  } finally {
    await client.close()
    await api.close()
  }
})

it.each((['build', 'pull'] as const).flatMap((route) => [' us', 'us ', 5].map((region) => [route, region] as const)))('images.%s refuses region %j before any request', async (route, region) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(imageCall(client, route, region)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(/^region must /) })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['build', 'pull'] as const)('images.%s sends a named region', async (route) => {
  const api = await startFakeApi([{ status: 500, body: { message: 'stop' } }])
  const client = clientAt(api.url)
  try {
    await expect(imageCall(client, route, 'us')).rejects.toMatchObject(SERVER_STOP)
    expect(JSON.parse(sentRequest(api, 1).body)).toMatchObject({ region_id: 'us' })
  } finally {
    await client.close()
    await api.close()
  }
})

// The two SDKs check a call's arguments in the same order, so the first bad
// one is named alike (the Python twin pins the same rows).
it.each([
  ['volumes.get', (client: Daytona) => client.v2.volumes.get('', { create: 'x' as never }), 'nameOrId'],
  ['volumes.delete', (client: Daytona) => client.v2.volumes.delete('', { idOnly: 'x' as never }), 'nameOrId'],
  // An unknown key too: Python has no such check (an unknown keyword is a TypeError).
  ['volumes.delete with an unknown key', (client: Daytona) => client.v2.volumes.delete('', { bogus: 1 } as never), 'nameOrId'],
  ['reservations.renew', (client: Daytona) => client.v2.reservations.renew('', { idempotencyKey: ' k' }), 'id'],
  ['reservations.update', (client: Daytona) => client.v2.reservations.update('', { autoRenew: 'x' as never }), 'id'],
  ['secrets.set', (client: Daytona) => client.v2.secrets.set('K', 5 as never, { allowedDomains: 5 as never }), 'value'],
] as const)('%s names the first bad argument as Python does', async (_name, call, argument) => {
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(new RegExp(`^${argument} must `)) })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it('words a missing ifMatch as Python does', async () => {
  const api = await startFakeApi([])
  try {
    await expect(handle(api).resetNetworkPolicy({ ifMatch: '' })).rejects.toMatchObject({ message: 'ifMatch must be a non-empty string (the etag of the last read)' })
  } finally {
    await api.close()
  }
})

it.each([
  ['build', ''], ['build', ' '], ['build', '\t\u2028'], ['pull', ''], ['pull', ' '], ['pull', '\t\u2028'], ['captureImage', ' '],
] as const)('refuses a blank image name on %s (%j) before any request', async (call, name) => {
  // As promote() refuses one; the Python twin is in test_argument_checks.py.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    const pending = call === 'build' ? client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { name })
      : call === 'pull' ? client.v2.images.pull('alpine', { name, wait: false })
        : handle(api).captureImage({ name })
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'name must be a non-empty string' })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([2280, 2281, 22220])('still unexposes reserved port %d, as Python does', async (port) => {
  // The API refuses the daemon's ports only where a preview is made.
  const api = await startFakeApi([{ status: 204, body: undefined }])
  try {
    await handle(api).ports.unexpose(port)
    expect(api.requests.map((request) => [request.method, request.path])).toEqual([
      ['DELETE', `/sandboxes/sbx/ports/${port}`],
    ])
  } finally {
    await api.close()
  }
})
