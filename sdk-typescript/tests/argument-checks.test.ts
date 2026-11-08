/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import { BadRequestError, ImageBuilder, RequestTimeoutError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { Image } from '../src/v2/Image'
import { INTERNAL } from '../src/v2/internal'
import { pathSegment } from '../src/v2/pathSegment'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { FakeApi } from './networkPolicyTestServer'

// The argument rule for the client, lifecycle and platform-service area: a
// wrong type or value is a BadRequestError (400, BAD_REQUEST, SDK) before any
// request, as in Python (tests/test_argument_checks.py).

type Call = (client: Daytona, api: FakeApi) => Promise<unknown>
const handle = (api: FakeApi) => createSandbox({ id: 'sbx', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))
const withToolbox = (api: FakeApi) => createSandbox({ id: 'sbx', class: 'linux', status: 'started' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })
const unknownClass = (api: FakeApi) => createSandbox('sbx', createHttpClient(api.url, 'key'))

// SAFETY: every `as never` below is a deliberately invalid plain-JavaScript argument.
const REFUSED: readonly (readonly [string, Call])[] = [
  ['wait until a non-state', (_client, api) => handle(api).wait({ until: 'bogus' as never })],
  ['wait until a list with a non-state', (_client, api) => handle(api).wait({ until: ['started', 'bogus'] as never })],
  ['wait until a number', (_client, api) => handle(api).wait({ until: 5 as never })],
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
  ['create waitSeconds 1e300 is fine, createTimeoutSeconds above 2^53-1 is not', (client) => client.v2.create({ createTimeoutSeconds: 2 ** 53 })],
  ['create memory above int32 MiB', (client) => client.v2.create({ resources: { memory: 2_097_152 } })],
  ['create diskMax above int32 MiB', (client) => client.v2.create({ resources: { diskMax: 2_097_152 } })],
  ['reservations.update without autoRenew', (client) => client.v2.reservations.update('res', {} as never)],
  ['reservations.update autoRenew as a string', (client) => client.v2.reservations.update('res', { autoRenew: 'yes' as never })],
  ['volumes.delete(a Volume object)', (client) => client.v2.volumes.delete({ id: 'vol' } as never)],
  ['volumes.delete(blank)', (client) => client.v2.volumes.delete(' ')],
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
    expect(JSON.parse(api.requests[0]?.body ?? '{}')).not.toHaveProperty('create_timeout_seconds')
  } finally {
    await client.close()
    await api.close()
  }
})

// Python's str.isspace() set: U+001C and U+0085 are whitespace there and not
// to JavaScript's trim(); U+FEFF is the reverse. Both SDKs follow Python.
it.each([
  ['\u001c\u0085', false],
  ['\ufeff', true],
])('treats the name %j as %s a real name, as Python does', async (name, stamped) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    await client.v2.create({ name, waitSeconds: 0 })
    const body = JSON.parse(api.requests[0]?.body ?? '{}') as { labels?: Record<string, string> }
    expect(body.labels?.name !== undefined).toBe(stamped)
    expect(String(api.requests[0]?.headers['idempotency-key']).startsWith('name')).toBe(stamped)
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
    expect(api.requests[0]?.path).toContain(query)
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
  sandboxes_per_mac: 2, slots_used: 0, period_n: 1, period_seconds: 86400, renewal_pending: false, created_at: '2026-01-01T00:00:00Z',
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
    expect(JSON.parse(api.requests[0]?.body ?? '{}')).toMatchObject(expected)
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
    expect(JSON.parse(bodies[0] ?? '{}')).toMatchObject({ wait_seconds: 2_147_483 })
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
const PATH_VALUES: ReadonlyArray<readonly [string, unknown]> = [['null', null], ['undefined', undefined], ['a number', 5], ['empty', ''], ['blank', ' \t\u2028']]
// `getLimits({ organizationId: undefined | null })` is the key's own
// organization (Python's `None`), not a refusal.
const PATH_CASES = PATH_CALLS.flatMap(([name, argument, call]) => PATH_VALUES
  .filter(([label]) => !(name === 'getLimits' && (label === 'undefined' || label === 'null')))
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
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: `${argument} must not contain a lone surrogate` })
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
    await expect(client.v2.images.build(ImageBuilder.fromRegistry('alpine'), { idleExpirySeconds: -1, cpuArch: 'arm64' })).rejects.toBeDefined()
    await client.v2.secrets.set('K', 'v', { allowedDomains: ['*'], ifNotExists: true })
    expect(api.requests[0]?.path).toBe('/sandboxes?cursor=c&limit=200')
    expect(JSON.parse(api.requests[1]?.body ?? '{}')).toMatchObject({ idle_expiry_seconds: -1, cpu_arch: 'arm64' })
    expect(api.requests[3]?.headers['if-none-match']).toBe('*')
  } finally {
    await client.close()
    await api.close()
  }
})
