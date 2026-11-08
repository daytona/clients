/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import type { Server } from 'node:http'
import { DockerWaitTimeoutError, ImageBuilder, RequestTimeoutError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import type { Daytona } from '../src/Daytona'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'

// A wait budget above the timer maximum (about 24.8 days) is no deadline, for
// every wait of the client, lifecycle and platform services, in both SDKs.
// The clock jumps 36 days once the first wait read is answered (after the
// wait's deadline is armed): a real deadline of 3,000,000 s (about 34.7 days)
// would then have passed, and a budget of 2,000,000 s (23 days, a real
// deadline) does. The Python twin is
// tests/test_unbounded_waits.py.
const DAY_MS = 86_400_000
let offset = 0
const realNow = Date.now.bind(Date)

beforeEach(() => {
  offset = 0
  jest.spyOn(Date, 'now').mockImplementation(() => realNow() + offset)
})
afterEach(() => { jest.restoreAllMocks() })

async function serve(bodies: readonly unknown[], jumpAfter = 1): Promise<{ readonly url: string; readonly server: Server; readonly count: () => number }> {
  let served = 0
  const server = createServer((request, response) => {
    request.resume()
    request.on('end', () => {
      const body = bodies[Math.min(served, bodies.length - 1)]
      served++
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(body))
      if (served === jumpAfter) offset += 36 * DAY_MS
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  return { url: `http://127.0.0.1:${address.port}`, server, count: () => served }
}

const detail = (status: string, id = 'sbx') => ({ id, class: 'linux', name: id, status, cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a' })
const handle = (url: string) => createSandbox({ id: 'sbx', class: 'linux', status: 'running' }, createHttpClient(url, 'key'))
const withToolbox = (url: string) => createSandbox({ id: 'sbx', class: 'linux', status: 'running' }, createHttpClient(url, 'key'), undefined, 'key', undefined, { toolboxUrl: url, accessToken: 'tok' })

type Call = (client: Daytona, url: string, seconds: number) => Promise<unknown>
// What a call documents when its budget runs out: the exact timeout error it
// throws, or (fork) the handle it returns, short of `started`.
type Outcome = (abstract new (...args: never[]) => RequestTimeoutError) | 'handle'
// [name, answers, call, the answer after which the clock jumps (the first wait
// read), the outcome of a budget that runs out]
const CASES: ReadonlyArray<readonly [string, readonly unknown[], Call, number, Outcome]> = [
  ['sandbox.stop', [{}, detail('stopping'), detail('stopping'), detail('stopped')], (_client, url, seconds) => handle(url).stop({ timeoutSeconds: seconds }), 2, RequestTimeoutError],
  ['client.stop by id', [{}, detail('stopping'), detail('stopping'), detail('stopped')], (client, _url, seconds) => client.v2.stop('sbx', { timeoutSeconds: seconds }), 2, RequestTimeoutError],
  ['sandbox.wait', [detail('booting'), detail('booting'), detail('running')], (_client, url, seconds) => handle(url).wait({ until: 'started', timeoutSeconds: seconds }), 1, RequestTimeoutError],
  ['images.pull', [{ id: 'img', status: 'pending' }, imageWire({ id: 'img', status: 'pending' }), imageWire({ id: 'img', status: 'pending' }), imageWire({ id: 'img', status: 'pending' }), imageWire({ id: 'img', status: 'ready' })], (client, _url, seconds) => client.v2.images.pull('alpine', { timeoutSeconds: seconds }), 1, RequestTimeoutError],
  ['images.get wait ready', [imageWire({ id: 'img', status: 'building' }), imageWire({ id: 'img', status: 'building' }), imageWire({ id: 'img', status: 'ready' })], (client, _url, seconds) => client.v2.images.get('img', { wait: 'ready', timeoutSeconds: seconds }), 1, RequestTimeoutError],
  ['docker.waitUntilReady', [{ owner: 'daemon', state: 'starting' }, { owner: 'daemon', state: 'starting' }, { owner: 'daemon', state: 'ready' }], (_client, url, seconds) => withToolbox(url).docker.waitUntilReady({ timeoutSeconds: seconds }), 1, DockerWaitTimeoutError],
  ['createFromBuild buildTimeoutSeconds', [{ id: 'img', status: 'pending' }, imageWire({ id: 'img', status: 'building' }), imageWire({ id: 'img', status: 'building' }), imageWire({ id: 'img', status: 'building' }), imageWire({ id: 'img', status: 'ready' }), { id: 'sbx', status: 'running' }], (client, _url, seconds) => client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { buildTimeoutSeconds: seconds, waitSeconds: 0 }), 3, RequestTimeoutError],
  ['fork', [detail('running', 'src'), { id: 'f', status: 'pending', forked_from: 'src', toolbox_proxy_url: 'http://p' }, detail('booting', 'f'), detail('booting', 'f'), detail('running', 'f')], (_client, url, seconds) => createSandbox({ id: 'src', class: 'linux', status: 'running' }, createHttpClient(url, 'key')).fork({ waitSeconds: seconds }), 3, 'handle'],
]

describe.each(CASES)('%s', (_name, bodies, call, jumpAfter, outcome) => {
  it('waits with no deadline for a budget of 3,000,000 s, past 35 days', async () => {
    const { url, server, count } = await serve(bodies, jumpAfter)
    const client = clientAt(url)
    try {
      const result = await call(client, url, 3_000_000)
      // Every planned answer was read: the call waited through them all.
      expect(count()).toBe(bodies.length)
      if (_name === 'fork') expect(result).toMatchObject({ state: 'started' })
    } finally {
      await client.close()
      server.closeAllConnections()
      server.close()
    }
  }, 20_000)

  it('ends a real budget of 2,000,000 s once the clock passes it', async () => {
    const { url, server, count } = await serve(bodies, jumpAfter)
    const client = clientAt(url)
    try {
      const ended = await call(client, url, 2_000_000).then(
        (value) => ({ value }),
        (error: unknown) => ({ error }),
      )
      // The answer after which the clock jumps was served, so the deadline
      // passed only because of the jump, before the call read every answer.
      expect(count()).toBeGreaterThanOrEqual(jumpAfter)
      expect(count()).toBeLessThan(bodies.length)
      if (outcome === 'handle') {
        if (!('value' in ended)) throw new Error(`expected the handle, got ${String(ended.error)}`)
        // A fork returns its handle however the wait ends: a real deadline
        // leaves it at the state last read.
        expect(ended.value).toMatchObject({ id: 'f', state: 'starting' })
      } else {
        if (!('error' in ended)) throw new Error('expected the budget to run out')
        expect(ended.error).toBeInstanceOf(outcome)
        expect((ended.error as object).constructor).toBe(outcome)
        expect(ended.error).toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
      }
    } finally {
      await client.close()
      server.closeAllConnections()
      server.close()
    }
  }, 20_000)
})

it('keeps fork({ waitSeconds: 0 }) one request', async () => {
  const { url, server, count } = await serve([{ id: 'f', status: 'pending', forked_from: 'src', toolbox_proxy_url: 'http://p' }])
  try {
    await createSandbox('src', createHttpClient(url, 'key')).fork({ waitSeconds: 0 })
    expect(count()).toBe(1)
  } finally {
    server.closeAllConnections()
    server.close()
  }
})
