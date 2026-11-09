/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { createSandbox as createSandboxRequest } from '../src/v2/clientCreate'
import { RequestTimeoutError, ServiceUnavailableError } from '../src/v2/errors/DaytonaError'
import { WaitHub } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'

// A sandbox wait ends on a read of the server when the events stream is
// silent. The stream answers each subscribe with a snapshot and then sends no
// transitions (its reconcile is minutes away). The hub gets the budget less a
// final reserve; the reserve reads the state from the server, so a sandbox
// that reaches the state mid-wait is seen within the budget, and one that
// never does still times out. Timings are scaled down (the state is reached
// 400 ms in), and the hub's early probe is off, as it is in effect on a
// socket kept alive by keepalive frames. The Python twin is
// tests/test_wait_final_read.py.

const ID = 'sbx-region-a'
const REACHED_AFTER_MS = 400

class ServerSandbox {
  movedAt: number | undefined
  constructor(readonly before: string, readonly after: string, readonly reachedAfterMs: number | undefined) {}
  move(): void { this.movedAt = Date.now() }
  status(): string {
    if (this.movedAt === undefined || this.reachedAfterMs === undefined) return this.before
    return Date.now() - this.movedAt >= this.reachedAfterMs ? this.after : this.before
  }
}

/** Answers every subscribe with a snapshot of the current state, and nothing else. */
class SnapshotOnlyWebSocket {
  readyState = 1
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  private version = 0
  constructor(private readonly sandbox: ServerSandbox) {}
  send(data: string): void {
    const frame = JSON.parse(data) as { type?: string; ids?: Array<{ id: string }> }
    if (frame.type !== 'subscribe') return
    for (const entry of frame.ids ?? []) {
      this.version += 1
      const snapshot = { type: 'snapshot', id: entry.id, status: this.sandbox.status(), version: this.version, error: null }
      queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(snapshot) } as MessageEvent))
    }
  }
  close(code = 1000): void {
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: true } as CloseEvent)
  }
}

const detail = (status: string) => ({
  id: ID, class: 'linux', name: ID, status, cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a',
})

interface Harness {
  readonly api: AxiosInstance
  readonly hub: WaitHub
  readonly gets: Array<Record<string, unknown> | undefined>
  readonly sockets: () => number
}

function harness(sandbox: ServerSandbox): Harness {
  const api = createHttpClient('http://api.invalid', 'key')
  const gets: Array<Record<string, unknown> | undefined> = []
  jest.spyOn(api, 'get').mockImplementation(async (_url, config) => {
    gets.push(config?.params as Record<string, unknown> | undefined)
    return { data: detail(sandbox.status()), headers: {} }
  })
  jest.spyOn(api, 'post').mockImplementation(async (url: string) => {
    if (/\/events\/[^/]+\/ticket$/.test(url)) {
      return { data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' }, headers: {} }
    }
    sandbox.move()
    if (url === '/sandboxes') return { data: { id: ID, status: sandbox.status(), wait_cap_seconds: 0 }, headers: {} }
    return { data: {}, headers: {} }
  })
  jest.spyOn(api, 'delete').mockImplementation(async () => {
    sandbox.move()
    return { data: {}, headers: {} }
  })
  let sockets = 0
  const factory = () => {
    sockets += 1
    return new SnapshotOnlyWebSocket(sandbox) as unknown as WebSocket
  }
  // The production reconcile (60 s) never fires within these budgets.
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 60_000 }, factory, api, { earlyProbeMs: 0 })
  hub.attach()
  return { api, hub, gets, sockets: () => sockets }
}

const handle = (h: Harness) => createSandbox(ID, h.api, undefined, 'key', undefined, {}, undefined, undefined, h.hub)

type Call = (h: Harness) => Promise<unknown>
const LIFECYCLE: Array<[string, string, string, Call]> = [
  ['start', 'stopped', 'running', (h) => handle(h).start({ timeoutSeconds: 1 })],
  ['stop', 'running', 'stopped', (h) => handle(h).stop({ timeoutSeconds: 1 })],
  ['delete', 'deleting', 'deleted', (h) => handle(h).delete({ timeoutSeconds: 1 })],
]

let savedEnv: string | undefined
beforeEach(() => {
  savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
  process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
})
afterEach(() => {
  jest.restoreAllMocks()
  if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
  else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
})

async function timed<T>(h: Harness, call: () => Promise<T>): Promise<{ result: T; elapsedMs: number }> {
  const started = Date.now()
  try {
    const result = await call()
    return { result, elapsedMs: Date.now() - started }
  } finally {
    await h.hub.detach()
    // The waits ran on the socket, not on the hub's polling fallback.
    expect(h.sockets()).toBeGreaterThanOrEqual(1)
  }
}

it.each(LIFECYCLE)('%s sees a state the stream never reports, within its budget', async (_verb, before, after, call) => {
  const h = harness(new ServerSandbox(before, after, REACHED_AFTER_MS))
  const { elapsedMs } = await timed(h, () => call(h))
  expect(elapsedMs).toBeLessThan(1_000)
})

it.each(LIFECYCLE)('%s still times out when the state never comes', async (_verb, before, after, call) => {
  const h = harness(new ServerSandbox(before, after, undefined))
  const { result, elapsedMs } = await timed(h, () => call(h).then(() => undefined, (error: unknown) => error))
  expect(result).toBeInstanceOf(RequestTimeoutError)
  expect(result).toMatchObject({ source: 'SDK', code: 'REQUEST_TIMEOUT' })
  expect(elapsedMs).toBeGreaterThanOrEqual(900)
  expect(elapsedMs).toBeLessThan(1_500)
})

function createWithWait(h: Harness, waitSeconds: number) {
  return createSandboxRequest(
    { api: h.api, buildSandbox: (source, options) => createSandbox(source, h.api, undefined, 'key', undefined, options, undefined, undefined, h.hub) },
    { path: '/sandboxes', body: { wait_seconds: waitSeconds }, metadata: { autoDelete: false, waitSeconds, image: 'img', sandboxClass: 'linux' } },
  )
}

it('create returns the sandbox started when the stream never reports it', async () => {
  const h = harness(new ServerSandbox('pending', 'running', REACHED_AFTER_MS))
  const { result, elapsedMs } = await timed(h, () => createWithWait(h, 2))
  expect(result.state).toBe('started')
  expect(elapsedMs).toBeLessThan(2_000)
})

it('create still returns the handle when the sandbox never starts', async () => {
  const h = harness(new ServerSandbox('pending', 'running', undefined))
  const { result, elapsedMs } = await timed(h, () => createWithWait(h, 1))
  expect(result.state).toBe('creating')
  expect(elapsedMs).toBeGreaterThanOrEqual(900)
  expect(elapsedMs).toBeLessThan(1_500)
})

it('gives the stream the budget less the reserve, then reads once', async () => {
  // No read before the hub's share ends: with the stream silent, the
  // reserve's long poll is the wait's only read.
  const h = harness(new ServerSandbox('stopped', 'running', REACHED_AFTER_MS))
  await timed(h, () => handle(h).start({ timeoutSeconds: 1 }))
  // start() reads the sandbox once before POST /start.
  expect(h.gets).toHaveLength(2)
  const final = h.gets[1]
  expect(final?.['wait']).toBe('running')
  // The hub had all but the reserve (a quarter of the 1 s budget).
  expect(Number(final?.['timeout_ms'])).toBeGreaterThan(0)
  expect(Number(final?.['timeout_ms'])).toBeLessThanOrEqual(260)
})

it('keeps the wait timeout when the read in the reserve fails', async () => {
  const h = harness(new ServerSandbox('stopped', 'running', undefined))
  const sandbox = handle(h)
  jest.spyOn(h.api, 'get').mockRejectedValue(new ServiceUnavailableError('busy', { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'DAYTONA_API' }))
  const { result } = await timed(h, () => sandbox.wait({ until: 'started', timeoutSeconds: 1 }).then(() => undefined, (error: unknown) => error))
  expect(result).toBeInstanceOf(RequestTimeoutError)
  expect(result).toMatchObject({ source: 'SDK', code: 'REQUEST_TIMEOUT' })
})
