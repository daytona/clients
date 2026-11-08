/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { AxiosHeaders, type AxiosInstance } from 'axios'
import {
  DEFAULT_IDLE_CLOSE_WITHOUT_UNREF_MS,
  trackLoopHandle,
  MAX_SERVER_FRAME_BYTES,
  WaitHub,
  type WaitOutcome,
  toWsUrl,
  attachHub,
  detachHub,
  targetPredicate,
  regionOf,
  eventDestinationFromHeaders,
  _hubRegistryForTest,
} from '../src/v2/events'
import { sandboxState } from '../src/v2/http'
import {
  AuthenticationError,
  ForbiddenError,
  DaytonaError,
  NotFoundError,
  RequestTimeoutError,
  SandboxTerminalStateError,
  ServiceUnavailableError,
} from '../src/v2/errors/DaytonaError'
import type { SandboxState } from '../src/v2/types'
import type { EventsHubConfig } from '../src/v2/events'

/** What every create/start wait registers: reach `started`, reject on a
 *  terminal state (the SDK's DEFAULT_SANDBOX_TERMINAL_STATES). */
function startedPredicate() {
  return targetPredicate(
    new Set<SandboxState>(['started']),
    new Set<SandboxState>(['destroying', 'destroyed', 'error']),
  )
}

const API_URL = 'https://api.test'
const API_KEY = 'dtn_test'
const SANDBOX_ID = 'abc-regiona'

// ---------------------------------------------------------------------------
// Fake WebSocket: starts OPEN, supports inject/injectClose, records .sent
// ---------------------------------------------------------------------------

class FakeWebSocket {
  readyState = 1

  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  readonly sent: Array<{ readonly at: number; readonly data: string }> = []
  closeCode: number | undefined
  private _closed = false

  constructor(public readonly url = '', public readonly protocols: readonly string[] = []) {}

  send(data: string): void {
    if (!this._closed) this.sent.push({ at: Date.now(), data })
  }

  close(code = 1000): void {
    if (this._closed) return
    this._closed = true
    this.closeCode = code
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: code === 1000 } as CloseEvent)
  }

  inject(frame: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }

  injectRaw(data: string): void {
    this.onmessage?.({ data } as MessageEvent)
  }

  injectClose(code = 1006): void {
    if (this._closed) return
    this._closed = true
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: false } as CloseEvent)
  }

  sentFrames(): Record<string, unknown>[] {
    return this.sent.map(({ data }) => JSON.parse(data) as Record<string, unknown>)
  }

  sentFrameRecords(): Array<{ readonly at: number; readonly frame: Record<string, unknown> }> {
    return this.sent.map(({ at, data }) => ({ at, frame: JSON.parse(data) as Record<string, unknown> }))
  }
}

// ---------------------------------------------------------------------------
// FakeConnector: manages a queue of FakeWebSocket instances
// ---------------------------------------------------------------------------

interface FakeConnector {
  factory(url: string, protocols: readonly string[]): WebSocket
  connections: FakeWebSocket[]
  prepare(ws?: FakeWebSocket): FakeWebSocket
  get last(): FakeWebSocket
  get count(): number
}

function makeFakeConnector(): FakeConnector {
  const queue: FakeWebSocket[] = []
  const connections: FakeWebSocket[] = []
  const connector: FakeConnector = {
    factory(url, protocols) {
      const ws = queue.shift() ?? new FakeWebSocket(url, protocols)
      connections.push(ws)
      return ws as unknown as WebSocket
    },
    connections,
    prepare(ws?: FakeWebSocket) {
      const w = ws ?? new FakeWebSocket()
      queue.push(w)
      return w
    },
    get last() {
      return connections[connections.length - 1]!
    },
    get count() {
      return connections.length
    },
  }
  return connector
}

// ---------------------------------------------------------------------------
// mockAxios: jest.fn() mocks for post/get on an AxiosInstance-shaped object
// ---------------------------------------------------------------------------

function mockAxios(
  opts: { ticketStatus?: number; sandboxStatus?: string; ticketDelayMs?: number; getStatus?: number } = {},
): AxiosInstance {
  const { ticketStatus = 200, sandboxStatus = 'running', ticketDelayMs = 0, getStatus = 200 } = opts

  const postFn = jest.fn().mockImplementation(async (url: string) => {
    if (typeof url === 'string' && /\/events\/[^/]+\/ticket$/.test(url)) {
      if (ticketDelayMs > 0) await new Promise((r) => setTimeout(r, ticketDelayMs))
      if (ticketStatus === 404) throw new NotFoundError('Not Found', { statusCode: 404, code: 'NOT_FOUND', source: 'DAYTONA_API' })
      if (ticketStatus === 401) throw new AuthenticationError('Unauthorized', { statusCode: 401, code: 'UNAUTHORIZED', source: 'DAYTONA_API' })
      if (ticketStatus === 403) throw new ForbiddenError('Forbidden', { statusCode: 403, code: 'FORBIDDEN', source: 'DAYTONA_API' })
      if (ticketStatus >= 400) throw new DaytonaError(`ticket ${ticketStatus}`, { statusCode: ticketStatus })
      return { data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' }, headers: {} }
    }
    return { data: {}, headers: {} }
  })

  const getFn = jest.fn().mockImplementation(async () => {
    if (getStatus === 404) throw new NotFoundError('Not Found', { statusCode: 404, code: 'NOT_FOUND', source: 'DAYTONA_API' })
    if (getStatus >= 400) throw new DaytonaError(`get ${getStatus}`, { statusCode: getStatus })
    return {
      data: { status: sandboxStatus, id: SANDBOX_ID },
      headers: {},
    }
  })

  return { post: postFn, get: getFn, defaults: { timeout: 30_000 } } as unknown as AxiosInstance
}

function makeHub(
  fc: FakeConnector,
  opts: {
    enabled?: boolean
    reconcileIntervalMs?: number
    idleCloseMs?: number
    api?: AxiosInstance
    timings?: {
      openTimeoutMs?: number
      noFrameTimeoutMs?: number
      closeGraceMs?: number
      earlyProbeMs?: number
      clientPingAfterMs?: number
      subscribeFlushMs?: number
    }
  } = {},
): WaitHub {
  const { enabled = true, reconcileIntervalMs = 0, idleCloseMs } = opts
  const config: EventsHubConfig = { enabled, reconcileIntervalMs, idleCloseMs }
  const api = opts.api ?? mockAxios()
  return new WaitHub(
    API_URL,
    API_KEY,
    config,
    fc.factory.bind(fc) as unknown as (url: string, protocols: readonly string[]) => WebSocket,
    api,
    opts.timings,
  )
}

async function drain(iterations = 15): Promise<void> {
  for (let i = 0; i < iterations; i++) {
    await new Promise<void>((resolve) => setImmediate(resolve))
  }
}

// ---------------------------------------------------------------------------
// regionOf
// ---------------------------------------------------------------------------

// The jest setup file switches the hub off for polling suites (they count
// long-poll requests); this suite is the hub's own and runs with it on.
const savedEventsEnv = process.env['DAYTONA_SANDBOX_EVENTS']
beforeAll(() => {
  delete process.env['DAYTONA_SANDBOX_EVENTS']
})
afterAll(() => {
  if (savedEventsEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
  else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEventsEnv
})

describe('WaitHub: timer config clamps to Node-safe maxima', () => {
  test('reconcileIntervalMs and idleCloseMs clamp above 2^31-1', () => {
    const hub = makeHub(makeFakeConnector(), {
      reconcileIntervalMs: 3_000_000_000,
      idleCloseMs: 3_000_000_000,
    })
    const config = (hub as unknown as { config: { reconcileIntervalMs: number; idleCloseMs: number | undefined } }).config
    // The reconcile interval keeps headroom for its ±10% jitter.
    expect(config.reconcileIntervalMs).toBe(Math.floor(2_147_483_647 / 1.1))
    expect(config.reconcileIntervalMs * 1.1).toBeLessThanOrEqual(2_147_483_647)
    expect(config.idleCloseMs).toBe(2_147_483_647)
  })

  test('NaN falls back to the defaults instead of a 1 ms timer', () => {
    const hub = makeHub(makeFakeConnector(), { reconcileIntervalMs: Number.NaN, idleCloseMs: Number.NaN })
    const config = (hub as unknown as { config: { reconcileIntervalMs: number; idleCloseMs: number | undefined } }).config
    expect(config.reconcileIntervalMs).toBe(60_000)
    expect(config.idleCloseMs).toBeUndefined()
  })

  test('Infinity clamps instead of becoming a 1 ms timer', () => {
    const hub = makeHub(makeFakeConnector(), {
      reconcileIntervalMs: Number.POSITIVE_INFINITY,
      idleCloseMs: Number.POSITIVE_INFINITY,
    })
    const config = (hub as unknown as { config: { reconcileIntervalMs: number; idleCloseMs: number | undefined } }).config
    expect(config.reconcileIntervalMs).toBe(Math.floor(2_147_483_647 / 1.1))
    expect(config.idleCloseMs).toBe(2_147_483_647)
  })
})

// The socket URL keeps the API's base path — a deployment behind
// `https://host/platform/api` answers the upgrade there, not at the host
// root, and the ticket was minted through the full path. Same table as the
// Python twin.
describe('toWsUrl keeps the API base path', () => {
  test.each([
    ['https://api.example.com', 'regiona', 'wss://api.example.com/events/regiona'],
    ['https://host/platform/api', 'regiona', 'wss://host/platform/api/events/regiona'],
    ['https://host/platform/api/', 'regiona', 'wss://host/platform/api/events/regiona'],
    ['http://127.0.0.1:8080', '_', 'ws://127.0.0.1:8080/events/_'],
    ['http://127.0.0.1:8080/base', '_', 'ws://127.0.0.1:8080/base/events/_'],
    ['https://host/platform/api?trace=1', 'regiona', 'wss://host/platform/api/events/regiona'],
    ['https://host/platform/api#frag', 'regiona', 'wss://host/platform/api/events/regiona'],
  ])('%s + %s -> %s', (apiUrl, region, expected) => {
    expect(toWsUrl(apiUrl, region)).toBe(expected)
  })
})

// Region parsing matches the Python SDK, including untagged and malformed IDs.
describe('regionOf', () => {
  test.each([
    ['7fJkQ2mN8pL3xR5tV9wA1b-regiona', 'regiona', 'tagged id'],
    ['550e8400-e29b-41d4-a716-446655440000', '_', 'bare UUID carries no tag'],
    ['nodash', '_', 'no delimiter'],
    ['abc-reg-extra', 'reg-extra', 'the slug may contain dashes'],
    ['', '_', 'empty'],
    ['-regiona', '_', 'a leading dash is not a tag'],
    ['abc-', '_', 'a trailing dash is an empty tag'],
    ['-', '_', 'a lone dash'],
  ])('%s -> %s (%s)', (sandboxId, expected) => {
    expect(regionOf(sandboxId)).toBe(expected)
  })
})

// ---------------------------------------------------------------------------
// Snapshot-then-live
// ---------------------------------------------------------------------------

describe('WaitHub: snapshot then live', () => {
  test('creating → pending snapshot → running event resolves', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1, error: null })
    await drain()
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2, error: null })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('snapshot already satisfying resolves immediately', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['error', 'destroyed'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 3, error: null })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('version rule: lower version dropped', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 5 })
    await drain()
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'failed', version: 4 })
    await drain()
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 6 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('version rule: equal version applied', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['stopped']), new Set(['error'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 3 })
    await drain()
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'stopped', version: 3 })
    await expect(prom).resolves.toMatchObject({ state: 'stopped' })
  })

  test('unknown wire state keeps waiting', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    let settled = false
    void prom.then(
      () => { settled = true },
      () => { settled = true },
    )
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'weirdunknownstate', version: 1 })
    await drain()
    expect(settled).toBe(false)
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('target predicate: terminal state rejects', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['destroying', 'destroyed', 'error'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'failed', version: 1 })
    await expect(prom).rejects.toBeInstanceOf(SandboxTerminalStateError)
  })

  test('stopped is not terminal with default terminal set', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['destroying', 'destroyed', 'error'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'stopped', version: 1 })
    await drain()
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('pong sent on server ping', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'ping' })
    await drain()
    const pongs = ws.sentFrames().filter((f) => f['type'] === 'pong')
    expect(pongs.length).toBeGreaterThanOrEqual(1)
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('offers the daytona subprotocol and ticket names', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    expect(fc.last.protocols).toEqual(['daytona.events.v1', 'daytona.ticket.t0k3n'])
    fc.last.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('subscribe frame has correct shape (id + known)', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    const subs = ws.sentFrames().filter((f) => f['type'] === 'subscribe')
    expect(subs.length).toBeGreaterThanOrEqual(1)
    const ids = subs[0]!['ids'] as Array<{ id: string; known: boolean }>
    expect(ids.some((e) => e.id === SANDBOX_ID && e.known === false)).toBe(true)
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })
})

// ---------------------------------------------------------------------------
// NOT_FOUND
// ---------------------------------------------------------------------------

describe('WaitHub: NOT_FOUND', () => {
  test('was_known=true rejects with NotFoundError', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['error'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: true })
    await expect(prom).rejects.toBeInstanceOf(NotFoundError)
  })

  test('was_known=false hands the wait to fallback polling without resolving early', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios()
    let releaseGet!: () => void
    jest.spyOn(api, 'get').mockImplementation(
      () => new Promise((resolve) => { releaseGet = () => resolve({ data: { status: 'running', id: SANDBOX_ID }, headers: {} }) }),
    )
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['error'])), { timeout: 5_000 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: false })
    await drain()
    expect((api.get as jest.Mock).mock.calls).toHaveLength(1)
    expect(await Promise.race([prom.then(() => 'resolved'), Promise.resolve('pending')])).toBe('pending')
    releaseGet()
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('was_known=false falls back to one GET and surfaces a 404 immediately', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios({ getStatus: 404 })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: false })
    await expect(prom).rejects.toBeInstanceOf(NotFoundError)
    expect((api.get as jest.Mock).mock.calls).toHaveLength(1)
    await new Promise((resolve) => setTimeout(resolve, 60))
    expect(ws.sentFrames()).toContainEqual({ type: 'unsubscribe', ids: [SANDBOX_ID] })
  })

  test('a real frame makes the client treat its own knowledge as authoritative', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['error'])), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: false })
    await expect(prom).rejects.toBeInstanceOf(NotFoundError)
  })
})

// ---------------------------------------------------------------------------
// Timeout
// ---------------------------------------------------------------------------

describe('WaitHub: timeout', () => {
  test('deadline fires RequestTimeoutError', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    fc.prepare()
    await expect(hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 50 })).rejects.toBeInstanceOf(RequestTimeoutError)
  })

  test('timeout=0 means unbounded (resolves on first event)', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 0 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('an open that never completes times out and falls back after three strikes', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const api = mockAxios({ sandboxStatus: 'running' })
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        api,
        { openTimeoutMs: 5 },
      )
      const first = new FakeWebSocket()
      first.readyState = 0
      const second = new FakeWebSocket()
      second.readyState = 0
      const third = new FakeWebSocket()
      third.readyState = 0
      fc.prepare(first)
      fc.prepare(second)
      fc.prepare(third)
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(5)
      await drain()
      await jest.advanceTimersByTimeAsync(250)
      await drain()
      await jest.advanceTimersByTimeAsync(5)
      await drain()
      await jest.advanceTimersByTimeAsync(500)
      await drain()
      await jest.advanceTimersByTimeAsync(5)
      await drain()
      await jest.advanceTimersByTimeAsync(60_000)
      await expect(prom).resolves.toMatchObject({ state: 'started' })
      expect((api.get as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(1)
    } finally {
      jest.useRealTimers()
    }
  })

  test('sockets that open and never send still reach the 3-strike fallback', async () => {
    // A server that answers 101 and then goes silent: each socket opens and
    // dies with no frame. Opening must not reset the failure count.
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const api = mockAxios()
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        api,
        { noFrameTimeoutMs: 5 },
      )
      for (let i = 0; i < 6; i++) fc.prepare()
      let settled: unknown
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 120_000 }).then((r) => { settled = r; return r })
      // Well under the 120 s budget: the fallback must engage, not the timeout.
      for (let i = 0; i < 1_800 && settled === undefined; i++) {
        await jest.advanceTimersByTimeAsync(50)
        await drain()
      }
      expect(settled).toMatchObject({ state: 'started' })
      expect(fc.count).toBeGreaterThanOrEqual(3)
      expect((api.get as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(1)
      await prom
    } finally {
      jest.useRealTimers()
    }
  })

  test('a silent open socket hits the no-frame deadline and reconnects', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { noFrameTimeoutMs: 200 },
      )
      const ws1 = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
      await drain()
      let secondConnection: FakeWebSocket | undefined
      // Step finely: the second socket must be fed before its own deadline.
      for (let i = 0; i < 200; i++) {
        await jest.advanceTimersByTimeAsync(5)
        await drain()
        if (fc.count > 1) {
          secondConnection = fc.last
          break
        }
      }
      expect(secondConnection).toBeDefined()
      secondConnection?.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
    } finally {
      jest.useRealTimers()
    }
  })

  test('a wait on a closed hub polls instead of hanging', async () => {
    // A handle made before close() must still work: nothing runs a socket
    // loop on a closed hub, so a registered waiter would never be decided.
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    await hub.detach()
    const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
    await expect(
      hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 0, sandboxPollFn: poll }),
    ).resolves.toMatchObject({ state: 'started' })
    expect(poll).toHaveBeenCalledTimes(1)
    expect(fc.count).toBe(0)
  })

  test('an early probe uses half the remaining budget on a short wait', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      const api = mockAxios()
      const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        api,
        { earlyProbeMs: 80, noFrameTimeoutMs: 5_000 },
      )
      fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 60, sandboxPollFn: poll }).catch(() => undefined)
      await drain()
      await jest.advanceTimersByTimeAsync(29)
      await drain()
      expect(poll).toHaveBeenCalledTimes(0)
      await jest.advanceTimersByTimeAsync(1)
      await drain()
      expect(poll).toHaveBeenCalledTimes(1)
      expect((api.get as jest.Mock).mock.calls).toHaveLength(0)
      await hub.detach()
      await prom
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('a socket frame arriving before the probe fires cancels the early probe', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 50, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      await jest.advanceTimersByTimeAsync(20)
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1, error: null })
      await drain()
      await jest.advanceTimersByTimeAsync(40)
      await drain()
      expect(poll).toHaveBeenCalledTimes(0)
      expect(await Promise.race([prom.then(() => 'resolved'), Promise.resolve('pending')])).toBe('pending')
      ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2, error: null })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
    } finally {
      jest.useRealTimers()
    }
  })

  test('a socket frame arriving while the probe poll is in flight makes the socket decide', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      let releasePoll!: (outcome: WaitOutcome) => void
      const poll = jest.fn(
        () => new Promise<WaitOutcome>((resolve) => {
          releasePoll = resolve
        }),
      )
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 20, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      await jest.advanceTimersByTimeAsync(20)
      await drain()
      expect(poll).toHaveBeenCalledTimes(1)
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1, error: null })
      await drain()
      releasePoll({ state: 'started', rawState: 'running' })
      await drain()
      expect(await Promise.race([prom.then(() => 'resolved'), Promise.resolve('pending')])).toBe('pending')
      ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2, error: null })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('frames for another sandbox postpone the early probe for this waiter', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 20, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
    ws.inject({ type: 'snapshot', id: 'other-regiona', status: 'pending', version: 1, error: null })
      await drain()
      await jest.advanceTimersByTimeAsync(19)
      await drain()
      expect(poll).toHaveBeenCalledTimes(0)
      await jest.advanceTimersByTimeAsync(1)
      await expect(prom).resolves.toMatchObject({ state: 'started' })
      expect(poll).toHaveBeenCalledTimes(1)
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('a healthy socket keeps 200 same-id waiters from probing', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      const poll = jest.fn(async () => ({ state: 'creating' as const, rawState: 'pending' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 20, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const waits = Array.from({ length: 200 }, () =>
        hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll }).catch(() => undefined),
      )
      await drain()
      for (let i = 0; i < 6; i++) {
        await jest.advanceTimersByTimeAsync(8)
        ws.inject({ type: 'ping' })
        await drain()
      }
      expect(poll).toHaveBeenCalledTimes(0)
      await hub.detach()
      await Promise.all(waits)
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('registering a waiter on a held snapshot does not refresh the socket quiet clock', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 20, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const first = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1, error: null })
      await drain()
      await jest.advanceTimersByTimeAsync(15)
      const second = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      await jest.advanceTimersByTimeAsync(5)
      await drain()
      expect(poll).toHaveBeenCalledTimes(1)
      ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2, error: null })
      await expect(first).resolves.toMatchObject({ state: 'started' })
      await expect(second).resolves.toMatchObject({ state: 'started' })
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('the early probe re-arms for the remaining quiet time after a frame', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const fc = makeFakeConnector()
      const poll = jest.fn(async () => ({ state: 'started' as const, rawState: 'running' }))
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { earlyProbeMs: 20, noFrameTimeoutMs: 5_000 },
      )
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 200, sandboxPollFn: poll })
      await drain()
      await jest.advanceTimersByTimeAsync(15)
      ws.inject({ type: 'ping' })
      await drain()
      await jest.advanceTimersByTimeAsync(19)
      await drain()
      expect(poll).toHaveBeenCalledTimes(0)
      await jest.advanceTimersByTimeAsync(1)
      await expect(prom).resolves.toMatchObject({ state: 'started' })
      expect(poll).toHaveBeenCalledTimes(1)
    } finally {
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  test('a quiet socket sends one client ping per quiet window and re-arms on pong', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { timings: { clientPingAfterMs: 10, earlyProbeMs: 200, noFrameTimeoutMs: 5_000 } })
      const ws = fc.prepare()
      const wait = hub.wait(SANDBOX_ID, startedPredicate(), {
        timeout: 200,
        sandboxPollFn: async () => ({ state: 'creating', rawState: 'pending' }),
      }).catch(() => undefined)
      await drain()
      await jest.advanceTimersByTimeAsync(9)
      await drain()
      expect(ws.sentFrames().filter((frame) => frame['type'] === 'ping')).toHaveLength(0)
      await jest.advanceTimersByTimeAsync(1)
      await drain()
      expect(ws.sentFrames().filter((frame) => frame['type'] === 'ping')).toHaveLength(1)
      await jest.advanceTimersByTimeAsync(30)
      await drain()
      expect(ws.sentFrames().filter((frame) => frame['type'] === 'ping')).toHaveLength(1)
      ws.inject({ type: 'pong' })
      await drain()
      await jest.advanceTimersByTimeAsync(9)
      await drain()
      expect(ws.sentFrames().filter((frame) => frame['type'] === 'ping')).toHaveLength(1)
      await jest.advanceTimersByTimeAsync(1)
      await drain()
      expect(ws.sentFrames().filter((frame) => frame['type'] === 'ping')).toHaveLength(2)
      await hub.detach()
      await wait
    } finally {
      jest.useRealTimers()
    }
  })
})

// ---------------------------------------------------------------------------
// Reconnect
// ---------------------------------------------------------------------------

describe('WaitHub: reconnect resubscribes with known', () => {
  test('after a snapshot, reconnect sends known=true', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws1 = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 10_000 })
    await drain()
    ws1.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1 })
    await drain()
    const ws2 = fc.prepare()
    ws1.injectClose(1006)
    await new Promise<void>((resolve) => setTimeout(resolve, 350))
    await drain(30)
    expect(fc.count).toBe(2)
    const subs = ws2.sentFrames().filter((f) => f['type'] === 'subscribe')
    expect(subs.length).toBeGreaterThanOrEqual(1)
    const ids = subs[0]!['ids'] as Array<{ id: string; known: boolean }>
    expect(ids.find((e) => e.id === SANDBOX_ID)?.known).toBe(true)
    ws2.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('a reconnect requires a fresh snapshot before reusing cached state', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws1 = fc.prepare()
    const keepWaiting = hub.wait(
      SANDBOX_ID,
      targetPredicate(new Set(['paused']), new Set(['destroying', 'destroyed', 'error'])),
      { timeout: 5_000 },
    )
    await drain()
    ws1.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await drain()

    const ws2 = fc.prepare()
    ws1.injectClose(1006)
    await new Promise<void>((resolve) => setTimeout(resolve, 350))
    await drain(30)

    const freshWait = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
    let settled = false
    void freshWait.then(
      () => { settled = true },
      () => { settled = true },
    )
    await drain()
    expect(settled).toBe(false)

    ws2.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await drain()
    expect(settled).toBe(false)

    ws2.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(freshWait).resolves.toMatchObject({ state: 'started' })

    ws2.inject({ type: 'event', id: SANDBOX_ID, status: 'paused', version: 2 })
    await expect(keepWaiting).resolves.toMatchObject({ state: 'paused' })
  })

  test('a newer event can satisfy a wait before the reconnect snapshot arrives', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws1 = fc.prepare()
    const waiting = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
    await drain()
    ws1.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1 })
    await drain()

    const ws2 = fc.prepare()
    ws1.injectClose(1006)
    await new Promise<void>((resolve) => setTimeout(resolve, 350))
    await drain(30)

    ws2.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 2 })
    await expect(waiting).resolves.toMatchObject({ state: 'started' })
  })

  test('post-open closes back off before reconnecting', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const random = jest.spyOn(Math, 'random').mockReturnValue(1)
    try {
      const fc = makeFakeConnector()
      const api = mockAxios()
      const hub = makeHub(fc, { api })
      const ws1 = fc.prepare()
      const ws2 = fc.prepare()
      const ws3 = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 10_000 })
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(1)

      ws1.injectClose(1006)
      await drain()
      await jest.advanceTimersByTimeAsync(200)
      expect((api.post as jest.Mock).mock.calls.length).toBe(1)
      await jest.advanceTimersByTimeAsync(60)
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(2)

      ws2.injectClose(1013)
      await drain()
      await jest.advanceTimersByTimeAsync(400)
      expect((api.post as jest.Mock).mock.calls.length).toBe(2)
      await jest.advanceTimersByTimeAsync(120)
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(3)

      ws3.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 3 })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
    } finally {
      random.mockRestore()
      jest.useRealTimers()
    }
  })

  test('a session frame resets the reconnect backoff to the first attempt', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const random = jest.spyOn(Math, 'random').mockReturnValue(1)
    try {
      const fc = makeFakeConnector()
      const api = mockAxios()
      const hub = makeHub(fc, { api })
      const ws1 = fc.prepare()
      const ws2 = fc.prepare()
      const ws3 = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 10_000 })
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(1)

      ws1.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 1 })
      ws1.injectClose(1001)
      await drain()
      await jest.advanceTimersByTimeAsync(200)
      expect((api.post as jest.Mock).mock.calls.length).toBe(1)
      await jest.advanceTimersByTimeAsync(60)
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(2)

      ws2.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'pending', version: 2 })
      ws2.injectClose(1001)
      await drain()
      await jest.advanceTimersByTimeAsync(200)
      expect((api.post as jest.Mock).mock.calls.length).toBe(2)
      await jest.advanceTimersByTimeAsync(60)
      await drain()
      expect((api.post as jest.Mock).mock.calls.length).toBe(3)

      ws3.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 3 })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
    } finally {
      random.mockRestore()
      jest.useRealTimers()
    }
  })
})

// ---------------------------------------------------------------------------
// Unsubscribe when no waiters
// ---------------------------------------------------------------------------

describe('WaitHub: unsubscribe', () => {
  test('unsubscribe sent after waiter resolves', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc)
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
      await drain()
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
      await expect(prom).resolves.toMatchObject({ state: 'started' })
      await drain()
      await jest.advanceTimersByTimeAsync(50)
      await drain()
      const unsubs = ws.sentFrames().filter((f) => f['type'] === 'unsubscribe')
      expect(unsubs.length).toBeGreaterThanOrEqual(1)
      expect((unsubs[0]!['ids'] as string[])).toContain(SANDBOX_ID)
    expect((hub as unknown as { sockets: Map<string, { versions: Map<string, number> }> }).sockets.get('regiona')?.versions.has(SANDBOX_ID)).toBe(false)
    } finally {
      jest.useRealTimers()
    }
  })

  test('400 resolutions in one tick send one unsubscribe frame carrying 400 ids', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc)
      const ws = fc.prepare()
    const bootstrap = hub.wait('bootstrap-regiona', startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(50)
      await drain()
    ws.inject({ type: 'snapshot', id: 'bootstrap-regiona', status: 'running', version: 1 })
      await expect(bootstrap).resolves.toMatchObject({ state: 'started' })
      await drain()
      await jest.advanceTimersByTimeAsync(50)
      await drain()

      const unsubscribeBaseline = ws.sentFrames().filter((frame) => frame['type'] === 'unsubscribe').length
    const ids = Array.from({ length: 400 }, (_, index) => `id${index}-regiona`)
      const waits = ids.map((id) => hub.wait(id, startedPredicate(), { timeout: 30_000 }))
      await drain(30)
      await jest.advanceTimersByTimeAsync(50)
      await drain()
      ids.forEach((id, index) => {
        ws.inject({ type: 'snapshot', id, status: 'running', version: index + 1 })
      })
      await expect(Promise.all(waits)).resolves.toHaveLength(400)
      await drain()
      await jest.advanceTimersByTimeAsync(50)
      await drain()

      const unsubscribeFrames = ws.sentFrames().filter((frame) => frame['type'] === 'unsubscribe').slice(unsubscribeBaseline)
      expect(unsubscribeFrames).toHaveLength(1)
      expect(unsubscribeFrames[0]!['ids']).toEqual(ids)
    } finally {
      jest.useRealTimers()
    }
  })

  test('steady resolved stream stays at or under 50 subscribe+unsubscribe frames per second', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc)
      const ws = fc.prepare()
      type InternalSocket = {
        ws: WebSocket | undefined
        subscriptions: Set<string>
      }
      const internal = hub as unknown as {
        ensureSocket: (key: string, region: string) => InternalSocket
        enqueueSubscribeIds: (sock: InternalSocket, ids: readonly string[]) => void
        releaseSubscription: (sock: InternalSocket, sandboxId: string) => void
        waiters: Map<string, unknown[]>
      }
    const sock = internal.ensureSocket('regiona', 'regiona')
      sock.ws = ws as unknown as WebSocket
      const baseline = ws.sentFrameRecords().length
    const ids = Array.from({ length: 300 }, (_, index) => `steady-${index}-regiona`)
      ids.forEach((id, index) => {
        setTimeout(() => {
          sock.subscriptions.add(id)
          internal.waiters.set(id, [{}])
          internal.enqueueSubscribeIds(sock, [id])
        }, index * 4)
        setTimeout(() => {
          internal.waiters.delete(id)
          internal.releaseSubscription(sock, id)
        }, index * 4 + 80)
      })

      await jest.advanceTimersByTimeAsync(1_400)
      await drain(40)
      await jest.advanceTimersByTimeAsync(100)
      await drain()

      const frameRecords = ws.sentFrameRecords()
        .slice(baseline)
        .filter(({ frame }) => frame['type'] === 'subscribe' || frame['type'] === 'unsubscribe')
      for (const record of frameRecords) {
        const framesInWindow = frameRecords.filter((candidate) => candidate.at >= record.at && candidate.at < record.at + 1_000)
        expect(framesInWindow.length).toBeLessThanOrEqual(50)
      }

      const unsubscribeIds = frameRecords
        .filter(({ frame }) => frame['type'] === 'unsubscribe')
        .flatMap(({ frame }) => frame['ids'] as string[])
      expect(unsubscribeIds).toHaveLength(ids.length)
      ids.forEach((id) => {
        expect(unsubscribeIds.filter((value) => value === id)).toHaveLength(1)
      })
    } finally {
      jest.useRealTimers()
    }
  })
})

// ---------------------------------------------------------------------------
// Reconcile timer
// ---------------------------------------------------------------------------

describe('WaitHub: reconcile', () => {
  test('reconcile timer re-subscribes with live waiter', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { reconcileIntervalMs: 50 })
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 10_000 })
    await drain()

    const before = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
    await new Promise<void>((resolve) => setTimeout(resolve, 300))
    const after = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
    expect(after).toBeGreaterThan(before)

    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  }, 10_000)
})

// ---------------------------------------------------------------------------
// Shared hub
// ---------------------------------------------------------------------------

describe('WaitHub: shared hub', () => {
  afterEach(() => { _hubRegistryForTest.clear() })

  test('same identity → same hub instance', () => {
    const api = mockAxios()
    const config: EventsHubConfig = { enabled: true, reconcileIntervalMs: 0 }
    const hub1 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config })
    const hub2 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config })
    expect(hub1).toBe(hub2)
    void detachHub(hub1, { apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true })
    void detachHub(hub2, { apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true })
  })

  test('closing first of two refs keeps hub alive for second', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios()
    const config: EventsHubConfig = { enabled: true, reconcileIntervalMs: 0 }
    const wsFactory = fc.factory.bind(fc) as unknown as (url: string, protocols: readonly string[]) => WebSocket
    const hub1 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config, wsFactory })
    const hub2 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config, wsFactory })
    expect(hub1).toBe(hub2)
    const ws = fc.prepare()
    const prom = hub2.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    await detachHub(hub1, { apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true })
    expect(hub2.closed).toBe(false)
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
    await detachHub(hub2, { apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true })
    expect(hub2.closed).toBe(true)
  })

  test('a client with different events settings gets its own hub', async () => {
    // Sharing must not silently apply one client's settings to another.
    const api = mockAxios()
    const a = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config: { idleCloseMs: 0 } })
    const b = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config: { idleCloseMs: 5_000 } })
    const c = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true, config: { idleCloseMs: 0 } })
    expect(a).not.toBe(b)
    expect(a).toBe(c)
    const opts = { apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: true }
    await detachHub(a, opts)
    await detachHub(c, opts)
    await detachHub(b, opts)
    expect(_hubRegistryForTest.size).toBe(0)
  })

  test('hub identity lowercases scheme and host only', async () => {
    const api = mockAxios()
    const config: EventsHubConfig = { enabled: true, reconcileIntervalMs: 0 }
    const same = attachHub({ _api: api, apiUrl: 'HTTPS://API.EXAMPLE.com/Case/Path/', apiKey: API_KEY, target: undefined, shared: true, config })
    const sameAgain = attachHub({ _api: api, apiUrl: 'https://api.example.com/Case/Path', apiKey: API_KEY, target: undefined, shared: true, config })
    const different = attachHub({ _api: api, apiUrl: 'https://api.example.com/case/path', apiKey: API_KEY, target: undefined, shared: true, config })
    expect(same).toBe(sameAgain)
    expect(different).not.toBe(same)
    await detachHub(same, { apiUrl: 'https://api.example.com/Case/Path', apiKey: API_KEY, target: undefined, shared: true })
    await detachHub(sameAgain, { apiUrl: 'https://api.example.com/Case/Path', apiKey: API_KEY, target: undefined, shared: true })
    await detachHub(different, { apiUrl: 'https://api.example.com/case/path', apiKey: API_KEY, target: undefined, shared: true })
  })

  test('sharedHub=false gives each call its own hub', async () => {
    const api = mockAxios()
    const config: EventsHubConfig = { enabled: true, reconcileIntervalMs: 0 }
    const hub1 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: false, config })
    const hub2 = attachHub({ _api: api, apiUrl: API_URL, apiKey: API_KEY, target: undefined, shared: false, config })
    expect(hub1).not.toBe(hub2)
    await hub1.detach()
    await hub2.detach()
  })
})

// ---------------------------------------------------------------------------
// Learned destination rekeying
// ---------------------------------------------------------------------------

describe('WaitHub: learned destination rekeying', () => {
  test('regional socket rekeyed to learned destination', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    hub.learn(SANDBOX_ID, 'destination-1')
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })

  test('two IDs with different regions but the same destination share one socket', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.learn('id1-regiona', 'destination-x')
    hub.learn('id2-region-b', 'destination-x')
    const ws = fc.prepare()
    const p1 = hub.wait('id1-regiona', startedPredicate(), { timeout: 5000 })
    const p2 = hub.wait('id2-region-b', startedPredicate(), { timeout: 5000 })
    await drain()
    expect(fc.count).toBe(1)
    ws.inject({ type: 'snapshot', id: 'id1-regiona', status: 'running', version: 1 })
    ws.inject({ type: 'snapshot', id: 'id2-region-b', status: 'running', version: 1 })
    await expect(Promise.all([p1, p2])).resolves.toMatchObject([{ state: 'started' }, { state: 'started' }])
  })

  test('a remapped region moves waiters off the old destination socket', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.learn(SANDBOX_ID, 'destination-a')
    const wsOld = fc.prepare()
    const waiting = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
    await drain()

    const wsNew = fc.prepare()
    hub.learn(SANDBOX_ID, 'destination-b')
    await drain(30)

    expect(fc.count).toBe(2)
    wsOld.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await drain()
    expect(await Promise.race([waiting.then(() => 'resolved'), Promise.resolve('pending')])).toBe('pending')

    wsNew.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(waiting).resolves.toMatchObject({ state: 'started' })
  })

  test('a moved socket that was still opening closes when it finally opens', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.learn(SANDBOX_ID, 'destination-a')
    const wsOld = fc.prepare(new class extends FakeWebSocket {
      override close(code = 1000): void {
        if (this.readyState === 0) return
        super.close(code)
      }
    }())
    wsOld.readyState = 0
    const waiting = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 })
    await drain()

    const wsNew = fc.prepare()
    hub.learn(SANDBOX_ID, 'destination-b')
    await drain(30)

    wsOld.readyState = 1
    wsOld.onopen?.({} as Event)
    await drain()
    expect(wsOld.closeCode).toBe(1000)

    wsNew.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(waiting).resolves.toMatchObject({ state: 'started' })
  })
})

// ---------------------------------------------------------------------------
// Stale destination eviction and regional retry
// ---------------------------------------------------------------------------

describe('WaitHub: NOT_FOUND on a learned destination evicts and retries', () => {
  test('NOT_FOUND evicts the mapping and retries on the regional socket', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.learn(SANDBOX_ID, 'destination-1')
    const wsDestination = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, targetPredicate(new Set(['started']), new Set(['error'])), { timeout: 10_000 })
    await drain()
    const wsRegion = fc.prepare()
    wsDestination.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: true })
    await drain(30)
    // A late frame from the stale destination must not decide the moved waiter.
    wsDestination.inject({ type: 'event', id: SANDBOX_ID, status: 'failed', version: 9 })
    await drain()
    wsRegion.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
    // Release the stale subscription so it no longer consumes subscription limits.
    await new Promise((resolve) => setTimeout(resolve, 60))
    expect(wsDestination.sentFrames()).toContainEqual({ type: 'unsubscribe', ids: [SANDBOX_ID] })
  })

  test('known ids are bounded as an LRU', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 0 })
    const ws = fc.prepare()
    void hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 2_000 }).catch(() => undefined)
    await drain()
    for (let i = 0; i < 10_005; i++) {
      ws.inject({ type: 'snapshot', id: `id-${i}-regiona`, status: 'running', version: 1 })
    }
    const known = (hub as unknown as { known: Map<string, true> }).known
    expect(known.size).toBe(10_000)
    expect(known.has('id-0-regiona')).toBe(false)
    expect(known.has('id-10004-regiona')).toBe(true)
  })

  test('a late NOT_FOUND does not re-subscribe an id whose waiter already left', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.learn(SANDBOX_ID, 'destination-1')
    const wsDestination = fc.prepare()
    const timedOut = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 50 })
    await drain()
    await expect(timedOut).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT' })
    wsDestination.inject({ type: 'error', id: SANDBOX_ID, code: 'NOT_FOUND', was_known: true })
    await drain(30)
    expect(
        (hub as unknown as { sockets: Map<string, { subscriptions: Set<string> }> }).sockets.has('regiona'),
    ).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Ticket 404 → permanent fallback (long-poll)
// ---------------------------------------------------------------------------

describe('WaitHub: 404 ticket → long-poll fallback', () => {
  test('404 ticket → fallback resolves via sandboxPollFn', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios({ ticketStatus: 404 })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    const result = await hub.wait(SANDBOX_ID, startedPredicate(), {
      timeout: 5000,
      sandboxPollFn: async () => ({ state: 'started' as const, rawState: 'running' }),
    })
    expect(result.state).toBe('started')
  })

  test('404 ticket → fallbackWait using api.get resolves', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios({ ticketStatus: 404, sandboxStatus: 'running' })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    const result = await hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    expect(result.state).toBe('started')
  })

  test('fallbackWait skips wait= for multi-target predicates', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios({ ticketStatus: 404 })
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: SANDBOX_ID, status: 'booting' }, headers: {} })
      .mockResolvedValueOnce({ data: { id: SANDBOX_ID, status: 'stopped' }, headers: {} })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    const result = await hub.wait(
      SANDBOX_ID,
      targetPredicate(new Set(['started', 'stopped']), new Set(['destroying', 'destroyed', 'error'])),
      { timeout: 5000 },
    )
    expect(result.state).toBe('stopped')
    for (const call of get.mock.calls) expect(call[1]?.params).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// 3 consecutive failures → transient fallback
// ---------------------------------------------------------------------------

describe('WaitHub: 3 failures → transient fallback then sandboxPollFn', () => {
  test('3 open errors → HubFallback → sandboxPollFn used', async () => {
    let connectCount = 0
    const failFactory = (url: string, protocols: readonly string[]): WebSocket => {
      connectCount++
      const ws = new FakeWebSocket(url, protocols)
      ws.readyState = 0
      if (connectCount <= 3) {
        setTimeout(() => {
          ws.readyState = 3
          ws.onerror?.({} as Event)
        }, 1)
      } else {
        setTimeout(() => ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 }), 10)
      }
      return ws as unknown as WebSocket
    }
    const api = mockAxios({ ticketStatus: 200 })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, failFactory, api)

    let pollCalls = 0
    const result = await hub.wait(SANDBOX_ID, startedPredicate(), {
      timeout: 10_000,
      sandboxPollFn: async () => { pollCalls++; return { state: 'started' as const, rawState: 'running' } },
    })
    expect(result.state).toBe('started')
    expect(pollCalls).toBeGreaterThanOrEqual(1)
  }, 15_000)
})

describe('WaitHub: shutdown during ticket mint', () => {
  test('closing the hub while ticket minting is pending never opens a socket afterwards', async () => {
    const fc = makeFakeConnector()
    const api = mockAxios({ ticketDelayMs: 20 })
    const hub = new WaitHub(API_URL, API_KEY, { enabled: true, reconcileIntervalMs: 0 }, fc.factory.bind(fc) as never, api)
    hub.attach()
    void hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 }).catch(() => undefined)
    await drain()
    await hub.detach()
    await new Promise((resolve) => setTimeout(resolve, 40))
    expect(fc.count).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Disabled via env
// ---------------------------------------------------------------------------

describe('WaitHub: env disabled → fallback', () => {
  test('DAYTONA_SANDBOX_EVENTS=0 skips WS, uses sandboxPollFn', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '0'
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { enabled: true })
      let called = false
      const result = await hub.wait(SANDBOX_ID, startedPredicate(), {
        timeout: 5000,
        sandboxPollFn: async () => { called = true; return { state: 'started' as const, rawState: 'running' } },
      })
      expect(result.state).toBe('started')
      expect(called).toBe(true)
      expect(fc.count).toBe(0)
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })
})

// ---------------------------------------------------------------------------
// Protocol fixture parity
// ---------------------------------------------------------------------------

describe('protocol fixture parity', () => {
  const fixturePath = resolve(join(__dirname, '../../contract/sandbox-events-protocol.json'))
  let fixture: Record<string, unknown>

  beforeAll(() => {
    fixture = JSON.parse(readFileSync(fixturePath, 'utf8')) as Record<string, unknown>
  })

  test('fixture has expected top-level keys', () => {
    expect(fixture).toHaveProperty('server_frames')
    expect(fixture).toHaveProperty('client_frames')
    expect(fixture).toHaveProperty('sdk_state_decoding')
    expect(fixture).toHaveProperty('version_rule')
  })

  test('every server snapshot/event wire state decodes correctly', () => {
    const serverFrames = fixture['server_frames'] as Array<{ name: string; frame: Record<string, unknown> }>
    const decoding = fixture['sdk_state_decoding'] as Record<string, string>
    for (const frameDef of serverFrames) {
      const frame = frameDef.frame
      if (frame['type'] !== 'snapshot' && frame['type'] !== 'event') continue
      expect(typeof frame['status']).toBe('string')
      const wire = String(frame['status'] ?? '')
      const expected = decoding[wire] ?? decoding['*'] ?? 'unknown'
      expect(sandboxState(wire)).toBe(expected)
    }
  })

  test('subscribe client frame shape matches fixture', () => {
    const clientFrames = fixture['client_frames'] as Array<{ name: string; frame: Record<string, unknown> }>
    const sub = clientFrames.find((f) => f.name === 'subscribe')!
    expect(sub).toBeDefined()
    const ids = sub.frame['ids'] as Array<Record<string, unknown>>
    expect(ids).toBeInstanceOf(Array)
    for (const entry of ids) {
      expect(entry).toHaveProperty('id')
      expect(entry).toHaveProperty('known')
    }
  })

  test('hub sends subscribe frame matching fixture shape', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    const subs = ws.sentFrames().filter((f) => f['type'] === 'subscribe')
    expect(subs.length).toBeGreaterThanOrEqual(1)
    expect(subs[0]!).toMatchObject({ type: 'subscribe', ids: expect.arrayContaining([expect.objectContaining({ id: SANDBOX_ID, known: false })]) })
    ws.inject({ type: 'event', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toBeDefined()
  })

  test('error_codes in fixture are all known (NOT_FOUND, LIMIT, MALFORMED)', () => {
    const codes = fixture['error_codes'] as string[]
    expect(codes).toContain('NOT_FOUND')
    expect(codes).toContain('LIMIT')
    expect(codes).toContain('MALFORMED')
  })

  test('id-less LIMIT retries live subscriptions instead of dropping them', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'error', id: null, code: 'LIMIT', message: 'inbound frame rate' })
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 50))
      if (ws.sentFrames().filter((f) => f['type'] === 'subscribe').length >= 2) break
    }
    expect(ws.sentFrames().filter((f) => f['type'] === 'subscribe').length).toBeGreaterThanOrEqual(2)
    ws.inject({ type: 'error', id: null, code: 'MALFORMED', message: 'invalid frame' })
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  })
})

// ---------------------------------------------------------------------------
// Close / cleanup
// ---------------------------------------------------------------------------

describe('WaitHub: a spent budget never becomes an unbounded wait', () => {
  test('the fallback rejects with RequestTimeoutError when the deadline has passed', async () => {
    // `0` means UNBOUNDED to every timeout argument in this SDK, so it must
    // never be produced by arithmetic on a deadline that already passed —
    // a wait that falls back after its budget is spent has timed out.
    const fc = makeFakeConnector()
    // The race this guards: the hub falls back AFTER the budget is spent but
    // BEFORE the waiter's own timer has run. Reproduced by blocking the event
    // loop past the deadline — when it unblocks, the mint's 404 and the
    // fallback chain are microtasks and run before the due timer, cancelling
    // it. Pre-fix the spent budget became `0`, which every timeout argument
    // in this SDK reads as UNBOUNDED, and the wait polled on forever.
    const api = mockAxios({ ticketStatus: 404, ticketDelayMs: 10 })
    const hub = new WaitHub(
      API_URL,
      API_KEY,
      { enabled: true, reconcileIntervalMs: 0 },
      fc.factory.bind(fc) as never,
      api,
    )
    const deadlinePassed = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 50 })
    const until = Date.now() + 150
    while (Date.now() < until) {
      /* block the loop so the waiter's timer is overdue, not fired */
    }
    await expect(deadlinePassed).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT' })
  })
})

// A refusal is an answer, not silence: the server said it is sending no
// snapshot for this id, so a waiter that keeps waiting waits forever. Same
// scenarios as the Python twin's TestRefusedSubscription.
describe('WaitHub: a refused subscription', () => {
  test('LIMIT is retried rather than waited out', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'LIMIT', message: 'subscriptions per socket' })
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 50))
      if (ws.sentFrames().filter((f) => f['type'] === 'subscribe').length >= 2) break
    }
    expect(ws.sentFrames().filter((f) => f['type'] === 'subscribe').length).toBeGreaterThanOrEqual(2)
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    await expect(prom).resolves.toMatchObject({ state: 'started' })
  }, 15_000)

  test('LIMIT retries are batched into one subscribe frame per tick', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const otherId = 'def-regiona'
    const first = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
    const second = hub.wait(otherId, startedPredicate(), { timeout: 30_000 })
    await drain()
    const initialFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'LIMIT', message: 'subscriptions per socket' })
    ws.inject({ type: 'error', id: otherId, code: 'LIMIT', message: 'subscriptions per socket' })
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 50))
      if (ws.sentFrames().filter((f) => f['type'] === 'subscribe').length > initialFrames) break
    }
    const retryFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe')
    expect(retryFrames).toHaveLength(initialFrames + 1)
    expect(retryFrames.at(-1)).toMatchObject({
      type: 'subscribe',
      ids: expect.arrayContaining([
        expect.objectContaining({ id: SANDBOX_ID }),
        expect.objectContaining({ id: otherId }),
      ]),
    })
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    ws.inject({ type: 'snapshot', id: otherId, status: 'running', version: 1, error: null })
    await expect(Promise.all([first, second])).resolves.toMatchObject([
      { state: 'started' },
      { state: 'started' },
    ])
  }, 15_000)

  test('MALFORMED fails the waiter — retrying cannot fix the id', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
    await drain()
    ws.inject({ type: 'error', id: SANDBOX_ID, code: 'MALFORMED', message: 'invalid sandbox id' })
    await expect(prom).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST' })

    // The optimistic local subscription is released: a later wait on the
    // same id must send a subscribe, not assume it already has one and wait
    // out its timeout.
    const before = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
    const again = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
    await drain()
    await new Promise((r) => setTimeout(r, 60))
    expect(ws.sentFrames().filter((f) => f['type'] === 'subscribe').length).toBe(before + 1)
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    await expect(again).resolves.toMatchObject({ state: 'started' })
  })
})

describe('WaitHub: fresh subscribe batching', () => {
  test('300 waits in one tick send one subscribe frame carrying 300 ids', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { timings: { subscribeFlushMs: 10 } })
      const ws = fc.prepare()
    const bootstrap = hub.wait('bootstrap-regiona', startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
      await drain()
    ws.inject({ type: 'snapshot', id: 'bootstrap-regiona', status: 'running', version: 1 })
      await expect(bootstrap).resolves.toMatchObject({ state: 'started' })
      const initialFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
      const waits = Array.from({ length: 300 }, (_, i) =>
      hub.wait(`id${i}-regiona`, startedPredicate(), { timeout: 0 }).catch(() => undefined),
      )
      for (let i = 0; i < 30; i++) {
        await drain()
        const waiterCount = [...((hub as unknown as { waiters: Map<string, Array<unknown>> }).waiters.values())]
          .reduce((sum, list) => sum + list.length, 0)
        if (waiterCount === 300) break
      }
      await jest.advanceTimersByTimeAsync(10)
      await drain()
      const subscribeFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe')
      expect(subscribeFrames).toHaveLength(initialFrames + 1)
      expect((subscribeFrames.at(-1)!['ids'] as Array<unknown>)).toHaveLength(300)
      await hub.detach()
      await Promise.all(waits)
    } finally {
      jest.useRealTimers()
    }
  })

  test('2500 waits in one tick are chunked into 1000/1000/500 ids', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { timings: { subscribeFlushMs: 10 } })
      const ws = fc.prepare()
    const bootstrap = hub.wait('bootstrap-regiona', startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
      await drain()
    ws.inject({ type: 'snapshot', id: 'bootstrap-regiona', status: 'running', version: 1 })
      await expect(bootstrap).resolves.toMatchObject({ state: 'started' })
      const initialFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
      const waits = Array.from({ length: 2_500 }, (_, i) =>
      hub.wait(`id${i}-regiona`, startedPredicate(), { timeout: 0 }).catch(() => undefined),
      )
      for (let i = 0; i < 60; i++) {
        await drain()
        const waiterCount = [...((hub as unknown as { waiters: Map<string, Array<unknown>> }).waiters.values())]
          .reduce((sum, list) => sum + list.length, 0)
        if (waiterCount === 2_500) break
      }
      await jest.advanceTimersByTimeAsync(10)
      await drain()
      const subscribeFrames = ws.sentFrames().filter((f) => f['type'] === 'subscribe')
      expect(subscribeFrames).toHaveLength(initialFrames + 3)
      expect(subscribeFrames.slice(initialFrames).map((frame) => (frame['ids'] as Array<unknown>).length)).toEqual([1000, 1000, 500])
      await hub.detach()
      await Promise.all(waits)
    } finally {
      jest.useRealTimers()
    }
  })

  test('an id subscribed and released within one flush window sends neither subscribe nor unsubscribe', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { timings: { subscribeFlushMs: 10 } })
      const ws = fc.prepare()
    const bootstrap = hub.wait('bootstrap-regiona', startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
      await drain()
    ws.inject({ type: 'snapshot', id: 'bootstrap-regiona', status: 'running', version: 1 })
      await expect(bootstrap).resolves.toMatchObject({ state: 'started' })
      await jest.advanceTimersByTimeAsync(10)
      await drain()
      const before = ws.sentFrames().filter((f) => f['type'] === 'subscribe').length
      const unsubBefore = ws.sentFrames().filter((f) => f['type'] === 'unsubscribe').length
      const wait = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 1 }).catch(() => undefined)
      for (let i = 0; i < 10; i++) {
        await drain()
        const waiterCount = [...((hub as unknown as { waiters: Map<string, Array<unknown>> }).waiters.values())]
          .reduce((sum, list) => sum + list.length, 0)
        if (waiterCount === 1) break
      }
      await jest.advanceTimersByTimeAsync(1)
      await drain()
      await jest.advanceTimersByTimeAsync(9)
      await drain()
      expect(ws.sentFrames().filter((f) => f['type'] === 'subscribe')).toHaveLength(before)
      expect(ws.sentFrames().filter((f) => f['type'] === 'unsubscribe')).toHaveLength(unsubBefore)
      await wait
      await hub.detach()
    } finally {
      jest.useRealTimers()
    }
  })

  test('a released id re-awaited and dropped in one window is still unsubscribed', async () => {
    // release -> re-await -> drop within one flush window: the re-await cancels
    // the pending unsubscribe, the drop cancels the pending subscribe, but the
    // server still holds the id from the first wait.
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc, { timings: { subscribeFlushMs: 10 } })
      const ws = fc.prepare()
    const bootstrap = hub.wait('bootstrap-regiona', startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
      await drain()
    ws.inject({ type: 'snapshot', id: 'bootstrap-regiona', status: 'running', version: 1 })
      await expect(bootstrap).resolves.toMatchObject({ state: 'started' })

      const first = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
      await drain()
      await jest.advanceTimersByTimeAsync(10)
      await drain()
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
      await expect(first).resolves.toMatchObject({ state: 'started' })
      // The release's unsubscribe is now pending in this window.

      const again = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 2 }).catch(() => undefined)
      await drain()
      await jest.advanceTimersByTimeAsync(3)
      await drain()
      await again
      await jest.advanceTimersByTimeAsync(20)
      await drain()
      const unsubscribed = ws
        .sentFrames()
        .filter((f) => f['type'] === 'unsubscribe')
        .flatMap((f) => f['ids'] as string[])
        .filter((id) => id === SANDBOX_ID)
      expect(unsubscribed).toEqual([SANDBOX_ID])
      await hub.detach()
    } finally {
      jest.useRealTimers()
    }
  })
})

// A `4401` means "mint a fresh ticket" — once. If the mint keeps succeeding
// and the socket keeps being refused, minting again as fast as the loop
// allows is a hot loop against the API. Same scenario as the
// Python twin's TestCredentialRefusal.
describe('WaitHub: a repeatedly refused credential', () => {
  test('fails the waiters instead of spinning on fresh tickets', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const seen = new Set<unknown>()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30_000 })
    const rejection = expect(prom).rejects.toMatchObject({ statusCode: 401 })
    for (let i = 0; i < 40; i++) {
      await drain(5)
      const ws = fc.count > 0 ? fc.last : undefined
      if (ws !== undefined && !seen.has(ws)) {
        seen.add(ws)
        ws.close(4401)
      }
      await new Promise((r) => setTimeout(r, 60))
    }
    await rejection
    // It gave up rather than minting without end.
    expect(seen.size).toBeLessThanOrEqual(4)
  }, 15_000)

  test('resets credential refusals after a session delivers frames', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    const ws1 = fc.prepare()
    const ws2 = fc.prepare()
    const ws3 = fc.prepare()
    const ws4 = fc.prepare()

    const waiting = hub.wait(
      SANDBOX_ID,
      targetPredicate(new Set(['paused']), new Set(['destroying', 'destroyed', 'error'])),
      { timeout: 10_000 },
    )

    await drain()
    ws1.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1 })
    ws1.inject({ type: 'ping' })
    ws1.close(4401)

    await new Promise((resolve) => setTimeout(resolve, 250))
    await drain(20)
    ws2.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 2 })
    ws2.inject({ type: 'ping' })
    ws2.close(4401)

    await new Promise((resolve) => setTimeout(resolve, 250))
    await drain(20)
    ws3.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 3 })
    ws3.inject({ type: 'ping' })
    ws3.close(4401)

    await new Promise((resolve) => setTimeout(resolve, 250))
    await drain(20)
    ws4.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'paused', version: 4 })

    await expect(waiting).resolves.toMatchObject({ state: 'paused', rawState: 'paused' })
    expect(fc.count).toBe(4)
  }, 15_000)
})

describe('WaitHub: an oversized server frame', () => {
  test('closes the socket 1009 instead of parsing it', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 0 })
    const ws = fc.prepare()
    void hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 2_000 }).catch(() => undefined)
    await drain()
    ws.injectRaw('x'.repeat(MAX_SERVER_FRAME_BYTES + 1))
    await drain()
    expect(ws.readyState).toBe(3)
    expect(ws.closeCode).toBe(1009)
  })

  test('counts UTF-8 bytes, not UTF-16 units', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 0 })
    const ws = fc.prepare()
    void hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 2_000 }).catch(() => undefined)
    await drain()
    // Under the cap in UTF-16 units, three times it in UTF-8 bytes.
    ws.injectRaw('€'.repeat(MAX_SERVER_FRAME_BYTES / 2))
    await drain()
    expect(ws.closeCode).toBe(1009)
  })
})

describe('WaitHub: idle close', () => {
  test('idleCloseMs: 0 keeps a socket with no waiters open', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 0 })
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    await prom
    await new Promise((r) => setTimeout(r, 1_200))
    expect(ws.readyState).toBe(1)
  })

  test('a releasable socket stays open while idle and stops holding the event loop', async () => {
    // Spaced waits must reuse the socket (no ticket per wait), and an idle
    // socket must not keep the process alive.
    const fc = makeFakeConnector()
    const api = mockAxios()
    const hub = makeHub(fc, { api })
    const ws = fc.prepare()
    const calls: string[] = []
    trackLoopHandle(ws as unknown as WebSocket, { ref: () => calls.push('ref'), unref: () => calls.push('unref') })
    for (let i = 0; i < 3; i++) {
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
      await drain()
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: i + 1, error: null })
      await prom
      expect(calls[calls.length - 1]).toBe('unref')
    }
    expect(calls).toEqual(['unref', 'ref', 'unref', 'ref', 'unref'])
    expect(fc.count).toBe(1)
    expect((api.post as jest.Mock).mock.calls.length).toBe(1)
    expect(ws.readyState).toBe(1)
  })

  test('a socket that cannot be released closes after the fallback idle default', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = makeHub(fc)
      const ws = fc.prepare()
      const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 60_000 })
      await drain()
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
      await prom
      jest.advanceTimersByTime(DEFAULT_IDLE_CLOSE_WITHOUT_UNREF_MS - 1)
      expect(ws.readyState).toBe(1)
      jest.advanceTimersByTime(2)
      expect(ws.readyState).toBe(3)
    } finally {
      jest.useRealTimers()
    }
  })

  test('a socket whose waiters all left during the handshake is released at once', async () => {
    const fc = makeFakeConnector()
    let releaseMint!: () => void
    const gate = new Promise<void>((r) => { releaseMint = r })
    const api = mockAxios()
    const post = api.post as jest.Mock
    const original = post.getMockImplementation()
    post.mockImplementation(async (...args: unknown[]) => {
      await gate
      return original?.(...args)
    })
    const hub = makeHub(fc, { api })
    const ws = fc.prepare()
    const calls: string[] = []
    trackLoopHandle(ws as unknown as WebSocket, { ref: () => calls.push('ref'), unref: () => calls.push('unref') })
    await expect(hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 30 })).rejects.toThrow()
    releaseMint()
    await drain()
    expect(fc.count).toBe(1)
    expect(calls).toEqual(['unref'])
  })

  test('idleCloseMs closes the socket once the last waiter leaves', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 20 })
    const ws = fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    await prom
    expect(ws.readyState).toBe(1)
    await new Promise((r) => setTimeout(r, 60))
    expect(ws.readyState).toBe(3)
  })

  test('a new waiter before the deadline cancels the idle close', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc, { idleCloseMs: 60 })
    const ws = fc.prepare()
    const first = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5000 })
    await drain()
    ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'running', version: 1, error: null })
    await first
    const second = hub.wait('other-regiona', startedPredicate(), { timeout: 5000 })
    await drain()
    await new Promise((r) => setTimeout(r, 90))
    expect(ws.readyState).toBe(1)
    ws.inject({ type: 'snapshot', id: 'other-regiona', status: 'running', version: 1, error: null })
    await second
  })
})

describe('WaitHub: close', () => {
  test('detach() closes hub when last ref released and rejects waiters', async () => {
    const fc = makeFakeConnector()
    const hub = makeHub(fc)
    hub.attach()
    fc.prepare()
    const prom = hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 10_000 })
    await drain()
    await hub.detach()
    await expect(prom).rejects.toBeInstanceOf(ServiceUnavailableError)
    await expect(prom).rejects.toMatchObject({ statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'SDK' })
    expect(hub.closed).toBe(true)
  })

  test('ref-counted: two refs need two detaches to close', async () => {
    const hub = makeHub(makeFakeConnector())
    hub.attach()
    hub.attach()
    expect(hub.closed).toBe(false)
    await hub.detach()
    expect(hub.closed).toBe(false)
    await hub.detach()
    expect(hub.closed).toBe(true)
  })

  test('hub close terminates a ws socket whose close handshake stalls', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    try {
      const fc = makeFakeConnector()
      const hub = new WaitHub(
        API_URL,
        API_KEY,
        { enabled: true, reconcileIntervalMs: 0 },
        fc.factory.bind(fc) as never,
        mockAxios(),
        { closeGraceMs: 5 },
      )
      hub.attach()
      const ws = fc.prepare() as FakeWebSocket & { terminate: jest.Mock }
      ws.close = jest.fn(() => { ws.readyState = 2 })
      ws.terminate = jest.fn(() => {
        ws.readyState = 3
        ws.onclose?.({ code: 1006, reason: '', wasClean: false } as CloseEvent)
      })
      void hub.wait(SANDBOX_ID, startedPredicate(), { timeout: 5_000 }).catch(() => undefined)
      await drain()
      await hub.detach()
      await jest.advanceTimersByTimeAsync(5)
      expect(ws.terminate).toHaveBeenCalledTimes(1)
    } finally {
      jest.useRealTimers()
    }
  })
})

// ---------------------------------------------------------------------------
// Events destination header
// ---------------------------------------------------------------------------

describe('eventDestinationFromHeaders', () => {
  test('reads x-daytona-cell', () => {
    expect(eventDestinationFromHeaders({ 'x-daytona-cell': 'destination-a' })).toBe('destination-a')
  })
  test('ignores unrelated headers', () => {
    expect(eventDestinationFromHeaders({ 'x-test-header': 'unrelated' })).toBe('')
    expect(eventDestinationFromHeaders({ 'x-test-header': 'unrelated', 'x-daytona-cell': 'destination-new' })).toBe('destination-new')
  })
  test('matches the header name case-insensitively (axios keeps the wire case)', () => {
    expect(eventDestinationFromHeaders(new AxiosHeaders({ 'X-Daytona-Cell': 'destination-h' }))).toBe('destination-h')
    expect(eventDestinationFromHeaders({ 'X-Daytona-Cell': 'destination-p' })).toBe('destination-p')
  })
  test('nothing set, or no headers at all', () => {
    expect(eventDestinationFromHeaders({})).toBe('')
    expect(eventDestinationFromHeaders(undefined)).toBe('')
  })
})

describe('a terminal state the hub reports, on the real hub', () => {
  test('a stale error snapshot ends a delete once the sandbox is gone', async () => {
    // delete() sends the DELETE, subscribes, and the snapshot still says
    // `failed` (taken before the DELETE moved the sandbox on). The hub reports
    // `error` at once; the read finds `deleting`, so the wait polls on to the
    // 404. The Python twin is in tests/test_terminal_report.py.
    const { createHttpClient } = await import('../src/v2/http')
    const { createSandbox } = await import('./helpers/sandbox')
    const { startFakeApi } = await import('./networkPolicyTestServer')
    const detail = { id: SANDBOX_ID, class: 'linux', name: SANDBOX_ID, status: 'deleting', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'regiona' }
    const api = await startFakeApi([
      { status: 200, body: {} },
      { status: 200, body: detail },
      { status: 404, body: { error: 'sandbox not found', code: 'NOT_FOUND' } },
    ])
    const fc = makeFakeConnector()
    const ws = fc.prepare()
    const hub = makeHub(fc)
    try {
      const sandbox = createSandbox({ id: SANDBOX_ID, class: 'linux', status: 'running', region_id: 'regiona' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, {}, undefined, undefined, hub)
      const deleting = sandbox.delete({ timeoutSeconds: 5 })
      for (let i = 0; i < 200 && !ws.sentFrames().some((f) => f['type'] === 'subscribe'); i++) await drain()
      expect(ws.sentFrames().some((f) => f['type'] === 'subscribe')).toBe(true)
      ws.inject({ type: 'snapshot', id: SANDBOX_ID, status: 'failed', version: 1, error: 'boom' })
      await deleting
      expect(api.requests.map((r) => `${r.method} ${r.path.split('?')[0]}`)).toEqual([
        `DELETE /sandboxes/${SANDBOX_ID}`, `GET /sandboxes/${SANDBOX_ID}`, `GET /sandboxes/${SANDBOX_ID}`,
      ])
      expect([sandbox.state, sandbox.rawState]).toEqual(['destroyed', 'deleted'])
    } finally {
      await hub.detach()
      await api.close()
    }
  }, 15_000)
})
