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
