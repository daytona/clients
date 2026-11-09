/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Process-wide WaitHub for sandbox state events.
 *
 * One hub per process per (apiUrl, apiKey) identity, shared across `Daytona`
 * instances via ref-counting; WebSockets are shared by waits for the same destination.
 */

import { nestedDeeperThan } from './jsonDepth.js'
import type { AxiosHeaderValue, AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { createHttpClient, inBrowser, sandboxState } from './http.js'
import {
  BadRequestError,
  DaytonaError,
  AuthenticationError,
  ForbiddenError,
  NotFoundError,
  RequestTimeoutError,
  SandboxTerminalStateError,
  ServiceUnavailableError,
} from './errors/DaytonaError.js'
import type { SandboxEventsConfig, SandboxState } from './types.js'
import { MAX_TIMER_DELAY_MS, optionalSeconds } from './options.js'
import { WAIT_FOR_STATE_MIN_INTERVAL_MS } from './waitConstants.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SUBPROTOCOL = 'daytona.events.v1'
const TICKET_PROTOCOL_PREFIX = 'daytona.ticket.'

/**
 * The opaque events destination from the `x-daytona-cell` response header.
 * `''` when it is not set.
 *
 * @internal
 */
export function eventDestinationFromHeaders(headers: { readonly [name: string]: AxiosHeaderValue | undefined } | undefined): string {
  if (headers === undefined) return ''
  // Case-insensitive: axios keeps a header name's wire case, and an HTTP/1.1
  // response may spell it `X-Daytona-Cell`.
  for (const [name, value] of Object.entries(headers)) {
    if (name.toLowerCase() === 'x-daytona-cell' && value !== undefined && value !== null) return String(value)
  }
  return ''
}
const BACKOFF_BASE_MS = 250
const BACKOFF_CAP_MS = 30_000
const MAX_IDS_PER_FRAME = 1_000
const FALLBACK_RETRY_MS = 60_000
/**
 * The hub's own settings, in milliseconds: {@link SandboxEventsConfig} after
 * {@link eventsHubConfig} converted its seconds. Internal.
 */
export interface EventsHubConfig {
  readonly enabled?: boolean
  readonly reconcileIntervalMs?: number
  readonly idleCloseMs?: number
}

/** The hub settings for a public {@link SandboxEventsConfig} (seconds → ms). */
export function eventsHubConfig(config: SandboxEventsConfig | undefined): EventsHubConfig {
  // Validated (finite, non-negative) here, at construction; large values clamp
  // to the timer maximum where the hub arms them.
  const ms = (name: string, seconds: number | undefined): number | undefined => {
    const valid = optionalSeconds(name, seconds)
    return valid === undefined ? undefined : valid * 1000
  }
  return {
    enabled: config?.enabled,
    reconcileIntervalMs: ms('events.reconcileIntervalSeconds', config?.reconcileIntervalSeconds),
    idleCloseMs: ms('events.idleCloseSeconds', config?.idleCloseSeconds),
  }
}

/** The server's close code for a credential that is no longer valid. */
const CLOSE_UNAUTHORIZED = 4401
const TRANSIENT_FAILURE_THRESHOLD = 3
/** Consecutive `4401` closes before the waiters are told the credential
 *  cannot open a socket (see the socket loop). */
const MAX_CREDENTIAL_REFUSALS = 3
/**
 * The default `idleCloseMs` in a non-browser runtime where the events socket
 * cannot be released from the event loop (see `loopHolds`). Closing is then
 * the only way a script that never calls `close()` can exit, and every close
 * costs the next wait a ticket. The server allows 10 mints a minute per
 * credential, so a close at most every 10 s keeps a conforming client well
 * under it.
 */
export const DEFAULT_IDLE_CLOSE_WITHOUT_UNREF_MS = 10_000
/** The largest server frame either SDK hub accepts (1 MiB). */
export const MAX_SERVER_FRAME_BYTES = 1 << 20
/** How long to wait before re-subscribing an id a budget refused. */
const REFUSED_RETRY_MS = 2_000
const RECONCILE_JITTER = 0.10
const FALLBACK_SLICE_MS = 30_000
const FALLBACK_MIN_INTERVAL_MS = 200
const KNOWN_IDS_CAP = 10_000
const DEFAULT_OPEN_TIMEOUT_MS = 30_000
const DEFAULT_NO_FRAME_TIMEOUT_MS = 70_000
const DEFAULT_CLOSE_GRACE_MS = 1_000
const DEFAULT_EARLY_PROBE_MS = 10_000
const DEFAULT_CLIENT_PING_AFTER_MS = 5_000
const DEFAULT_SUBSCRIBE_FLUSH_MS = 50
/**
 * An id a waiter holds that has heard no state frame (and sent no subscribe)
 * for `DEFAULT_STALE_AFTER_MS` is re-subscribed at the next sweep tick; the
 * server answers with a fresh snapshot. This bounds the delay a missed event
 * adds to a wait (about 4 s), which neither the early probe (held off while the
 * socket is live, keepalives included) nor the reconcile timer does.
 */
const DEFAULT_STALE_AFTER_MS = 3_000
const DEFAULT_STALE_SWEEP_TICK_MS = 1_000
/** Ids per second the sweep re-subscribes at most: 75 % of the server's
 *  per-socket subscribe rate (2,000 ids/s), leaving room for fresh subscribes
 *  and refused retries. Past it the oldest go first and the rest wait a tick. */
const STALE_SWEEP_IDS_PER_SECOND = 1_500
const WAITER_PIN_INTERVAL_MS = MAX_TIMER_DELAY_MS
const WS_OPEN = 1
const WS_CLOSING = 2
const WS_CLOSED = 3


// ---------------------------------------------------------------------------
// Bare UUID RE (mirrors Python's _BARE_UUID_RE exactly)
// ---------------------------------------------------------------------------

const BARE_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// ---------------------------------------------------------------------------
// Predicate types (exported for Sandbox.ts / Daytona.ts)
// ---------------------------------------------------------------------------

/**
 * Wait for a target state, rejecting terminal states. Creation waits use the
 * same predicate: `create()` succeeds only when the sandbox reaches `started`.
 */
export interface Predicate {
  readonly kind: 'target'
  readonly states: ReadonlySet<SandboxState>
  readonly terminalStates: ReadonlySet<SandboxState>
}

export function targetPredicate(
  states: ReadonlySet<SandboxState>,
  terminalStates: ReadonlySet<SandboxState>,
): Predicate {
  return { kind: 'target', states, terminalStates }
}

type EvalResult = 'resolve' | 'reject' | 'keep'

function evaluate(predicate: Predicate, state: SandboxState): EvalResult {
  // `unknown` — a status this SDK does not recognize — is neither a match nor terminal:
  // every wait keeps waiting through it.
  if (state === 'unknown') return 'keep'
  if (predicate.states.has(state)) return 'resolve'
  if (predicate.terminalStates.has(state)) return 'reject'
  return 'keep'
}

// ---------------------------------------------------------------------------
// WebSocket factory (injectable for tests)
// ---------------------------------------------------------------------------

export type WebSocketFactory = (url: string, protocols: readonly string[]) => WebSocket | Promise<WebSocket>

/** The part of a Node socket the hub needs: whether it pins the event loop. */
export interface LoopHandle {
  ref(): void
  unref(): void
}

/**
 * Sockets whose transport the hub can release from the event loop.
 *
 * An open WebSocket keeps Node's event loop — and so the process — alive.
 * A socket with no waiter must not, or a script that never calls `close()`
 * hangs after its last wait; but closing it would make the next wait mint a
 * ticket and pay a handshake. So in Node the hub keeps the socket open and
 * `unref()`s its TCP socket while no waiter is registered, and `ref()`s it on
 * the next wait. The platform `WebSocket` (undici) exposes no socket; the
 * `ws` package does, through its `upgrade` event. A socket missing from this
 * map cannot be released, and falls back to the idle close.
 */
const loopHolds = new WeakMap<WebSocket, { handle: LoopHandle | undefined; held: boolean }>()

/** Register `ws`'s transport so the hub can release it (exported for tests). */
export function trackLoopHandle(ws: WebSocket, handle: LoopHandle): void {
  const hold = loopHolds.get(ws) ?? { handle: undefined, held: true }
  hold.handle = handle
  if (!hold.held) handle.unref()
  loopHolds.set(ws, hold)
}

function setLoopHold(ws: WebSocket | undefined, held: boolean): void {
  const hold = ws === undefined ? undefined : loopHolds.get(ws)
  if (hold === undefined || hold.held === held) return
  hold.held = held
  if (held) hold.handle?.ref()
  else hold.handle?.unref()
}

/** The `ws` package's constructor, as far as the hub uses it. */
type NodeWsCtor = new (
  url: string,
  protocols: string[],
  options: { maxPayload: number },
) => WebSocket & { on(event: 'upgrade', listener: (res: { socket: LoopHandle }) => void): unknown }

// A variable specifier keeps browser bundlers from resolving a Node-only package.
const WS_PACKAGE = 'ws'
let nodeWs: Promise<NodeWsCtor | undefined> | undefined

function loadNodeWs(): Promise<NodeWsCtor | undefined> {
  const isNode = typeof process !== 'undefined' && typeof process.versions?.node === 'string'
  if (inBrowser() || !isNode) return Promise.resolve(undefined)
  nodeWs ??= (import(/* @vite-ignore */ WS_PACKAGE) as Promise<{ WebSocket?: NodeWsCtor; default?: NodeWsCtor }>)
    .then((m) => m.WebSocket ?? m.default)
    .catch(() => undefined)
  return nodeWs
}

/** @internal The hub's socket factory (exported for the child-process tests). */
export const defaultWsFactory: WebSocketFactory = async (url, protocols): Promise<WebSocket> => {
  const NodeWs = await loadNodeWs()
  if (NodeWs === undefined) return new WebSocket(url, protocols as string[])
  // `maxPayload`: the same 1 MiB ceiling `runWs` enforces, applied before the
  // frame is buffered.
  const ws = new NodeWs(url, [...protocols], { maxPayload: MAX_SERVER_FRAME_BYTES })
  loopHolds.set(ws, { handle: undefined, held: true })
  ws.on('upgrade', (res) => {
    // A runtime whose `ws` stand-in hands back no real socket cannot release
    // it: forget the hold so the idle close applies instead of a hang.
    if (typeof res?.socket?.unref === 'function') trackLoopHandle(ws, res.socket)
    else loopHolds.delete(ws)
  })
  return ws
}

// ---------------------------------------------------------------------------
// Sentinel errors (internal to this module)
// ---------------------------------------------------------------------------

class HubFallback extends Error {
  constructor() {
    super('hub entered fallback mode')
    this.name = 'HubFallback'
  }
}

class PermanentFallbackError extends Error {
  constructor() {
    super('events endpoint unavailable (404 ticket)')
    this.name = 'PermanentFallbackError'
  }
}

class AuthErrorInternal extends Error {
  readonly cause: Error
  constructor(err: Error) {
    super(err.message)
    this.name = 'AuthErrorInternal'
    this.cause = err
  }
}

class ReconnectError extends Error {
  readonly credentialRefused: boolean

  constructor(message: string, options: { readonly credentialRefused?: boolean } = {}) {
    super(message)
    this.name = 'ReconnectError'
    this.credentialRefused = options.credentialRefused ?? false
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * The region tag of a sandbox id, or `_` when it carries none.
 *
 * A bare
 * 8-4-4-4-12 UUID has no tag; otherwise the tag is everything after the FIRST
 * `-` (base58 has none, so it is the delimiter), unless that dash leads or
 * trails the id. Leading and trailing dashes do not identify a region.
 */
export function regionOf(sandboxId: string): string {
  if (sandboxId === '' || BARE_UUID_RE.test(sandboxId)) return '_'
  const dash = sandboxId.indexOf('-')
  if (dash <= 0 || dash === sandboxId.length - 1) return '_'
  return sandboxId.slice(dash + 1)
}

/**
 * The socket URL for a region, derived from the API URL.
 *
 * The API's base PATH is kept: an api served at `https://host/platform/api`
 * answers the upgrade at `/platform/api/events/{region}`, and dropping the
 * prefix would dial the host root — where nothing answers — while the ticket
 * was minted through the full path.
 */
export function toWsUrl(apiUrl: string, region: string): string {
  // Parsed, not concatenated: an apiUrl carrying a query or fragment would
  // otherwise put the route inside it (`?x=1/events/region-a`) and the socket
  // would never reach the endpoint.
  try {
    const parsed = new URL(apiUrl)
    const scheme = parsed.protocol === 'https:' ? 'wss' : 'ws'
    const base = parsed.pathname.replace(/\/$/, '')
    return `${scheme}://${parsed.host}${base}/events/${region}`
  } catch {
    const stripped = apiUrl.replace(/\/$/, '')
    const scheme = /^https/i.test(stripped) ? 'wss' : 'ws'
    return `${scheme}://${stripped.replace(/^https?:\/\//i, '')}/events/${region}`
  }
}

function normalizeHubUrlIdentity(rawUrl: string): string {
  const stripped = rawUrl.replace(/\/$/, '')
  try {
    const parsed = new URL(stripped)
    return `${parsed.protocol.toLowerCase()}//${parsed.host.toLowerCase()}${parsed.pathname.replace(/\/$/, '')}`
  } catch {
    return stripped.replace(
      /^([a-z][a-z0-9+.-]*:\/\/)([^/?#]+)/i,
      (_match, scheme: string, host: string) => `${scheme.toLowerCase()}${host.toLowerCase()}`,
    )
  }
}

/**
 * Whether an error from the long-poll fallback will answer the same way on
 * every retry: a 4xx that is not a rate limit. Everything else — network
 * trouble, 5xx, 429 — is worth another poll inside the caller's budget.
 */
function isPermanentHttpError(err: unknown): boolean {
  if (!(err instanceof DaytonaError)) return false
  const status = err.statusCode
  return typeof status === 'number' && status >= 400 && status < 500 && status !== 429
}

function jittered(value: number): number {
  return value * (1 + RECONCILE_JITTER * (Math.random() * 2 - 1))
}

function backoffMs(attempt: number): number {
  const window = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * Math.pow(2, attempt))
  return Math.random() * window
}

function chunks<T>(arr: readonly T[], n: number): readonly (readonly T[])[] {
  const result: (readonly T[])[] = []
  for (let i = 0; i < arr.length; i += n) {
    result.push(arr.slice(i, i + n))
  }
  return result
}

function eventsDisabledEnv(): boolean {
  const val =
    (typeof process !== 'undefined' ? process.env?.['DAYTONA_SANDBOX_EVENTS'] : undefined) ?? ''
  return ['0', 'false', 'off'].includes(val.trim().toLowerCase())
}

function unrefTimer(t: ReturnType<typeof setTimeout> | ReturnType<typeof setInterval>): void {
  if (typeof t === 'object' && typeof (t as NodeJS.Timeout).unref === 'function') {
    ;(t as NodeJS.Timeout).unref()
  }
}

function sleepUnref(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms)
    unrefTimer(t)
  })
}

function armTimerUntilDeadline(
  deadlineAtMs: number,
  arm: (timer: ReturnType<typeof setTimeout>) => void,
  expire: () => void,
): void {
  const schedule = (): void => {
    const timeLeft = deadlineAtMs - Date.now()
    if (timeLeft <= 0) {
      expire()
      return
    }
    const t = setTimeout(schedule, Math.min(timeLeft, MAX_TIMER_DELAY_MS))
    unrefTimer(t)
    arm(t)
  }
  schedule()
}

function clampTimerDelayMs(ms: number | undefined): number | undefined {
  // `Infinity` would become a 1 ms timer in Node too, so it clamps like any
  // other over-limit delay; NaN is treated as unset (the caller's default).
  if (ms === undefined || Number.isNaN(ms)) return undefined
  if (ms <= MAX_TIMER_DELAY_MS) return ms
  return MAX_TIMER_DELAY_MS
}

/** The wire `wait=` value for the long-poll fallback, when one target maps to
 *  one wire status; `undefined` means plain polling. */
function predicateFallbackWaitState(predicate: Predicate): string | undefined {
  if (predicate.states.size !== 1) return undefined
  for (const [sdkState, wireState] of [
    ['started', 'running'],
    ['stopped', 'stopped'],
    ['paused', 'paused'],
    ['destroyed', 'deleted'],
  ] as const) {
    if (predicate.states.has(sdkState)) return wireState
  }
  return undefined
}

/** The terminal states the exact `wait_exact=deleted` long poll still answers
 *  on: `deleted` itself and `failed`/`lost` (public `error`). */
const EXACT_DELETED_ANSWERS: ReadonlySet<SandboxState> = new Set<SandboxState>(['destroyed', 'error'])

/**
 * The query parameter that long-polls for `wireState` in a wait that rejects
 * on `terminalStates`. `deleted` uses the exact form: the loose
 * `wait=deleted` is already met by `deleting`, so a deletion that takes a
 * while to finish would answer every poll at once instead of holding it. The
 * exact form answers on `failed` and `lost` too, but on no other state, so a
 * wait that also rejects on another state keeps the loose form and sees it at
 * once.
 */
export function waitQueryKey(wireState: string, terminalStates: Iterable<SandboxState>): 'wait' | 'wait_exact' {
  if (wireState !== 'deleted') return 'wait'
  for (const state of terminalStates) if (!EXACT_DELETED_ANSWERS.has(state)) return 'wait'
  return 'wait_exact'
}

// ---------------------------------------------------------------------------
// Waiter
// ---------------------------------------------------------------------------

/** What a wait resolved with: the decoded state and the wire status. */
export interface WaitOutcome {
  readonly state: SandboxState
  readonly rawState: string
}

interface Waiter {
  readonly sandboxId: string
  readonly predicate: Predicate
  readonly resolve: (outcome: WaitOutcome) => void
  readonly reject: (err: Error) => void
  readonly registeredAtMs: number
  readonly deadlineAtMs: number | undefined
  readonly observe?: (outcome: WaitOutcome) => void
  allowShortBudgetProbe: boolean
  timeoutTimer: ReturnType<typeof setTimeout> | undefined
  earlyProbeTimer: ReturnType<typeof setTimeout> | undefined
}

// ---------------------------------------------------------------------------
// Socket state
// ---------------------------------------------------------------------------

interface SocketState {
  key: string
  readonly region: string
  readonly subscriptions: Set<string>
  consecutiveFailures: number
  /** Consecutive `4401` closes. A fresh ticket is the right answer to the
   *  first one; repeating it without end is not. */
  credentialRefusals: number
  fallbackUntil: number | undefined
  permanentFallback: boolean
  loopRunning: boolean
  reconcileTimer: ReturnType<typeof setInterval> | undefined
  staleTimer: ReturnType<typeof setTimeout> | undefined
  ws: WebSocket | undefined
  readonly versions: Map<string, number>
  readonly rawStates: Map<string, string>
  /** Pending `idleCloseMs` close; undefined when the knob is off or a
   *  waiter is live. */
  idleTimer: ReturnType<typeof setTimeout> | undefined
  closeGraceTimer: ReturnType<typeof setTimeout> | undefined
  clientPingTimer: ReturnType<typeof setTimeout> | undefined
  clientPingOutstanding: boolean
  reconnectAttempt: number
  readonly refusedRetryIds: Set<string>
  refusedRetryTimer: ReturnType<typeof setTimeout> | undefined
  readonly pendingSubscribeIds: Set<string>
  readonly pendingUnsubscribeIds: Set<string>
  /** Ids whose pending unsubscribe a re-subscribe cancelled in this window: the
   *  server still holds them, so a dropped re-subscribe must unsubscribe after all. */
  readonly unsubscribeReplacedIds: Set<string>
  subscribeFlushTimer: ReturnType<typeof setTimeout> | undefined
  readonly snapshotPending: Map<string, number>
  /** When each subscribed id was last sent a subscribe or given a state frame. */
  heardAt: Map<string, number>
  /** The last decoded state per subscribed id: a new waiter on an id the
   *  socket already holds is judged against it at once (a sandbox resting in
   *  its target state emits no further event). */
  readonly states: Map<string, SandboxState>
  /** Resolved when a waiter registers; the socket loop parks on it while it
   *  has nothing to subscribe instead of polling. */
  wake: (() => void) | undefined
  clientClosing: boolean
  lastFrameAt: number
}

// ---------------------------------------------------------------------------
// Waiter utilities
// ---------------------------------------------------------------------------

function removeWaiter(waiters: Map<string, Waiter[]>, w: Waiter): void {
  const list = waiters.get(w.sandboxId)
  if (!list) return
  const idx = list.indexOf(w)
  if (idx >= 0) list.splice(idx, 1)
  if (list.length === 0) waiters.delete(w.sandboxId)
}

function waiterStillPending(waiters: Map<string, Waiter[]>, waiter: Waiter): boolean {
  return waiters.get(waiter.sandboxId)?.includes(waiter) ?? false
}

// ---------------------------------------------------------------------------
// Async poll loop (used by fallback sandboxPollFn callers)
// ---------------------------------------------------------------------------

export async function runPollLoop(
  sandboxId: string,
  predicate: Predicate,
  options: { readonly deadline: number | undefined; readonly fn: () => Promise<WaitOutcome> },
): Promise<WaitOutcome> {
  const { deadline, fn } = options
  for (;;) {
    const remaining = deadline === undefined ? undefined : deadline - Date.now()
    if (remaining !== undefined && remaining <= 0) {
      throw new RequestTimeoutError(
        `sandbox ${sandboxId} did not reach the requested state before timeout`,
        { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
      )
    }
    const outcome = await fn()
    const result = evaluate(predicate, outcome.state)
    if (result === 'resolve') return outcome
    if (result === 'reject') throw new SandboxTerminalStateError(sandboxId, outcome.state, { rawState: outcome.rawState })
  }
}

// ---------------------------------------------------------------------------
// WaitHub
// ---------------------------------------------------------------------------

/** @internal */
export class WaitHub {
  private readonly api: AxiosInstance
  private readonly apiUrl: string
  private readonly sockets: Map<string, SocketState>
  private readonly learned: Map<string, string>
  private readonly waiters: Map<string, Waiter[]>
  private readonly known: Map<string, true>
  private readonly config: { readonly enabled: boolean; readonly reconcileIntervalMs: number; readonly idleCloseMs: number | undefined }
  private readonly wsFactory: WebSocketFactory
  private readonly openTimeoutMs: number
  private readonly noFrameTimeoutMs: number
  private readonly closeGraceMs: number
  private readonly earlyProbeMs: number
  private readonly clientPingAfterMs: number
  private readonly subscribeFlushMs: number
  private readonly staleAfterMs: number
  private readonly staleSweepTickMs: number
  private readonly staleSweepMaxIds: number
  private refCount: number
  private waiterPin: ReturnType<typeof setInterval> | undefined
  closed: boolean

  constructor(
    apiUrl: string,
    apiKey: string,
    config: EventsHubConfig,
    wsFactory: WebSocketFactory,
    api?: AxiosInstance,
    timings: {
      readonly openTimeoutMs?: number
        readonly noFrameTimeoutMs?: number
        readonly closeGraceMs?: number
        readonly earlyProbeMs?: number
        readonly clientPingAfterMs?: number
        readonly subscribeFlushMs?: number
        readonly staleAfterMs?: number
        readonly staleSweepTickMs?: number
        readonly staleSweepMaxIds?: number
      } = {},
  ) {
    this.apiUrl = apiUrl.replace(/\/$/, '')
    // The SDK's own client (identity headers, typed errors), not a bare axios
    // instance: a 404 on the ticket must arrive as `NotFoundError`.
    this.api = api ?? createHttpClient(this.apiUrl, apiKey, 30_000)
    this.config = {
      enabled: config.enabled ?? true,
      // Leave room for the ±10% jitter so a clamped interval stays a valid timer.
      reconcileIntervalMs: Math.min(
        clampTimerDelayMs(config.reconcileIntervalMs ?? 60_000) ?? 60_000,
        Math.floor(MAX_TIMER_DELAY_MS / (1 + RECONCILE_JITTER)),
      ),
      // Unset means "the runtime's default", resolved per socket in
      // `idleCloseFor`: none where the socket can be released from the event
      // loop (Node) or never pins it (browsers).
      idleCloseMs: clampTimerDelayMs(config.idleCloseMs),
    }
    this.wsFactory = wsFactory
    this.openTimeoutMs = timings.openTimeoutMs ?? DEFAULT_OPEN_TIMEOUT_MS
    this.noFrameTimeoutMs = timings.noFrameTimeoutMs ?? DEFAULT_NO_FRAME_TIMEOUT_MS
    this.closeGraceMs = timings.closeGraceMs ?? DEFAULT_CLOSE_GRACE_MS
    this.sockets = new Map()
    this.learned = new Map()
    this.waiters = new Map()
    this.known = new Map()
    this.refCount = 0
    this.waiterPin = undefined
    this.closed = false
    this.earlyProbeMs = timings.earlyProbeMs ?? DEFAULT_EARLY_PROBE_MS
    this.clientPingAfterMs = timings.clientPingAfterMs ?? DEFAULT_CLIENT_PING_AFTER_MS
    this.subscribeFlushMs = clampTimerDelayMs(timings.subscribeFlushMs) ?? DEFAULT_SUBSCRIBE_FLUSH_MS
    this.staleAfterMs = timings.staleAfterMs ?? DEFAULT_STALE_AFTER_MS
    this.staleSweepTickMs = timings.staleSweepTickMs ?? DEFAULT_STALE_SWEEP_TICK_MS
    this.staleSweepMaxIds =
      timings.staleSweepMaxIds ?? Math.floor((STALE_SWEEP_IDS_PER_SECOND * DEFAULT_STALE_SWEEP_TICK_MS) / 1_000)
    credentialSafeDiagnostics(this, () => ({ type: 'WaitHub' }))
  }

  /** Whether waits ride the socket at all (config, then the environment). */
  get enabled(): boolean {
    return this.config.enabled && !eventsDisabledEnv()
  }

  attach(): void {
    this.refCount++
  }

  async detach(): Promise<void> {
    this.refCount--
    if (this.refCount <= 0) await this.closeHub()
  }

  private async closeHub(): Promise<void> {
    if (this.closed) return
    this.closed = true
    this.clearWaiterPin()
    for (const sock of this.sockets.values()) {
      clearReconcileTimer(sock)
      clearStaleTimer(sock)
      this.clearIdleClose(sock)
      this.clearClientPing(sock)
      clearCloseGraceTimer(sock)
      this.clearPendingSubscribeFlush(sock)
      if (sock.refusedRetryTimer !== undefined) {
        clearTimeout(sock.refusedRetryTimer)
        sock.refusedRetryTimer = undefined
      }
      sock.refusedRetryIds.clear()
      sock.wake?.()
      if (sock.ws !== undefined) {
        try {
          this.closeSocket(sock)
        } catch {
          // ignore
        }
      }
    }
    const err = new ServiceUnavailableError('hub closed', { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'SDK' })
    for (const list of this.waiters.values()) {
      for (const w of list) {
        this.clearWaiterTimers(w)
        w.reject(err)
      }
    }
    this.waiters.clear()
    this.clearWaiterPin()
  }

  private armWaiterPin(): void {
    if (this.waiters.size === 0 || this.waiterPin !== undefined) return
    this.waiterPin = setInterval(() => {}, WAITER_PIN_INTERVAL_MS)
  }

  private clearWaiterPin(): void {
    if (this.waiters.size > 0 || this.waiterPin === undefined) return
    clearInterval(this.waiterPin)
    this.waiterPin = undefined
  }

  private removeWaiter(waiter: Waiter): void {
    removeWaiter(this.waiters, waiter)
    this.clearWaiterPin()
  }

  private clearWaiterTimers(waiter: Waiter): void {
    if (waiter.timeoutTimer !== undefined) {
      clearTimeout(waiter.timeoutTimer)
      waiter.timeoutTimer = undefined
    }
    if (waiter.earlyProbeTimer !== undefined) {
      clearTimeout(waiter.earlyProbeTimer)
      waiter.earlyProbeTimer = undefined
    }
  }

  private takeWaiters(sandboxId: string): Waiter[] {
    const waiters = this.waiters.get(sandboxId) ?? []
    this.waiters.delete(sandboxId)
    this.clearWaiterPin()
    return waiters
  }

  private rememberKnown(sandboxId: string): void {
    if (this.known.has(sandboxId)) this.known.delete(sandboxId)
    this.known.set(sandboxId, true)
    if (this.known.size <= KNOWN_IDS_CAP) return
    const oldest = this.known.keys().next().value
    if (typeof oldest === 'string') this.known.delete(oldest)
  }

  // ------------------------------------------------------------------
  // Remembering events destinations
  // ------------------------------------------------------------------

  learn(sandboxId: string, destination: string): void {
    if (!destination) return
    const region = regionOf(sandboxId)
    if (region === '_') return
    const previousDestination = this.learned.get(region)
    if (previousDestination === destination) return
    this.learned.set(region, destination)
    this.rekeySocket(region, previousDestination, destination)
  }

  private rekeySocket(region: string, previousDestination: string | undefined, destination: string): void {
    const regionalSocket = this.sockets.get(region)
    const previousSocket = previousDestination === undefined ? undefined : this.sockets.get(previousDestination)
    const destinationSocket = this.sockets.get(destination)

    if (previousSocket !== undefined && previousDestination !== destination) {
      const targetSocket = destinationSocket ?? this.ensureSocket(destination, region)
      this.moveSocketSubscriptions(previousSocket.key, previousSocket, targetSocket, false)
      return
    }

    if (regionalSocket === undefined) return

    if (destinationSocket === undefined) {
      // Reuse the regional socket under the learned destination key.
      this.sockets.delete(region)
      regionalSocket.key = destination
      this.sockets.set(destination, regionalSocket)
    } else {
      this.moveSocketSubscriptions(region, regionalSocket, destinationSocket, true)
    }
  }

  private moveSocketSubscriptions(
    sourceKey: string,
    source: SocketState,
    destination: SocketState,
    preserveState: boolean,
  ): void {
    const movedIds = [...source.subscriptions]

    for (const sid of movedIds) {
      destination.subscriptions.add(sid)
      const sourceVersion = source.versions.get(sid)
      const destinationVersion = destination.versions.get(sid)
      if (sourceVersion !== undefined && (destinationVersion === undefined || sourceVersion > destinationVersion)) {
        destination.versions.set(sid, sourceVersion)
      }
      const pendingVersion = source.snapshotPending.get(sid)
      if (pendingVersion !== undefined) destination.snapshotPending.set(sid, pendingVersion)
      if (preserveState) {
        const sourceState = source.states.get(sid)
        if (sourceState !== undefined) destination.states.set(sid, sourceState)
        const sourceRawState = source.rawStates.get(sid)
        if (sourceRawState !== undefined) destination.rawStates.set(sid, sourceRawState)
      }
      if (source.refusedRetryIds.has(sid)) destination.refusedRetryIds.add(sid)
    }

    this.clearPendingSubscribeFlush(source)
    source.subscriptions.clear()
    source.states.clear()
    source.rawStates.clear()
    source.versions.clear()
    source.snapshotPending.clear()
    source.refusedRetryIds.clear()
    if (source.refusedRetryTimer !== undefined) {
      clearTimeout(source.refusedRetryTimer)
      source.refusedRetryTimer = undefined
    }
    destination.wake?.()
    if (destination.ws !== undefined && destination.ws.readyState === WS_OPEN && movedIds.length > 0) {
      void this.sendSubscribeIds(destination, movedIds)
    }
    this.ensureSocketLoop(destination)
    clearReconcileTimer(source)
    clearStaleTimer(source)
    this.clearIdleClose(source)
    this.closeSocket(source)
    this.sockets.delete(sourceKey)
    if (destination.refusedRetryIds.size > 0) this.scheduleRefusedRetry(destination)
  }

  // ------------------------------------------------------------------
  // Routing
  // ------------------------------------------------------------------

  private socketKeyFor(sandboxId: string, regionHint?: string): [string, string] {
    let region = regionOf(sandboxId)
    if (region === '_' && regionHint) region = regionHint
    const destination = this.learned.get(region)
    const key = destination ?? region
    return [key, region]
  }

  private ensureSocket(key: string, region: string): SocketState {
    let sock = this.sockets.get(key)
    if (sock === undefined) {
      sock = {
        key,
        region,
        subscriptions: new Set(),
        consecutiveFailures: 0,
        credentialRefusals: 0,
        reconnectAttempt: 0,
        fallbackUntil: undefined,
        permanentFallback: false,
        loopRunning: false,
        reconcileTimer: undefined,
        staleTimer: undefined,
        ws: undefined,
        versions: new Map(),
        rawStates: new Map(),
        idleTimer: undefined,
        closeGraceTimer: undefined,
        clientPingTimer: undefined,
        clientPingOutstanding: false,
        refusedRetryIds: new Set(),
        refusedRetryTimer: undefined,
        pendingSubscribeIds: new Set(),
        pendingUnsubscribeIds: new Set(),
        unsubscribeReplacedIds: new Set(),
        subscribeFlushTimer: undefined,
        snapshotPending: new Map(),
        heardAt: new Map(),
        states: new Map(),
        wake: undefined,
        clientClosing: false,
        lastFrameAt: Date.now(),
      }
      this.sockets.set(key, sock)
    }
    return sock
  }

  private ensureSocketLoop(sock: SocketState): void {
    if (!sock.loopRunning) {
      sock.loopRunning = true
      void this.socketLoop(sock)
    }
  }

  // ------------------------------------------------------------------
  // Public wait entry point
  // ------------------------------------------------------------------

  async wait(
    sandboxId: string,
    predicate: Predicate,
    options: {
      readonly timeout?: number
      readonly regionHint?: string
      readonly sandboxPollFn?: () => Promise<WaitOutcome>
      readonly onObserved?: (outcome: WaitOutcome) => void
    } = {},
  ): Promise<WaitOutcome> {
    const { timeout, regionHint, sandboxPollFn, onObserved } = options
    const deadline = timeout === undefined || timeout === 0 ? undefined : Date.now() + timeout

    const useFallback = async (): Promise<WaitOutcome> => {
      if (sandboxPollFn !== undefined) {
        return runPollLoop(sandboxId, predicate, { deadline, fn: sandboxPollFn })
      }
      if (deadline === undefined) return this.fallbackWait(sandboxId, predicate, undefined)
      const remaining = deadline - Date.now()
      if (remaining <= 0) {
        // The deadline passed while the socket was failing. `0` means
        // UNBOUNDED to every timeout argument in this SDK, so it must never
        // be produced by arithmetic: a spent budget is a timeout.
        throw new RequestTimeoutError(
          `sandbox ${sandboxId} did not reach the requested state before timeout`,
          { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
        )
      }
      return this.fallbackWait(sandboxId, predicate, remaining)
    }

    // A closed hub runs no socket loop, so a waiter registered on it would
    // never be decided. A handle made before `close()` still works: it polls.
    if (!this.enabled || this.closed) {
      return useFallback()
    }

    const [key, region] = this.socketKeyFor(sandboxId, regionHint)
    const sock = this.ensureSocket(key, region)

    if (
      sock.permanentFallback ||
      (sock.fallbackUntil !== undefined && Date.now() < sock.fallbackUntil)
    ) {
      return useFallback()
    }

    const held = sock.subscriptions.has(sandboxId) && !sock.snapshotPending.has(sandboxId)
      ? sock.states.get(sandboxId)
      : undefined
    const haveSnapshot = held !== undefined
    if (haveSnapshot) {
      const verdict = evaluate(predicate, held!)
      if (verdict === 'resolve') return { state: held!, rawState: sock.rawStates.get(sandboxId) ?? '' }
      if (verdict === 'reject') throw new SandboxTerminalStateError(sandboxId, held!, { rawState: sock.rawStates.get(sandboxId) })
    }

    const waiter = this.registerWaiter(sandboxId, predicate, deadline, onObserved, sandboxPollFn)
    this.clearIdleClose(sock)
    setLoopHold(sock.ws, true)
    this.scheduleClientPing(sock)
    const fresh = !sock.subscriptions.has(sandboxId)
    sock.subscriptions.add(sandboxId)
    this.ensureSocketLoop(sock)
    sock.wake?.()

    if (fresh) this.enqueueSubscribeIds(sock, [sandboxId])

    try {
      return await waiter.promise
    } catch (err) {
      if (err instanceof HubFallback) {
        this.removeWaiter(waiter)
        return useFallback()
      }
      this.removeWaiter(waiter)
      this.maybeUnsubscribeForId(sandboxId)
      throw err
    }
  }

  // ------------------------------------------------------------------
  // Waiter management
  // ------------------------------------------------------------------

  private registerWaiter(
    sandboxId: string,
    predicate: Predicate,
    deadline: number | undefined,
    observe: ((outcome: WaitOutcome) => void) | undefined,
    sandboxPollFn: (() => Promise<WaitOutcome>) | undefined,
  ): Waiter & { promise: Promise<WaitOutcome> } {
    let outerResolve!: (outcome: WaitOutcome) => void
    let outerReject!: (err: Error) => void

    const promise = new Promise<WaitOutcome>((resolve, reject) => {
      outerResolve = resolve
      outerReject = reject
    })

    const waiter: Waiter & { promise: Promise<WaitOutcome> } = {
      sandboxId,
      predicate,
      resolve: outerResolve,
      reject: outerReject,
      registeredAtMs: Date.now(),
      deadlineAtMs: deadline,
      observe,
      allowShortBudgetProbe: false,
      timeoutTimer: undefined,
      earlyProbeTimer: undefined,
      promise,
    }

    const list = this.waiters.get(sandboxId) ?? []
    list.push(waiter)
    this.waiters.set(sandboxId, list)
    this.armWaiterPin()

    if (deadline !== undefined) {
      const remaining = deadline - Date.now()
      if (remaining <= 0) {
        // Already expired — reject synchronously in next microtask
        Promise.resolve().then(() => {
          this.removeWaiter(waiter)
          this.maybeUnsubscribeForId(sandboxId)
          this.clearWaiterTimers(waiter)
          waiter.reject(
            new RequestTimeoutError(
              `sandbox ${sandboxId} did not reach the requested state before timeout`,
              { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
            ),
          )
        }).catch(() => { /* ignore */ })
      } else {
        armTimerUntilDeadline(deadline, (t) => {
          waiter.timeoutTimer = t
        }, () => {
          this.removeWaiter(waiter)
          this.maybeUnsubscribeForId(sandboxId)
          this.clearWaiterTimers(waiter)
          waiter.reject(
            new RequestTimeoutError(
              `sandbox ${sandboxId} did not reach the requested state before timeout`,
              { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
            ),
          )
        })
      }
    }

    if (sandboxPollFn !== undefined && this.earlyProbeMs > 0) {
      const pollFn = sandboxPollFn
      const remainingBudgetMs = deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
      const allowShortBudgetProbe = remainingBudgetMs !== undefined && remainingBudgetMs / 2 < this.earlyProbeMs
      const firstDelayMs = jittered(
        Math.min(this.earlyProbeMs, remainingBudgetMs === undefined ? this.earlyProbeMs : remainingBudgetMs / 2),
      )
      waiter.allowShortBudgetProbe = allowShortBudgetProbe
      const t = setTimeout(() => {
        void this.runEarlyProbe(waiter, pollFn)
      }, firstDelayMs)
      unrefTimer(t)
      waiter.earlyProbeTimer = t
    } else {
      waiter.allowShortBudgetProbe = false
    }

    return waiter
  }

  private rearmEarlyProbe(waiter: Waiter, sandboxPollFn: () => Promise<WaitOutcome>): void {
    const sock = this.findSocketForSandbox(waiter.sandboxId)
    if (sock === undefined) return
    const silentMs = Date.now() - Math.max(sock.lastFrameAt, waiter.registeredAtMs)
    const remainingQuietMs = Math.max(0, this.earlyProbeMs - silentMs)
    const t = setTimeout(() => { void this.runEarlyProbe(waiter, sandboxPollFn) }, jittered(remainingQuietMs))
    unrefTimer(t)
    waiter.earlyProbeTimer = t
  }

  private rearmEarlyProbeAfterNotReadyPoll(waiter: Waiter, sandboxPollFn: () => Promise<WaitOutcome>): void {
    const sock = this.findSocketForSandbox(waiter.sandboxId)
    if (sock === undefined) return
    const silentMs = Date.now() - Math.max(sock.lastFrameAt, waiter.registeredAtMs)
    const remainingQuietMs = Math.max(0, this.earlyProbeMs - silentMs)
    const nextDelayMs = Math.max(remainingQuietMs, WAIT_FOR_STATE_MIN_INTERVAL_MS)
    const t = setTimeout(() => { void this.runEarlyProbe(waiter, sandboxPollFn) }, jittered(nextDelayMs))
    unrefTimer(t)
    waiter.earlyProbeTimer = t
  }

  private async runEarlyProbe(
    waiter: Waiter,
    sandboxPollFn: () => Promise<WaitOutcome>,
  ): Promise<void> {
    waiter.earlyProbeTimer = undefined
    if (!waiterStillPending(this.waiters, waiter)) return
    const sock = this.findSocketForSandbox(waiter.sandboxId)
    if (sock === undefined) return

    const lastRelevantAt = Math.max(sock.lastFrameAt, waiter.registeredAtMs)
    const silentMs = Date.now() - lastRelevantAt
    const shortFirstProbe = waiter.allowShortBudgetProbe
    waiter.allowShortBudgetProbe = false
    const quietEnough = shortFirstProbe ? sock.lastFrameAt <= waiter.registeredAtMs : silentMs >= this.earlyProbeMs
    if (!quietEnough) {
      this.rearmEarlyProbe(waiter, sandboxPollFn)
      return
    }

    const beforePollAt = sock.lastFrameAt
    let outcome: WaitOutcome
    try {
      outcome = await sandboxPollFn()
    } catch (err) {
      if (!waiterStillPending(this.waiters, waiter)) return
      if (err instanceof NotFoundError || err instanceof AuthenticationError || err instanceof ForbiddenError) {
        this.clearWaiterTimers(waiter)
        this.removeWaiter(waiter)
        this.maybeUnsubscribeForId(waiter.sandboxId)
        waiter.reject(err as Error)
        return
      }
      const t = setTimeout(() => { void this.runEarlyProbe(waiter, sandboxPollFn) }, jittered(this.earlyProbeMs))
      unrefTimer(t)
      waiter.earlyProbeTimer = t
      return
    }
    if (!waiterStillPending(this.waiters, waiter)) return
    const currentSock = this.findSocketForSandbox(waiter.sandboxId)
    if (currentSock === undefined || currentSock !== sock || currentSock.lastFrameAt !== beforePollAt) {
      this.rearmEarlyProbe(waiter, sandboxPollFn)
      return
    }

    const result = evaluate(waiter.predicate, outcome.state)
    if (result === 'keep') {
      this.rearmEarlyProbeAfterNotReadyPoll(waiter, sandboxPollFn)
      return
    }
    // Settle directly — the probe's GET already refreshed state; calling
    // observe would reset refreshedByPoll causing a redundant second GET.
    this.clearWaiterTimers(waiter)
    this.removeWaiter(waiter)
    if (result === 'resolve') waiter.resolve(outcome)
    else waiter.reject(new SandboxTerminalStateError(waiter.sandboxId, outcome.state, { rawState: outcome.rawState }))
    this.maybeUnsubscribeForId(waiter.sandboxId)
  }

  private settleWaiterFromObservedOutcome(waiter: Waiter, outcome: WaitOutcome): void {
    waiter.observe?.(outcome)
    const result = evaluate(waiter.predicate, outcome.state)
    if (result === 'keep') return
    this.clearWaiterTimers(waiter)
    this.removeWaiter(waiter)
    if (result === 'resolve') waiter.resolve(outcome)
    else waiter.reject(new SandboxTerminalStateError(waiter.sandboxId, outcome.state, { rawState: outcome.rawState }))
    this.maybeUnsubscribeForId(waiter.sandboxId)
  }

  private maybeUnsubscribeForId(sandboxId: string): void {
    for (const sock of this.sockets.values()) {
      if (sock.subscriptions.has(sandboxId)) {
        this.maybeUnsubscribe(sock, sandboxId)
        break
      }
    }
  }

  private findSocketForSandbox(sandboxId: string): SocketState | undefined {
    for (const sock of this.sockets.values()) {
      if (sock.subscriptions.has(sandboxId)) return sock
    }
    return undefined
  }

  // ------------------------------------------------------------------
  // Fallback long-poll
  // ------------------------------------------------------------------

  private async fallbackWait(
    sandboxId: string,
    predicate: Predicate,
    timeout: number | undefined,
  ): Promise<WaitOutcome> {
    const deadline =
      timeout === undefined || timeout === 0 ? undefined : Date.now() + timeout
    const waitWire = predicateFallbackWaitState(predicate)

    for (;;) {
      const remaining = deadline === undefined ? undefined : deadline - Date.now()
      if (remaining !== undefined && remaining <= 0) {
        throw new RequestTimeoutError(
          `sandbox ${sandboxId} did not reach the requested state before timeout`,
          { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
        )
      }

      const sliceMs =
        remaining === undefined
          ? FALLBACK_SLICE_MS
          : Math.min(FALLBACK_SLICE_MS, Math.max(1, Math.ceil(remaining)))

      const params: Record<string, string | number> = {}
      if (waitWire !== undefined) {
        params[waitQueryKey(waitWire, predicate.terminalStates)] = waitWire
        params['timeout_ms'] = sliceMs
      }

      const pollStart = Date.now()
      let data: unknown
      try {
        const encodedId = encodeURIComponent(sandboxId)
        const response = await this.api.get<unknown>(`/sandboxes/${encodedId}`, {
          params: Object.keys(params).length > 0 ? params : undefined,
          timeout: waitWire !== undefined ? undefined : 30_000,
        })
        data = response.data
      } catch (err) {
        // Retry what a retry can fix. A 4xx that is not a rate limit will
        // answer the same way every time, and swallowing it here turns a
        // clear "not found" or "forbidden" into a wait that ends in a
        // timeout. (Python's fallback lets these propagate too.)
        if (isPermanentHttpError(err)) throw err
        await sleepUnref(FALLBACK_MIN_INTERVAL_MS)
        continue
      }

      if (data === null || typeof data !== 'object') {
        await sleepUnref(FALLBACK_MIN_INTERVAL_MS)
        continue
      }

      const record = data as Record<string, unknown>
      const status = String(record['status'] ?? '')
      const state = sandboxState(status)

      const result = evaluate(predicate, state)
      if (result === 'resolve') return { state, rawState: status }
      if (result === 'reject') {
        const reason = typeof record['error'] === 'string' ? record['error'] : undefined
        throw new SandboxTerminalStateError(sandboxId, state, { rawState: status, reason })
      }

      const elapsed = Date.now() - pollStart
      if (elapsed < FALLBACK_MIN_INTERVAL_MS) {
        const pause = Math.min(
          FALLBACK_MIN_INTERVAL_MS - elapsed,
          deadline !== undefined
            ? Math.max(0, deadline - Date.now())
            : FALLBACK_MIN_INTERVAL_MS,
        )
        if (pause > 0) await sleepUnref(pause)
      }
    }
  }

  // ------------------------------------------------------------------
  // Socket loop (one floating Promise per socket key)
  // ------------------------------------------------------------------

  private async socketLoop(sock: SocketState): Promise<void> {
    while (!this.closed) {
      if (!this.hasAnyWaiterFor(sock)) {
        if (this.sockets.get(sock.key) !== sock) break
        await new Promise<void>((resolve) => {
          sock.wake = resolve
        })
        sock.wake = undefined
        sock.reconnectAttempt = 0
        continue
      }
      try {
        await this.connectAndRun(sock)
        sock.reconnectAttempt = 0
        sock.credentialRefusals = 0
      } catch (err) {
        if (this.closed) break
        if (err instanceof AuthErrorInternal) {
          this.failSocketWaiters(sock, err.cause)
          break
        }
        if (err instanceof PermanentFallbackError) {
          sock.permanentFallback = true
          this.activatePermanentFallback(sock)
          break
        }
        if (err instanceof ReconnectError && err.credentialRefused) {
          if (sock.credentialRefusals >= MAX_CREDENTIAL_REFUSALS) {
            this.failSocketWaiters(
              sock,
              new AuthenticationError(
                'the credential was refused by the events socket; mint a new one and retry',
                { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' },
              ),
            )
            break
          }
          await sleepUnref(backoffMs(sock.credentialRefusals - 1))
          continue
        }
        sock.credentialRefusals = 0
        sock.consecutiveFailures++
        if (sock.consecutiveFailures >= TRANSIENT_FAILURE_THRESHOLD) {
          sock.fallbackUntil = Date.now() + FALLBACK_RETRY_MS
          this.notifyFallbackActive(sock)
          await sleepUnref(FALLBACK_RETRY_MS)
          sock.fallbackUntil = undefined
          sock.consecutiveFailures = 0
          continue
        }
        await sleepUnref(backoffMs(sock.reconnectAttempt))
        sock.reconnectAttempt++
      }
    }
    sock.ws = undefined
    sock.loopRunning = false
  }

  private async connectAndRun(sock: SocketState): Promise<void> {
    const ticket = await this.mintTicket(sock)
    if (this.closed) return
    const url = toWsUrl(this.apiUrl, sock.region)
    const protocols = [SUBPROTOCOL, `${TICKET_PROTOCOL_PREFIX}${ticket}`]

    const ws = await this.wsFactory(url, protocols)
    if (this.closed) {
      ws.onerror = (): void => {}
      try {
        const terminate = (ws as unknown as { terminate?: () => void }).terminate
        if (typeof terminate === 'function') terminate.call(ws)
        else ws.close()
      } catch {}
      return
    }
    sock.clientClosing = false
    sock.ws = ws
    this.clearPendingSubscribeFlush(sock)
    sock.clientPingOutstanding = false
    this.clearClientPing(sock)
    clearCloseGraceTimer(sock)
    // Every waiter may have left while the ticket was minted; the new socket
    // must not then hold the process open.
    if (!this.hasAnyWaiterFor(sock)) setLoopHold(ws, false)

    // Wait for the socket to open (or fail)
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        ws.onopen = null
        ws.onclose = null
        // Keep an error listener: closing a still-connecting `ws` socket
        // emits 'error' later, and an EventEmitter with none throws it as an
        // uncaught exception (a black-holed upgrade would crash the process).
        ws.onerror = (): void => {}
        try {
          const terminate = (ws as unknown as { terminate?: () => void }).terminate
          if (typeof terminate === 'function') terminate.call(ws)
          else ws.close()
        } catch {
          // Already torn down; the timeout is still the failure to report.
        }
        reject(new ReconnectError('WebSocket open timed out'))
      }, this.openTimeoutMs)
      unrefTimer(timeout)
      const onOpen = (): void => {
        clearTimeout(timeout)
        ws.onopen = null
        ws.onerror = (): void => {}
        ws.onclose = null
        resolve()
      }
      const onError = (ev: Event): void => {
        clearTimeout(timeout)
        ws.onopen = null
        ws.onerror = (): void => {}
        ws.onclose = null
        reject(new Error(`WebSocket open failed: ${String(ev)}`))
      }
      const onClose = (ev: CloseEvent): void => {
        clearTimeout(timeout)
        ws.onopen = null
        ws.onerror = (): void => {}
        ws.onclose = null
        reject(new Error(`WebSocket closed before open: code ${ev.code}`))
      }
      if (ws.readyState === WS_OPEN) {
        clearTimeout(timeout)
        resolve()
        return
      }
      if (ws.readyState === WS_CLOSED || ws.readyState === WS_CLOSING) {
        clearTimeout(timeout)
        reject(new Error('WebSocket already closed'))
        return
      }
      ws.onopen = onOpen
      ws.onerror = onError
      ws.onclose = onClose
    })

    if (this.sockets.get(sock.key) !== sock) {
      sock.ws = undefined
      try {
        ws.close(1000, 'orphaned')
      } catch {}
      return
    }

    // The failure count is reset by the first frame (`runWs`), not by the
    // open: a server that answers 101 and then says nothing must still reach
    // the fallback threshold.
    this.startReconcileTimer(sock)
    this.armStaleSweep(sock)

    try {
      await this.resubscribeAll(sock)
      this.scheduleClientPing(sock)
      await this.runWs(sock, ws)
    } finally {
      clearReconcileTimer(sock)
      clearStaleTimer(sock)
      clearCloseGraceTimer(sock)
      this.invalidateSocketCache(sock)
      sock.ws = undefined
      this.clearPendingSubscribeFlush(sock)
      try {
        ws.close()
      } catch {
        // ignore
      }
    }
  }

  private invalidateSocketCache(sock: SocketState): void {
    for (const sid of sock.subscriptions) {
      sock.states.delete(sid)
      sock.rawStates.delete(sid)
    }
  }

  // ------------------------------------------------------------------
  // Ticket minting
  // ------------------------------------------------------------------

  private async mintTicket(sock: SocketState): Promise<string> {
    const region = sock.region
    try {
      const resp = await this.api.post<{ ticket?: unknown }>(`/events/${region}/ticket`)
      const data = resp.data
      if (!data || typeof data !== 'object') throw new Error('ticket response is not JSON object')
      const ticket = String((data as Record<string, unknown>)['ticket'] ?? '')
      if (!ticket) throw new Error('ticket response missing ticket field')
      return ticket
    } catch (err) {
      // Already-translated DaytonaErrors from the axios interceptor
      if (err instanceof NotFoundError || (err instanceof Error && (err as { statusCode?: number }).statusCode === 404)) {
        throw new PermanentFallbackError()
      }
      if (err instanceof AuthenticationError) throw new AuthErrorInternal(err)
      if (err instanceof ForbiddenError) throw new AuthErrorInternal(err)
      throw err
    }
  }

  // ------------------------------------------------------------------
  // WebSocket frame loop
  // ------------------------------------------------------------------

  private runWs(sock: SocketState, ws: WebSocket): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      let settled = false
      let livenessTimer: ReturnType<typeof setTimeout> | undefined

      const resetLiveness = (): void => {
        if (livenessTimer !== undefined) clearTimeout(livenessTimer)
        livenessTimer = setTimeout(() => {
          // Detach first: closing fires `onclose`, which would otherwise
          // settle this as a clean client close — no failure counted, and an
          // immediate reconnect in a tight loop against a silent server.
          cleanup()
          try {
            this.closeSocket(sock)
          } catch {
            // Already closing; the timeout is the failure to report.
          }
          // terminate() releases the ws package's ref'd 30 s closeTimeout so
          // the process can exit promptly after a liveness timeout.
          try {
            const terminate = (ws as unknown as { terminate?: () => void }).terminate
            if (typeof terminate === 'function') terminate.call(ws)
          } catch { /* already torn down */ }
          reject(new ReconnectError('WebSocket liveness timeout'))
        }, this.noFrameTimeoutMs)
        unrefTimer(livenessTimer)
      }

      const cleanup = (): void => {
        if (settled) return
        settled = true
        if (livenessTimer !== undefined) {
          clearTimeout(livenessTimer)
          livenessTimer = undefined
        }
        ws.onmessage = null
        // Never leave a `ws` socket without an error listener: a transport
        // error during the close that follows would otherwise be thrown as
        // an uncaught EventEmitter error and crash the process.
        ws.onerror = (): void => {}
        ws.onclose = null
      }

      resetLiveness()

      ws.onmessage = (event: MessageEvent): void => {
        resetLiveness()
        this.noteSocketFrame(sock)
        const raw = event.data
        if (typeof raw !== 'string') return
        // No real frame is anywhere near this (the largest is a snapshot
        // carrying an error string); one past it is a broken or hostile
        // server, and parsing it would only cost memory. Same ceiling as the
        // Python hub's `max_size`.
        // `length` counts UTF-16 units; a unit is at most 3 UTF-8 bytes, so
        // only a frame that might be over the byte cap pays for encoding.
        if (
          raw.length > MAX_SERVER_FRAME_BYTES ||
          (raw.length * 3 > MAX_SERVER_FRAME_BYTES &&
            new TextEncoder().encode(raw).byteLength > MAX_SERVER_FRAME_BYTES)
        ) {
          try {
            ws.close(1009, 'message too big')
          } catch {
            // already closing
          }
          return
        }
        let frame: Record<string, unknown>
        try {
          frame = JSON.parse(raw) as Record<string, unknown>
        } catch {
          return
        }
        // A frame nested deeper than every JSON body may be is skipped like
        // an undecodable one (`JSON_MAX_DEPTH`, as in Python).
        if (nestedDeeperThan(frame)) return
        if (typeof frame !== 'object' || frame === null || Array.isArray(frame)) return

        const ftype = frame['type']

        if (ftype === 'ping') {
          try {
            ws.send(JSON.stringify({ type: 'pong' }))
          } catch {
            // ignore send errors
          }
          return
        }

        if (ftype === 'pong') return

        if (ftype === 'snapshot' || ftype === 'event') {
          const sid = frame['id']
          if (typeof sid !== 'string') return
          const wireState = String(frame['status'] ?? '')
          const version = frame['version']
          if (typeof version !== 'number') return

          this.rememberKnown(sid)

          const pendingVersion = sock.snapshotPending.get(sid)
          if (ftype === 'event' && pendingVersion !== undefined && version <= pendingVersion) return
          if (ftype === 'snapshot' || pendingVersion !== undefined) sock.snapshotPending.delete(sid)

          const held = sock.versions.get(sid) ?? -1
          if (version < held) return
          // A frame for an id this socket no longer holds (unsubscribed, or
          // moved to another socket after a stale destination mapping) must not resolve
          // the id's waiters: they now listen elsewhere.
          if (!sock.subscriptions.has(sid)) return
          sock.versions.set(sid, version)
          sock.heardAt.set(sid, Date.now())

          const decoded = sandboxState(wireState)
          sock.states.set(sid, decoded)
          sock.rawStates.set(sid, wireState)
          this.dispatchState(sock, sid, { state: decoded, rawState: wireState })
          return
        }

        if (ftype === 'error') {
          const sid = frame['id']
          const code = frame['code']
          if (code === 'LIMIT' && sid === null) {
            this.retrySocketLevelLimit(sock)
            return
          }
          if (typeof sid !== 'string') return
          if (!sock.subscriptions.has(sid)) return
          const wasKnown = Boolean(frame['was_known'])
          if (code === 'NOT_FOUND') {
            this.dispatchNotFound(sock, sid, wasKnown)
          } else if (code === 'LIMIT') {
            // The server refused this id and will send no snapshot for it;
            // waiting for one is waiting forever. Retry it after a pause,
            // which is what the frame means.
            this.retryRefused(sock, sid)
          } else if (code === 'MALFORMED') {
            // The id itself does not parse: retrying cannot help.
            this.failWaiters(
              sid,
              new BadRequestError(
                `the server rejected sandbox id '${sid}' as malformed`,
                { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' },
              ),
            )
            this.releaseSubscription(sock, sid)
          }
        }
      }

      ws.onerror = (): void => {
        cleanup()
        clearCloseGraceTimer(sock)
        reject(new ReconnectError('WebSocket transport error'))
      }

      ws.onclose = (event: CloseEvent): void => {
        cleanup()
        clearCloseGraceTimer(sock)
        if (sock.clientClosing) {
          sock.clientClosing = false
          sock.credentialRefusals = 0
          resolve()
          return
        }
        if (event.code === CLOSE_UNAUTHORIZED) {
          sock.credentialRefusals += 1
          reject(new ReconnectError(`WebSocket closed: code ${event.code}`, { credentialRefused: true }))
          return
        }
        sock.credentialRefusals = 0
        reject(new ReconnectError(`WebSocket closed: code ${event.code}`))
      }

      // Handle already-closed socket
      if (ws.readyState === WS_CLOSED || ws.readyState === WS_CLOSING) {
        cleanup()
        clearCloseGraceTimer(sock)
        if (sock.clientClosing) {
          sock.clientClosing = false
          resolve()
        } else {
          reject(new ReconnectError('WebSocket already closed'))
        }
      }
    })
  }

  // ------------------------------------------------------------------
  // Frame dispatch helpers
  // ------------------------------------------------------------------

  private dispatchState(sock: SocketState, sandboxId: string, outcome: WaitOutcome): void {
    const { state } = outcome
    const list = this.waiters.get(sandboxId)
    if (!list) return

    const toResolve: Waiter[] = []
    const toReject: Array<[Waiter, Error]> = []

    for (const w of [...list]) {
      w.observe?.(outcome)
      const result = evaluate(w.predicate, state)
      if (result === 'resolve') {
        toResolve.push(w)
      } else if (result === 'reject') {
        toReject.push([w, new SandboxTerminalStateError(sandboxId, state, { rawState: outcome.rawState })])
      }
    }

    for (const w of toResolve) {
      this.clearWaiterTimers(w)
      w.resolve(outcome)
      this.removeWaiter(w)
    }

    for (const [w, err] of toReject) {
      this.clearWaiterTimers(w)
      w.reject(err)
      this.removeWaiter(w)
    }

    this.maybeUnsubscribe(sock, sandboxId)
  }

  private dispatchNotFound(sock: SocketState, sandboxId: string, wasKnown: boolean): void {
    const region = regionOf(sandboxId)
    const destination = this.learned.get(region)
    // A learned destination may become stale.
    const mapRouted = destination !== undefined && sock.key === destination

    if (mapRouted) {
      // Forget the stale destination and retry through the sandbox's region.
      this.learned.delete(region)
      this.releaseSubscription(sock, sandboxId)
      void this.retryViaRegion(sandboxId, region)
      return
    }

    // Not map-routed: apply was_known semantics.
    if (wasKnown || this.known.has(sandboxId)) {
      const waiters = this.takeWaiters(sandboxId)
      for (const w of waiters) {
        this.clearWaiterTimers(w)
        w.reject(
          new NotFoundError(
            `sandbox ${sandboxId} is no longer present; its final state was not retained`,
            { statusCode: 404, code: 'NOT_FOUND', source: 'SDK' },
          ),
        )
      }
      this.maybeUnsubscribe(sock, sandboxId)
      return
    }
    const waiters = this.takeWaiters(sandboxId)
    for (const w of waiters) {
      this.clearWaiterTimers(w)
      w.reject(new HubFallback())
    }
    this.maybeUnsubscribe(sock, sandboxId)
  }

  /** Re-subscribe an id a budget refused, after a pause. */
  private retryRefused(sock: SocketState, sandboxId: string): void {
    if ((this.waiters.get(sandboxId)?.length ?? 0) === 0) return
    sock.refusedRetryIds.add(sandboxId)
    this.scheduleRefusedRetry(sock)
  }

  private retrySocketLevelLimit(sock: SocketState): void {
    for (const sandboxId of sock.subscriptions) {
      if ((this.waiters.get(sandboxId)?.length ?? 0) === 0) continue
      sock.refusedRetryIds.add(sandboxId)
    }
    if (sock.refusedRetryIds.size > 0) this.scheduleRefusedRetry(sock)
  }

  private enqueueSubscribeIds(sock: SocketState, ids: readonly string[]): void {
    if (ids.length === 0) return
    for (const id of ids) {
      if (sock.pendingUnsubscribeIds.delete(id)) sock.unsubscribeReplacedIds.add(id)
      sock.pendingSubscribeIds.add(id)
    }
    this.scheduleSubscribeFlush(sock)
  }

  private enqueueUnsubscribeIds(sock: SocketState, ids: readonly string[]): void {
    if (ids.length === 0) return
    for (const id of ids) {
      if (sock.pendingSubscribeIds.delete(id)) {
        // Never sent, so nothing to undo — unless it had replaced a pending
        // unsubscribe for an id the server still holds.
        if (!sock.unsubscribeReplacedIds.delete(id)) continue
      }
      sock.pendingUnsubscribeIds.add(id)
    }
    this.scheduleSubscribeFlush(sock)
  }

  private scheduleSubscribeFlush(sock: SocketState): void {
    if (sock.subscribeFlushTimer !== undefined) return
    const t = setTimeout(() => {
      sock.subscribeFlushTimer = undefined
      void this.flushPendingSubscriptions(sock)
    }, this.subscribeFlushMs)
    unrefTimer(t)
    sock.subscribeFlushTimer = t
  }

  private async flushPendingSubscriptions(sock: SocketState): Promise<void> {
    if (this.closed) {
      sock.pendingSubscribeIds.clear()
      sock.pendingUnsubscribeIds.clear()
      sock.unsubscribeReplacedIds.clear()
      return
    }

    const subscribeIds = [...sock.pendingSubscribeIds].filter(
      (id) => sock.subscriptions.has(id) && (this.waiters.get(id)?.length ?? 0) > 0,
    )
    // Unsubscribe every pending id this socket no longer wants: no waiter, or
    // its waiter now listens on another socket. A dropped re-subscribe that had
    // replaced an unsubscribe counts too — the server still holds the id.
    const resent = new Set(subscribeIds)
    const unsubscribeIds = [...new Set([...sock.pendingUnsubscribeIds, ...sock.unsubscribeReplacedIds])].filter(
      (id) => !resent.has(id) && !((this.waiters.get(id)?.length ?? 0) > 0 && sock.subscriptions.has(id)),
    )
    sock.pendingSubscribeIds.clear()
    sock.pendingUnsubscribeIds.clear()
    sock.unsubscribeReplacedIds.clear()

    const ws = sock.ws
    if (ws === undefined || ws.readyState !== WS_OPEN) {
      return
    }

    if (subscribeIds.length > 0) await this.sendSubscribeIds(sock, subscribeIds)
    if (unsubscribeIds.length > 0) this.sendUnsubscribeIds(ws, unsubscribeIds)
  }

  private clearPendingSubscribeFlush(sock: SocketState): void {
    if (sock.subscribeFlushTimer !== undefined) {
      clearTimeout(sock.subscribeFlushTimer)
      sock.subscribeFlushTimer = undefined
    }
    sock.pendingSubscribeIds.clear()
    sock.pendingUnsubscribeIds.clear()
    sock.unsubscribeReplacedIds.clear()
  }

  private scheduleRefusedRetry(sock: SocketState): void {
    if (sock.refusedRetryTimer !== undefined) return
    const t = setTimeout(() => {
      sock.refusedRetryTimer = undefined
      void this.flushRefusedRetry(sock)
    }, REFUSED_RETRY_MS)
    unrefTimer(t)
    sock.refusedRetryTimer = t
  }

  private async flushRefusedRetry(sock: SocketState): Promise<void> {
    if (this.closed) {
      sock.refusedRetryIds.clear()
      return
    }
    const retryIds: string[] = []
    for (const id of [...sock.refusedRetryIds]) {
      if ((this.waiters.get(id)?.length ?? 0) === 0 || !sock.subscriptions.has(id)) {
        sock.refusedRetryIds.delete(id)
        continue
      }
      if (retryIds.length >= MAX_IDS_PER_FRAME) continue
      sock.refusedRetryIds.delete(id)
      retryIds.push(id)
    }
    if (retryIds.length > 0) {
      await this.sendSubscribeIds(sock, retryIds)
    }
    for (const id of [...sock.refusedRetryIds]) {
      if ((this.waiters.get(id)?.length ?? 0) === 0 || !sock.subscriptions.has(id)) {
        sock.refusedRetryIds.delete(id)
      }
    }
    if (sock.refusedRetryIds.size > 0) {
      this.scheduleRefusedRetry(sock)
    }
  }

  private failWaiters(sandboxId: string, error: Error): void {
    const waiters = this.takeWaiters(sandboxId)
    for (const w of waiters) {
      this.clearWaiterTimers(w)
      w.reject(error)
    }
  }

  private async retryViaRegion(sandboxId: string, region: string): Promise<void> {
    if ((this.waiters.get(sandboxId)?.length ?? 0) === 0) return
    const sock = this.ensureSocket(region, region)
    this.clearIdleClose(sock)
    setLoopHold(sock.ws, true)
    sock.subscriptions.add(sandboxId)
    this.ensureSocketLoop(sock)
    sock.wake?.()
    if (sock.ws !== undefined && sock.ws.readyState === WS_OPEN) {
      await this.sendSubscribeIds(sock, [sandboxId])
    }
  }

  // ------------------------------------------------------------------
  // Subscribe / unsubscribe helpers
  // ------------------------------------------------------------------

  private async sendSubscribeIds(sock: SocketState, ids: readonly string[]): Promise<void> {
    const ws = sock.ws
    if (ws === undefined || ws.readyState !== WS_OPEN) return
    const now = Date.now()
    for (const id of ids) {
      sock.snapshotPending.set(id, sock.versions.get(id) ?? -1)
      sock.heardAt.set(id, now)
    }
    for (const chunk of chunks(ids, MAX_IDS_PER_FRAME)) {
      const frame = {
        type: 'subscribe',
        ids: chunk.map((id) => ({ id, known: this.known.has(id) })),
      }
      try {
        ws.send(JSON.stringify(frame))
      } catch {
        // ignore
      }
    }
  }

  private sendUnsubscribeIds(ws: WebSocket, ids: readonly string[]): void {
    for (const chunk of chunks(ids, MAX_IDS_PER_FRAME)) {
      try {
        ws.send(JSON.stringify({ type: 'unsubscribe', ids: chunk }))
      } catch {
        // ignore
      }
    }
  }

  private async resubscribeAll(sock: SocketState): Promise<void> {
    const ids = [...sock.subscriptions].filter(
      (sid) => (this.waiters.get(sid)?.length ?? 0) > 0,
    )
    if (ids.length > 0) {
      await this.sendSubscribeIds(sock, ids)
    }
  }

  private maybeUnsubscribe(sock: SocketState, sandboxId: string): void {
    if ((this.waiters.get(sandboxId)?.length ?? 0) > 0) return
    this.releaseSubscription(sock, sandboxId)
  }

  private releaseSubscription(sock: SocketState, sandboxId: string): void {
    sock.subscriptions.delete(sandboxId)
    sock.states.delete(sandboxId)
    sock.rawStates.delete(sandboxId)
    sock.versions.delete(sandboxId)
    sock.snapshotPending.delete(sandboxId)
    sock.refusedRetryIds.delete(sandboxId)
    this.enqueueUnsubscribeIds(sock, [sandboxId])
    this.armIdleClose(sock)
    this.scheduleClientPing(sock)
  }

  // ------------------------------------------------------------------
  // Idle: a socket with no waiters stays open, because a cold socket
  // makes the next wait mint a ticket and pay an exposed handshake. It stops
  // holding the event loop instead (`loopHolds`); the idle close applies only
  // when configured, or where the runtime cannot release the socket.
  // ------------------------------------------------------------------

  private armIdleClose(sock: SocketState): void {
    if (this.hasAnyWaiterFor(sock)) return
    this.clearClientPing(sock)
    setLoopHold(sock.ws, false)
    const idleMs = this.idleCloseFor(sock.ws)
    if (idleMs === undefined || idleMs <= 0) return
    if (sock.idleTimer !== undefined) return
    const t = setTimeout(() => {
      sock.idleTimer = undefined
      if (this.hasAnyWaiterFor(sock) || sock.ws === undefined) return
      this.closeSocket(sock, 1000, 'idle')
    }, idleMs)
    unrefTimer(t)
    sock.idleTimer = t
  }

  /** The configured idle close, else the runtime's default for this socket. */
  private idleCloseFor(ws: WebSocket | undefined): number | undefined {
    if (this.config.idleCloseMs !== undefined) return this.config.idleCloseMs
    if (inBrowser()) return undefined
    return ws !== undefined && loopHolds.get(ws)?.handle !== undefined ? undefined : DEFAULT_IDLE_CLOSE_WITHOUT_UNREF_MS
  }

  private clearIdleClose(sock: SocketState): void {
    if (sock.idleTimer !== undefined) {
      clearTimeout(sock.idleTimer)
      sock.idleTimer = undefined
    }
  }

  private clearClientPing(sock: SocketState): void {
    if (sock.clientPingTimer !== undefined) {
      clearTimeout(sock.clientPingTimer)
      sock.clientPingTimer = undefined
    }
  }

  private noteSocketFrame(sock: SocketState): void {
    sock.lastFrameAt = Date.now()
    sock.consecutiveFailures = 0
    sock.credentialRefusals = 0
    sock.reconnectAttempt = 0
    sock.clientPingOutstanding = false
    this.scheduleClientPing(sock)
  }

  private scheduleClientPing(sock: SocketState): void {
    this.clearClientPing(sock)
    if (this.clientPingAfterMs <= 0 || sock.clientPingOutstanding) return
    const ws = sock.ws
    if (ws === undefined || ws.readyState !== WS_OPEN || !this.hasAnyWaiterFor(sock)) return
    const silentMs = Date.now() - sock.lastFrameAt
    const delayMs = Math.max(0, this.clientPingAfterMs - silentMs)
    const t = setTimeout(() => {
      sock.clientPingTimer = undefined
      const currentWs = sock.ws
      if (
        sock.clientPingOutstanding ||
        currentWs === undefined ||
        currentWs.readyState !== WS_OPEN ||
        !this.hasAnyWaiterFor(sock)
      ) {
        return
      }
      const currentSilentMs = Date.now() - sock.lastFrameAt
      if (currentSilentMs < this.clientPingAfterMs) {
        this.scheduleClientPing(sock)
        return
      }
      try {
        currentWs.send(JSON.stringify({ type: 'ping' }))
        sock.clientPingOutstanding = true
      } catch {}
    }, delayMs)
    unrefTimer(t)
    sock.clientPingTimer = t
  }

  // ------------------------------------------------------------------
  // Reconcile timer (per socket)
  // ------------------------------------------------------------------

  private startReconcileTimer(sock: SocketState): void {
    const intervalMs = this.config.reconcileIntervalMs
    if (intervalMs <= 0) return
    if (sock.reconcileTimer !== undefined) return
    const delay = Math.round(jittered(intervalMs))
    const t = setInterval(() => {
      if (sock.ws !== undefined && sock.ws.readyState === WS_OPEN) {
        void this.resubscribeAll(sock)
      }
    }, delay)
    unrefTimer(t)
    sock.reconcileTimer = t
  }

  /**
   * Re-subscribe every waited-on id that has heard nothing for `staleAfterMs`.
   * A wait whose transition event never arrives would otherwise end only at
   * the reconcile timer. The frame path pays one map store; a tick scans the
   * subscriptions and sends nothing while every id is fresh. `ws.send` queues
   * synchronously, so no flush can interleave with the sweep's frames.
   */
  private armStaleSweep(sock: SocketState): void {
    if (this.staleSweepTickMs <= 0 || sock.staleTimer !== undefined) return
    const t = setTimeout(() => {
      sock.staleTimer = undefined
      const ws = sock.ws
      if (this.closed || ws === undefined) return
      if (sock.heardAt.size > sock.subscriptions.size) {
        // Drop the times of released ids.
        const kept = new Map<string, number>()
        for (const sid of sock.subscriptions) {
          const at = sock.heardAt.get(sid)
          if (at !== undefined) kept.set(sid, at)
        }
        sock.heardAt = kept
      }
      const heardAt = sock.heardAt
      const cutoff = Date.now() - this.staleAfterMs
      const pending = sock.pendingSubscribeIds
      let stale: string[] = []
      for (const sid of sock.subscriptions) {
        if ((heardAt.get(sid) ?? 0) <= cutoff && !pending.has(sid) && (this.waiters.get(sid)?.length ?? 0) > 0) {
          stale.push(sid)
        }
      }
      if (stale.length > this.staleSweepMaxIds) {
        stale = stale.sort((a, b) => (heardAt.get(a) ?? 0) - (heardAt.get(b) ?? 0)).slice(0, this.staleSweepMaxIds)
      }
      if (stale.length > 0 && ws.readyState === WS_OPEN) void this.sendSubscribeIds(sock, stale)
      this.armStaleSweep(sock)
    }, Math.round(jittered(this.staleSweepTickMs)))
    unrefTimer(t)
    sock.staleTimer = t
  }

  // ------------------------------------------------------------------
  // Fallback helpers
  // ------------------------------------------------------------------

  private activatePermanentFallback(sock: SocketState): void {
    const err = new HubFallback()
    for (const sid of [...sock.subscriptions]) {
      const waiters = this.takeWaiters(sid)
      for (const w of waiters) {
        this.clearWaiterTimers(w)
        w.reject(err)
      }
    }
    sock.subscriptions.clear()
    sock.states.clear()
    sock.rawStates.clear()
    sock.versions.clear()
    sock.snapshotPending.clear()
    sock.refusedRetryIds.clear()
    this.clearPendingSubscribeFlush(sock)
    sock.clientPingOutstanding = false
    this.clearClientPing(sock)
  }

  private notifyFallbackActive(sock: SocketState): void {
    const err = new HubFallback()
    for (const sid of [...sock.subscriptions]) {
      const waiters = this.takeWaiters(sid)
      for (const w of waiters) {
        this.clearWaiterTimers(w)
        w.reject(err)
      }
    }
    sock.subscriptions.clear()
    sock.states.clear()
    sock.rawStates.clear()
    sock.versions.clear()
    sock.snapshotPending.clear()
    sock.refusedRetryIds.clear()
    this.clearPendingSubscribeFlush(sock)
    sock.clientPingOutstanding = false
    this.clearClientPing(sock)
  }

  private failSocketWaiters(sock: SocketState, error: Error): void {
    for (const sid of [...sock.subscriptions]) {
      const waiters = this.takeWaiters(sid)
      for (const w of waiters) {
        this.clearWaiterTimers(w)
        w.reject(error)
      }
    }
    sock.subscriptions.clear()
    sock.states.clear()
    sock.rawStates.clear()
    sock.versions.clear()
    sock.snapshotPending.clear()
    sock.refusedRetryIds.clear()
    this.clearPendingSubscribeFlush(sock)
    sock.clientPingOutstanding = false
    this.clearClientPing(sock)
  }

  private closeSocket(sock: SocketState, code?: number, reason?: string): void {
    const ws = sock.ws
    if (ws === undefined) return
    this.clearClientPing(sock)
    this.clearPendingSubscribeFlush(sock)
    sock.clientClosing = true
    try {
      if (code === undefined) ws.close()
      else ws.close(code, reason)
      if (typeof (ws as WebSocket & { terminate?: () => void }).terminate === 'function') {
        clearCloseGraceTimer(sock)
        sock.closeGraceTimer = setTimeout(() => {
          sock.closeGraceTimer = undefined
          if (sock.ws !== ws || ws.readyState === WS_CLOSED) return
          try {
            ;(ws as WebSocket & { terminate: () => void }).terminate()
          } catch {}
        }, this.closeGraceMs)
        unrefTimer(sock.closeGraceTimer)
      }
    } catch {
      sock.clientClosing = false
    }
  }

  // ------------------------------------------------------------------
  // Utility
  // ------------------------------------------------------------------

  private hasAnyWaiterFor(sock: SocketState): boolean {
    for (const sid of sock.subscriptions) {
      if ((this.waiters.get(sid)?.length ?? 0) > 0) return true
    }
    return false
  }
}

// ---------------------------------------------------------------------------
// Reconcile timer helpers (module-level to avoid closure over `this`)
// ---------------------------------------------------------------------------

function clearReconcileTimer(sock: SocketState): void {
  if (sock.reconcileTimer !== undefined) {
    clearInterval(sock.reconcileTimer)
    sock.reconcileTimer = undefined
  }
}

function clearStaleTimer(sock: SocketState): void {
  if (sock.staleTimer !== undefined) {
    clearTimeout(sock.staleTimer)
    sock.staleTimer = undefined
  }
}

function clearCloseGraceTimer(sock: SocketState): void {
  if (sock.closeGraceTimer !== undefined) {
    clearTimeout(sock.closeGraceTimer)
    sock.closeGraceTimer = undefined
  }
}

// ---------------------------------------------------------------------------
// Process-wide hub registry
// ---------------------------------------------------------------------------

const HUB_REGISTRY = new Map<string, WeakRef<WaitHub>>()
const _hubRegistry_finalization = new FinalizationRegistry<string>((key) => {
  const ref = HUB_REGISTRY.get(key)
  if (ref !== undefined && ref.deref() === undefined) HUB_REGISTRY.delete(key)
})

function hubIdentity(
  apiUrl: string,
  apiKey: string,
  target: string | undefined,
  config: EventsHubConfig,
  limits: { readonly requestTimeoutMs: unknown; readonly maxContentLength: unknown } = { requestTimeoutMs: null, maxContentLength: null },
): string {
  // Clients that differ in the request timeout or the body cap of the API
  // instance the hub uses get their own hub, so each client's limits apply to
  // its hub's requests.
  return JSON.stringify([
    normalizeHubUrlIdentity(apiUrl),
    apiKey,
    (target ?? '').toLowerCase(),
    config.enabled ?? true,
    config.reconcileIntervalMs ?? 60_000,
    config.idleCloseMs ?? null,
    limits.requestTimeoutMs ?? null,
    limits.maxContentLength ?? null,
  ])
}

/** @internal */
export interface AttachHubOptions {
  readonly apiUrl: string
  readonly apiKey: string
  readonly target: string | undefined
  readonly shared: boolean
  readonly config: EventsHubConfig
  readonly wsFactory?: WebSocketFactory
  /**
   * The attaching client's API instance; its buffered-body cap
   * (`maxResponseBytes`) then bounds the ticket mint and the long-poll
   * fallback too. Without one the hub builds its own, under `maxResponseBytes`.
   */
  readonly _api?: AxiosInstance
  /** The cap on buffered bodies for a hub that builds its own API instance. */
  readonly maxResponseBytes?: number
}

/** @internal */
export function attachHub(options: AttachHubOptions): WaitHub {
  const { apiUrl, apiKey, target, shared, config, wsFactory } = options
  const _api = options._api ?? (options.maxResponseBytes === undefined
    ? undefined
    : createHttpClient(apiUrl.replace(/\/$/, ''), apiKey, 30_000, { maxResponseBytes: options.maxResponseBytes }))

  if (!shared) {
    const hub = new WaitHub(apiUrl, apiKey, config, wsFactory ?? defaultWsFactory, _api)
    hub.attach()
    return hub
  }

  const key = hubIdentity(apiUrl, apiKey, target, config, {
    requestTimeoutMs: _api?.defaults.timeout,
    maxContentLength: _api?.defaults.maxContentLength ?? options.maxResponseBytes,
  })
  const existing = HUB_REGISTRY.get(key)?.deref()
  if (existing !== undefined && !existing.closed) {
    existing.attach()
    return existing
  }

  const hub = new WaitHub(apiUrl, apiKey, config, wsFactory ?? defaultWsFactory, _api)
  hub.attach()
  const ref = new WeakRef(hub)
  HUB_REGISTRY.set(key, ref)
  _hubRegistry_finalization.register(hub, key)
  return hub
}

/** @internal */
export async function detachHub(
  hub: WaitHub,
  options: {
    readonly apiUrl: string
    readonly apiKey: string
    readonly target: string | undefined
    readonly shared: boolean
  },
): Promise<void> {
  await hub.detach()
  if (hub.closed && options.shared) {
    for (const [key, ref] of HUB_REGISTRY) {
      if (ref.deref() === hub) { HUB_REGISTRY.delete(key); break }
    }
  }
}

/** @internal Exported for testing only. */
export const _hubRegistryForTest = HUB_REGISTRY
