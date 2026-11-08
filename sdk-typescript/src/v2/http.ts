/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios, { AxiosError } from 'axios'
import type { AxiosHeaders, AxiosInstance, AxiosRequestConfig, AxiosResponse, GenericAbortSignal, InternalAxiosRequestConfig } from 'axios'
import { ConnectionError, createAxiosDaytonaError, createDaytonaError, AuthenticationError, DaytonaError, BadRequestError, errorFromFetchResponse, ClientTimeoutError } from './errors/DaytonaError.js'
import { DISABLED } from './types.js'
import { boundedTimerDelay, badRequest } from './options.js'
import type { LifecycleAction, LifecyclePolicyOptions, SandboxLifecycle, SandboxLifecycleDto, SandboxState, SandboxClass } from './types.js'
import { SDK_VERSION } from '../version.js'
import { credentialSafeDiagnostics } from '../diagnostics.js'

/** `X-Daytona-Source` value for TypeScript SDK requests. */
export const SDK_SOURCE = 'sdk-typescript'

const ABSOLUTE_REQUEST_URL = /^(?:[a-z][a-z\d+\-.]*:)?\/\//i
const RELATIVE_PATH_ERROR = 'absolute request URLs are not allowed; pass a relative SDK path'
/**
 * The default request timeout: the total time one ordinary request may take,
 * from sending it to reading the last byte of the response. Requests governed
 * by another budget are not cut by it: waits and long polls pass their own
 * `timeout` (`0` = none), streams read through `fetch` are bounded only until
 * their response headers arrive, and file transfers are not bounded at all.
 * The Python SDK gives `request_timeout` the same meaning.
 */
export const DEFAULT_REQUEST_TIMEOUT_MS = 60_000
const TOKEN_REFRESH_WINDOW_MS = 30_000
const TOKEN_EXCHANGE_GRANT_TYPE = 'urn:ietf:params:oauth:grant-type:token-exchange'
const TOKEN_EXCHANGE_SUBJECT_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:jwt'
const OTEL_IMPORT_OVERRIDE = '__DAYTONA_OTEL_IMPORT__'
const ID_COLLECTION_SEGMENTS = new Set([
  'api-keys',
  'charges',
  'images',
  'invitations',
  'invoices',
  'members',
  'orgs',
  'processes',
  'projects',
  'recordings',
  'registries',
  'sandboxes',
  'secrets',
  'service-accounts',
  'sessions',
  'snapshots',
  'volumes',
])

const COLLECTION_STATIC_SEGMENTS: Readonly<Record<string, ReadonlySet<string>>> = {
  'api-keys': new Set(['current']),
  invitations: new Set(['accept', 'decline', 'lookup']),
  reservations: new Set(['bulk']),
  sandboxes: new Set(['batch-wait', 'from-snapshot', 'macos', 'search']),
}

function staticSegmentsForCollection(collection: string): ReadonlySet<string> {
  return COLLECTION_STATIC_SEGMENTS[collection] ?? new Set<string>()
}

type NodeAgentCtor = new (options: { readonly keepAlive: boolean; readonly maxSockets: number }) => unknown

type NodeAgents = {
  readonly httpAgent: unknown
  readonly httpsAgent: unknown
}

/** Structural twin of OTel `Context`; only ever passed back into the API that produced it. */
type OtelContextLike = {
  getValue(key: symbol): unknown
  setValue(key: symbol, value: unknown): OtelContextLike
  deleteValue(key: symbol): OtelContextLike
}
type OtelCarrier = Record<string, string>

type OtelSpanContextLike = {
  readonly traceId: string
  readonly spanId: string
  readonly traceFlags: number
}

interface OtelSpanLike {
  spanContext(): OtelSpanContextLike
  setAttribute(name: string, value: string | number | boolean): void
  recordException(error: Error): void
  setStatus(status: { readonly code: number; readonly message?: string }): void
  end(): void
}

interface OtelTracerLike {
  startSpan(
    name: string,
    options: {
      readonly kind: number
      readonly attributes: Record<string, string | number | boolean>
    },
  ): OtelSpanLike
}

/**
 * Structural subset of `@opentelemetry/api` the SDK uses. Pass your app's
 * `import * as otel from '@opentelemetry/api'` as `otelApi` to skip the SDK's
 * own module resolution (required in bundled browser apps).
 */
export interface OtelApiLike {
  readonly trace: {
    getTracer(name: string, version: string): OtelTracerLike
    setSpan(context: OtelContextLike, span: OtelSpanLike): OtelContextLike
  }
  readonly context: {
    active(): OtelContextLike
  }
  readonly propagation: {
    inject(context: OtelContextLike, carrier: OtelCarrier): void
  }
  readonly SpanKind: {
    readonly CLIENT: number
  }
  readonly SpanStatusCode: {
    readonly OK: number
    readonly ERROR: number
  }
}

type Span = OtelSpanLike
type SpanAttributeValue = string | number | boolean

type OtelBundle = {
  readonly api: OtelApiLike
  readonly tracer: OtelTracerLike
}

type TokenExchangeResponse = {
  readonly access_token?: unknown
  readonly expires_in?: unknown
}

type PlatformToken = {
  readonly accessToken: string
  readonly expiresAtMs: number
  /** Refresh this far before expiry: 30 s, or half the lifetime for short TTLs. */
  readonly refreshWindowMs: number
}

type DaytonaRequestMeta = {
  /**
   * When the request's total deadline expires (`nowMs()` clock). Set when the
   * deadline is first armed and kept across a 401 refresh and its retry, so
   * one call never gets a second full budget.
   */
  readonly deadlineAt?: number
  readonly routeTemplate?: string
  readonly sandboxId?: string
  readonly retryOnUnauthorized?: boolean
  readonly retriedAfterUnauthorized?: boolean
  readonly signal?: AbortSignalLike
  readonly span?: Span
}

type DaytonaAxiosRequestConfig = InternalAxiosRequestConfig & {
  daytona?: DaytonaRequestMeta
}

const AXIOS_TRANSPORT_CONFIG = Symbol('daytonaAxiosTransportConfig')

type AxiosTransportConfig = {
  readonly apiKey?: string
  readonly requestTimeout?: number
  readonly httpOptions?: HttpClientOptions
}

type AxiosInstanceWithTransportConfig = AxiosInstance & {
  [AXIOS_TRANSPORT_CONFIG]?: AxiosTransportConfig
}

type HeaderGetter = {
  get(name: string): unknown
}

type AbortSignalLike = {
  readonly aborted: boolean
  addEventListener?: (type: 'abort', listener: () => void, options?: boolean | AddEventListenerOptions) => void
  removeEventListener?: (type: 'abort', listener: () => void, options?: boolean | EventListenerOptions) => void
  readonly reason?: unknown
}

export interface HttpAuthProvider {
  authorizationHeader(options?: {
    readonly forceRefresh?: boolean
    readonly rejectedAuthorization?: string
    /**
     * The calling operation's abort signal. Aborting it stops THIS caller
     * waiting on a token exchange; the exchange itself keeps running for the
     * other requests sharing it.
     */
    readonly signal?: AbortSignalLike
  }): Promise<string>
}

type AuthorizationHeaderOptions = {
  readonly forceRefresh?: boolean
  readonly rejectedAuthorization?: string
}

export interface HttpClientOptions {
  readonly authProvider?: HttpAuthProvider
  readonly connectionPoolMaxsize?: number
  readonly otelEnabled?: boolean
  readonly otelApi?: OtelApiLike
  readonly sandboxId?: string
  /** Per-sandbox authentication hook (mints / refreshes short-lived JWT). */
  readonly auth?: TransportAuth
}

export interface HttpTransportConfig {
  readonly apiKey?: string
  readonly requestTimeout?: number
  readonly httpOptions?: HttpClientOptions
}

export interface FetchRequestInit extends RequestInit {
  readonly bodyFactory?: () => BodyInit | Promise<BodyInit>
  /** A file transfer: the request timeout does not apply (only the caller's `signal` ends it). */
  readonly transfer?: boolean
  /**
   * Replaces the request timeout (milliseconds) for this call, with the same
   * meaning: it bounds the call until the response headers arrive (a stream's
   * body is never bounded); `0` or above the timer maximum is no limit.
   */
  readonly timeout?: number
}

interface OTelImportOverride {
  [OTEL_IMPORT_OVERRIDE]?: () => Promise<OtelApiLike>
}

interface PlatformTokenProviderOptions {
  readonly baseURL: string
  readonly jwtToken: string
  readonly projectId: string
  readonly requestTimeout?: number
  readonly otelEnabled?: boolean
  readonly otelApi?: OtelApiLike
}

export function inBrowser(): boolean {
  return typeof globalThis !== 'undefined' &&
    (typeof (globalThis as { window?: unknown }).window !== 'undefined' ||
      typeof (globalThis as { importScripts?: unknown }).importScripts === 'function')
}

/**
 * Resolve with `promise`, or reject with the signal's reason as soon as it
 * aborts — WITHOUT cancelling the underlying work (it may be a token exchange
 * shared by other callers).
 */
function abortableWait<T>(promise: Promise<T>, signal?: AbortSignalLike): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) {
    promise.catch(() => undefined)
    return Promise.reject(signal.reason instanceof Error ? signal.reason : new DOMException('aborted', 'AbortError'))
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => {
      reject(signal.reason instanceof Error ? signal.reason : new DOMException('aborted', 'AbortError'))
    }
    signal.addEventListener?.('abort', onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener?.('abort', onAbort)
        resolve(value)
      },
      (error: unknown) => {
        signal.removeEventListener?.('abort', onAbort)
        reject(error instanceof Error ? error : new Error(String(error)))
      },
    )
  })
}

function errorName(error: unknown): string | undefined {
  return typeof error === 'object' && error !== null && 'name' in error && typeof error.name === 'string'
    ? error.name
    : undefined
}

function timeoutErrorFromAbortSignal(signal: AbortSignalLike | undefined, error: unknown): ClientTimeoutError | undefined {
  const reason = errorName(error) === 'TimeoutError' ? error : signal?.reason
  if (errorName(reason) === 'TimeoutError') {
    return new ClientTimeoutError()
  }
  return undefined
}

/**
 * Whether `error` is a fetch abort. Matched on the standard `name`, not on
 * `instanceof DOMException`: fetch polyfills (Remix's `@remix-run/web-fetch`)
 * reject with their own `Error` subclass, and a `DOMException` from another
 * realm fails `instanceof` against this one.
 */
export function isAbortError(error: unknown): error is DOMException | Error {
  return errorName(error) === 'AbortError'
}

function abortErrorFromSignal(signal: AbortSignalLike | undefined): DOMException | Error | undefined {
  return isAbortError(signal?.reason) ? signal.reason : undefined
}

function nowMs(): number {
  const perf = (globalThis as { performance?: { now?: () => number } }).performance
  return typeof perf?.now === 'function' ? perf.now() : Date.now()
}

export function authHeaders(apiKey?: string): Record<string, string> {
  const headers: Record<string, string> = {}
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`
  headers['X-Daytona-Source'] = SDK_SOURCE
  headers['X-Daytona-SDK-Version'] = SDK_VERSION
  // Browsers forbid setting User-Agent (edge fetch runtimes drop it silently);
  // the X-Daytona-* pair is accepted by API and toolbox CORS preflights.
  if (!inBrowser()) headers['User-Agent'] = `${SDK_SOURCE}/${SDK_VERSION}`
  return headers
}

function bearerCredentialFromAuthorizationHeader(header: string): string {
  if (header.startsWith('Bearer ')) return header.slice(7)
  throw new AuthenticationError('auth provider returned a non-Bearer authorization header', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
}

function daytonaHeadersObject(headers: HeadersInit | undefined): Record<string, string> {
  const out: Record<string, string> = {}
  if (headers === undefined) return out
  const normalized = new Headers(headers)
  normalized.forEach((value, key) => {
    out[key] = value
  })
  return out
}

function axiosHeaderValue(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.find((entry): entry is string => typeof entry === 'string')
  return undefined
}

function setAxiosHeader(config: AxiosRequestConfig, name: string, value: string): void {
  const headers = config.headers
  if (headers !== undefined && typeof (headers as AxiosHeaders).set === 'function') {
    ;(headers as AxiosHeaders).set(name, value)
    return
  }
  const nextHeaders: Record<string, string> = {}
  if (headers !== undefined && typeof headers === 'object') {
    for (const [key, entry] of Object.entries(headers as Record<string, unknown>)) {
      const headerValue = axiosHeaderValue(entry)
      if (headerValue !== undefined) nextHeaders[key] = headerValue
    }
  }
  nextHeaders[name] = value
  config.headers = nextHeaders
}

function getAxiosHeader(config: AxiosRequestConfig, name: string): string | undefined {
  const headers = config.headers
  if (headers !== undefined && typeof (headers as AxiosHeaders).get === 'function') {
    const value = (headers as AxiosHeaders).get(name)
    return axiosHeaderValue(value)
  }
  if (headers === undefined || typeof headers !== 'object') return undefined
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === name.toLowerCase()) return axiosHeaderValue(value)
  }
  return undefined
}

function sandboxIdFromPath(path: string | undefined, fallback: string | undefined): string | undefined {
  if (fallback) return fallback
  if (!path) return undefined
  const segments = path.split('?')[0]?.split('/').filter((segment) => segment.length > 0) ?? []
  const sandboxCollectionIndex = segments.findIndex((segment) => segment === 'sandboxes')
  if (sandboxCollectionIndex >= 0) {
    const candidate = segments[sandboxCollectionIndex + 1]
    return candidate && !staticSegmentsForCollection(segments[sandboxCollectionIndex] ?? '').has(candidate) ? candidate : undefined
  }
  return undefined
}

/** Exported for the parity tests against the Python SDK's `_route_template`. */
export function routeTemplate(path: string): string {
  const segments = path.split('?')[0]?.split('/') ?? []
  const templated = segments.map((segment, index, all) => {
    if (!segment || index === 0) return segment
    const previous = all[index - 1]
    // `ports` is also an id-bearing collection, so the port check must win.
    if (previous === 'ports' || previous === 'port') return '{port}'
    // `/computeruse/process/{name}/...`: computeruse itself is a namespace.
    if (previous === 'process' && all[index - 2] === 'computeruse') return '{id}'
    if (ID_COLLECTION_SEGMENTS.has(previous)) {
      if (staticSegmentsForCollection(previous).has(segment)) return segment
      return '{id}'
    }
    return segment
  })
  return templated.join('/')
}

function requestRouteTemplate(path: string | undefined, explicit: string | undefined): string {
  if (explicit) return explicit
  return routeTemplate(path ?? '/')
}

function serverAddress(baseURL: string | undefined): string | undefined {
  if (!baseURL) return undefined
  try {
    return new URL(baseURL).hostname || undefined
  } catch {
    return undefined
  }
}

function hasStatusCode(error: unknown, code: number): boolean {
  return error instanceof AxiosError ? error.response?.status === code : error instanceof DaytonaError && error.statusCode === code
}

function isNodeModuleMissing(error: unknown): boolean {
  return error instanceof Error && /Cannot find module|Cannot find package/.test(error.message)
}

function axiosTransportConfig(instance: AxiosInstance): AxiosTransportConfig | undefined {
  return (instance as AxiosInstanceWithTransportConfig)[AXIOS_TRANSPORT_CONFIG]
}

function hasHeaderGetter(value: unknown): value is HeaderGetter {
  return typeof value === 'object' && value !== null && 'get' in value && typeof Reflect.get(value, 'get') === 'function'
}

function apiKeyFromAxiosInstance(instance: AxiosInstance): string | undefined {
  const headers = instance.defaults.headers
  let authorization: string | undefined
  if (hasHeaderGetter(headers)) {
    authorization = axiosHeaderValue(headers.get('Authorization'))
  } else if (headers !== undefined && typeof headers === 'object') {
    const common = 'common' in headers && typeof headers.common === 'object' ? headers.common : undefined
    const headerEntries = Object.entries(common ?? headers)
    for (const [key, value] of headerEntries) {
      if (key.toLowerCase() === 'authorization') {
        authorization = axiosHeaderValue(value)
        break
      }
    }
  }
  if (authorization === undefined) return undefined
  try {
    return bearerCredentialFromAuthorizationHeader(authorization)
  } catch {
    return undefined
  }
}

function timeoutFromAxiosInstance(instance: AxiosInstance): number | undefined {
  return typeof instance.defaults.timeout === 'number' && Number.isFinite(instance.defaults.timeout)
    ? instance.defaults.timeout
    : undefined
}

function timeoutFromAxiosRequest(config: AxiosRequestConfig, instance: AxiosInstance): number {
  if (typeof config.timeout === 'number' && Number.isFinite(config.timeout)) return config.timeout
  return timeoutFromAxiosInstance(instance) ?? DEFAULT_REQUEST_TIMEOUT_MS
}

async function authorizationHeaderForAxiosRequest(
  provider: HttpAuthProvider,
  options: {
    readonly forceRefresh?: boolean
    readonly rejectedAuthorization?: string
    readonly signal?: AbortSignalLike
  },
  timeoutMs: number,
): Promise<string> {
  const timeout = timeoutSignal(timeoutMs, options.signal)
  try {
    // Bounded even when a provider ignores the signal it is given.
    return await abortableWait(provider.authorizationHeader({ ...options, signal: timeout.signal }), timeout.signal)
  } catch (error) {
    if (isAbortError(error) && timeout.timedOut()) {
      throw requestBudgetTimeout()
    }
    const timeoutError = timeoutErrorFromAbortSignal(timeout.signal, error)
    if (timeoutError) throw timeoutError
    const abortError = abortErrorFromSignal(timeout.signal)
    if (abortError) throw abortError
    throw error
  } finally {
    timeout.clearTimer()
    timeout.detach()
  }
}

async function importOtelApi(): Promise<OtelApiLike> {
  const override = (globalThis as typeof globalThis & OTelImportOverride)[OTEL_IMPORT_OVERRIDE]
  if (override) {
    try {
      return await override()
    } catch (error) {
      if (isNodeModuleMissing(error)) {
        throw new BadRequestError('otelEnabled requires the optional peer dependency @opentelemetry/api to be installed', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      }
      throw error
    }
  }
  try {
    // Node fallback. The specifier is built at runtime so TypeScript never
    // resolves the optional peer's types (consumers without it would hit
    // TS2307) — which also means bundlers cannot see it; browser apps pass
    // their own module via `otelApi` instead.
    if (inBrowser()) {
      throw new BadRequestError('otelEnabled in a browser requires passing your @opentelemetry/api module as `otelApi`', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    const specifier = '@opentelemetry/api'
    const loaded: unknown = await import(/* @vite-ignore */ /* webpackIgnore: true */ specifier)
    return loaded as OtelApiLike
  } catch (error) {
    if (isNodeModuleMissing(error)) {
      throw new BadRequestError('otelEnabled requires the optional peer dependency @opentelemetry/api to be installed', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    throw error
  }
}

let otelBundlePromise: Promise<OtelBundle> | undefined

async function loadOtelBundle(provided?: OtelApiLike): Promise<OtelBundle> {
  if (provided) {
    return { api: provided, tracer: provided.trace.getTracer('daytona', SDK_VERSION) }
  }
  const override = (globalThis as typeof globalThis & OTelImportOverride)[OTEL_IMPORT_OVERRIDE]
  if (override) {
    const api = await importOtelApi()
    return { api, tracer: api.trace.getTracer('daytona', SDK_VERSION) }
  }
  otelBundlePromise ??= importOtelApi().then((api) => ({
    api,
    tracer: api.trace.getTracer('daytona', SDK_VERSION),
  }))
  return otelBundlePromise
}

function isOneShotAxiosBody(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false
  if (typeof ReadableStream !== 'undefined' && data instanceof ReadableStream) return true
  const candidate = data as { readonly pipe?: unknown; readonly [Symbol.asyncIterator]?: unknown }
  return typeof candidate.pipe === 'function' || typeof candidate[Symbol.asyncIterator] === 'function'
}

function isReadableStreamBody(body: BodyInit | null | undefined): body is ReadableStream<Uint8Array> {
  return typeof ReadableStream !== 'undefined' && body instanceof ReadableStream
}

async function fetchRequestForAttempt(
  init: FetchRequestInit | undefined,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<{
  readonly requestInit: RequestInit
  readonly canRetryUnauthorized: boolean
}> {
  const { bodyFactory, transfer: _transfer, timeout: _timeout, ...requestInitWithoutFactory } = init ?? {}
  const body = bodyFactory
    ? await abortableWait(Promise.resolve().then(() => bodyFactory()), signal)
    : requestInitWithoutFactory.body
  return {
    requestInit: {
      ...requestInitWithoutFactory,
      body,
      headers,
      signal,
    },
    canRetryUnauthorized: bodyFactory !== undefined || !isReadableStreamBody(body),
  }
}

function fetchRequestWithoutFactory(
  init: FetchRequestInit | undefined,
  headers: Record<string, string>,
  signal: AbortSignal,
): {
  readonly requestInit: RequestInit
  readonly canRetryUnauthorized: boolean
} {
  const { bodyFactory: _ignored, transfer: _transfer, timeout: _timeout, ...requestInitWithoutFactory } = init ?? {}
  return {
    requestInit: {
      ...requestInitWithoutFactory,
      headers,
      signal,
    },
    canRetryUnauthorized: !isReadableStreamBody(requestInitWithoutFactory.body),
  }
}

function injectTraceHeaders(bundle: OtelBundle, span: Span, headers: Record<string, string>): void {
  const spanContext = span.spanContext()
  const contextWithSpan = bundle.api.trace.setSpan(bundle.api.context.active(), span)
  bundle.api.propagation.inject(contextWithSpan, headers)
  if (headers.traceparent !== undefined) return
  const traceFlags = spanContext.traceFlags.toString(16).padStart(2, '0')
  headers.traceparent = `00-${spanContext.traceId}-${spanContext.spanId}-${traceFlags}`
}

function spanAttributes(values: Record<string, SpanAttributeValue | undefined>): Record<string, SpanAttributeValue> {
  const attributes: Record<string, SpanAttributeValue> = {}
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) attributes[key] = value
  }
  return attributes
}

function finalizeSpan(bundle: OtelBundle, span: Span, statusCode?: number, error?: unknown): void {
  if (statusCode !== undefined) {
    span.setAttribute('http.response.status_code', statusCode)
  }
  if (error !== undefined) {
    if (error instanceof Error) span.recordException(error)
    span.setStatus({
      code: bundle.api.SpanStatusCode.ERROR,
      message: error instanceof Error ? error.message : String(error),
    })
    span.end()
    return
  }
  // Callers that disable `checkStatus` still got an HTTP failure: the span
  // must say so even though no exception travelled with it.
  if (statusCode !== undefined && statusCode >= 400) {
    span.setStatus({ code: bundle.api.SpanStatusCode.ERROR, message: `HTTP ${statusCode}` })
    span.end()
    return
  }
  span.setStatus({ code: bundle.api.SpanStatusCode.OK })
  span.end()
}

type NodeAgentModules = {
  readonly http: { readonly Agent: NodeAgentCtor }
  readonly https: { readonly Agent: NodeAgentCtor }
}

let nodeAgentModulesPromise: Promise<NodeAgentModules> | undefined
// Per-size in-flight promise, so concurrent first requests share one pool.
const nodeAgentsCache = new Map<number, Promise<NodeAgents>>()

async function nodeAgentModules(): Promise<NodeAgentModules> {
  // Only reached in Node (callers check `inBrowser()`). Runtime specifiers
  // keep bundlers from emitting chunks for `node:http`/`node:https` when the
  // SDK is bundled from source into a browser worker.
  nodeAgentModulesPromise ??= (async () => {
    const [httpSpecifier, httpsSpecifier] = ['node:http', 'node:https']
    const [http, https]: [unknown, unknown] = await Promise.all([
      import(/* @vite-ignore */ /* webpackIgnore: true */ httpSpecifier),
      import(/* @vite-ignore */ /* webpackIgnore: true */ httpsSpecifier),
    ])
    return { http: http as NodeAgentModules['http'], https: https as NodeAgentModules['https'] }
  })()
  return nodeAgentModulesPromise
}

function nodeAgentsFor(maxSockets: number): Promise<NodeAgents> {
  const existing = nodeAgentsCache.get(maxSockets)
  if (existing) return existing
  const created = nodeAgentModules().then(({ http, https }): NodeAgents => ({
    httpAgent: new http.Agent({ keepAlive: true, maxSockets }),
    httpsAgent: new https.Agent({ keepAlive: true, maxSockets }),
  }))
  nodeAgentsCache.set(maxSockets, created)
  created.catch(() => nodeAgentsCache.delete(maxSockets))
  return created
}

function requestMeta(config: AxiosRequestConfig, options: HttpClientOptions): DaytonaRequestMeta {
  const existing = (config as DaytonaAxiosRequestConfig).daytona ?? {}
  // Base URLs are opaque destinations and may contain identifiers. Only the
  // relative contract operation belongs in telemetry route templates.
  const path = typeof config.url === 'string' ? config.url : undefined
  return {
    ...existing,
    routeTemplate: requestRouteTemplate(path, existing.routeTemplate),
    sandboxId: sandboxIdFromPath(path, existing.sandboxId ?? options.sandboxId),
    retryOnUnauthorized: existing.retryOnUnauthorized ?? true,
  }
}

async function prepareAxiosRequest(
  config: DaytonaAxiosRequestConfig,
  baseURL: string | undefined,
  instance: AxiosInstance,
  options: HttpClientOptions | undefined,
): Promise<DaytonaAxiosRequestConfig> {
  if (options === undefined) return config
  const meta = requestMeta(config, options)
  config.daytona = { ...meta, signal: config.signal }
  // On the forced-refresh retry the header already carries the fresh token
  // (set by finishAxiosError); resolving again here could hand back the token
  // the provider has not yet rotated.
  if (options.authProvider && !options.auth && meta.retriedAfterUnauthorized !== true) {
    // Inside the request's deadline: its signal aborts the exchange when the
    // budget runs out (and relays the caller's own abort).
    const budget = remainingBudgetMs(config, instance)
    if (budget < 0) throw requestBudgetTimeout()
    setAxiosHeader(config, 'Authorization', await authorizationHeaderForAxiosRequest(options.authProvider, { signal: config.signal }, budget))
  }
  if (typeof options.connectionPoolMaxsize === 'number' && !inBrowser()) {
    const agents = await nodeAgentsFor(options.connectionPoolMaxsize)
    config.httpAgent ??= agents.httpAgent
    config.httpsAgent ??= agents.httpsAgent
  }
  if (!options.otelEnabled) return config
  const bundle = await loadOtelBundle(options.otelApi)
  const span = bundle.tracer.startSpan(`daytona.${(config.method ?? 'get').toLowerCase()} ${meta.routeTemplate}`, {
    kind: bundle.api.SpanKind.CLIENT,
    attributes: spanAttributes({
      'http.request.method': (config.method ?? 'get').toUpperCase(),
      'url.template': meta.routeTemplate,
      'server.address': serverAddress(baseURL),
      'daytona.sandbox_id': meta.sandboxId,
    }),
  })
  const carrier = daytonaHeadersObject(undefined)
  injectTraceHeaders(bundle, span, carrier)
  for (const [name, value] of Object.entries(carrier)) {
    setAxiosHeader(config, name, value)
  }
  config.daytona = { ...meta, span }
  return config
}

function finishAxiosSuccess(response: { readonly status: number; readonly config: AxiosRequestConfig }, options: HttpClientOptions | undefined): void {
  if (!options?.otelEnabled) return
  const span = (response.config as DaytonaAxiosRequestConfig).daytona?.span
  if (!span) return
  loadOtelBundle(options.otelApi).then((bundle) => finalizeSpan(bundle, span, response.status)).catch(() => {
    span.end()
  })
}

async function finishAxiosError(
  instance: AxiosInstance,
  error: unknown,
  options: HttpClientOptions | undefined,
): Promise<unknown> {
  const config = error instanceof AxiosError ? error.config as DaytonaAxiosRequestConfig | undefined : undefined
  const meta = config?.daytona
  // The request's signal carries the deadline's TimeoutError; the caller's
  // own signal (behind it) is what a token refresh must stay abortable by.
  const signal = config?.signal ?? meta?.signal
  const callerSignal = callerSignalOf(signal)
  if (options?.otelEnabled && meta?.span) {
    try {
      const bundle = await loadOtelBundle(options.otelApi)
      finalizeSpan(bundle, meta.span, error instanceof AxiosError ? error.response?.status : error instanceof DaytonaError ? error.statusCode : undefined, error)
    } catch {
      meta.span.end()
    }
  }
  if (
    options?.authProvider &&
    !options?.auth &&
    config &&
    meta?.retryOnUnauthorized !== false &&
    meta?.retriedAfterUnauthorized !== true &&
    hasStatusCode(error, 401)
  ) {
    const retryMeta = meta ?? requestMeta(config, options)
    const rejectedAuthorization = getAxiosHeader(config, 'Authorization')
    // The refresh and the retry spend what is left of the original deadline.
    const refreshBudget = remainingBudgetMs(config, instance)
    if (refreshBudget < 0) throw requestBudgetTimeout()
    const refreshedAuthorization = await authorizationHeaderForAxiosRequest(
      options.authProvider,
      { forceRefresh: true, rejectedAuthorization, signal: callerSignal },
      refreshBudget,
    )
    // A one-shot body (stream) was consumed by the first dispatch; replaying it
    // would send an empty or partial body. The token is rotated for the next
    // call and the 401 surfaces, like the fetch path without a body factory.
    if (!isOneShotAxiosBody(config.data)) {
      config.daytona = { ...retryMeta, retriedAfterUnauthorized: true, span: undefined }
      setAxiosHeader(config, 'Authorization', refreshedAuthorization)
      return instance.request(config)
    }
  }
  // The request's own deadline expired: the same error axios' own timeout
  // gives (and the Python SDK's transport timeout), with no status or code.
  const reason: unknown = signal?.aborted ? (signal as AbortSignalLike).reason : undefined
  if (typeof reason === 'object' && reason !== null && deadlineReasons.has(reason)) throw requestBudgetTimeout()
  const timeoutError = timeoutErrorFromAbortSignal(signal, error)
  if (timeoutError) throw timeoutError
  const abortError = abortErrorFromSignal(signal)
  if (abortError) throw abortError
  if (isAbortError(error)) throw error
  if (error instanceof DaytonaError) throw error
  if (error instanceof AxiosError) {
    const mapped = createAxiosDaytonaError(error)
    // axios' own timeout is the request's budget too.
    if (mapped instanceof ClientTimeoutError) requestBudgetTimeouts.add(mapped)
    throw mapped
  }
  throw createDaytonaError(error instanceof Error ? error.message : String(error), undefined, undefined, 'SDK')
}

/**
 * The token and its lifetime from a token-exchange body, or the
 * `BadRequestError` naming what is wrong (the Python SDK's messages for the
 * same body). A non-positive lifetime is a broken exchange, not a token to cache.
 */
function tokenExchange(body: string): { readonly accessToken: string; readonly expiresIn: number } | BadRequestError {
  let payload: unknown
  try {
    payload = JSON.parse(body)
  } catch {
    payload = undefined
  }
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return badRequest('token exchange response is not a JSON object')
  const { access_token: accessToken, expires_in: expiresIn } = payload as TokenExchangeResponse
  if (typeof accessToken !== 'string' || accessToken === '') return badRequest('token exchange response access_token must be a non-empty string')
  if (typeof expiresIn !== 'number' || !Number.isSafeInteger(expiresIn) || expiresIn <= 0) return badRequest('token exchange response expires_in must be a positive integer')
  return { accessToken, expiresIn }
}

class PlatformTokenProvider implements HttpAuthProvider {
  private cachedToken?: PlatformToken
  private inFlight?: Promise<PlatformToken>
  private readonly baseURL: string
  private readonly jwtToken: string
  private readonly projectId: string
  private readonly requestTimeout?: number
  private readonly otelEnabled?: boolean
  private readonly otelApi?: OtelApiLike

  constructor(options: PlatformTokenProviderOptions) {
    this.baseURL = options.baseURL
    this.jwtToken = options.jwtToken
    this.projectId = options.projectId
    this.requestTimeout = options.requestTimeout
    this.otelEnabled = options.otelEnabled
    this.otelApi = options.otelApi
    credentialSafeDiagnostics(this, () => ({ type: 'PlatformTokenProvider', projectId: this.projectId }))
  }

  public async authorizationHeader(options: {
    readonly forceRefresh?: boolean
    readonly rejectedAuthorization?: string
    readonly signal?: AbortSignal
  } = {}): Promise<string> {
    const token = await abortableWait(this.platformToken(options), options.signal)
    return `Bearer ${token.accessToken}`
  }

  private async platformToken(options: {
    readonly forceRefresh?: boolean
    readonly rejectedAuthorization?: string
  }): Promise<PlatformToken> {
    const forceRefresh = options.forceRefresh === true
    if (!forceRefresh && this.cachedToken && !this.shouldRefresh(this.cachedToken)) {
      return this.cachedToken
    }
    // A 401 for a token an earlier refresh already replaced: the fresh cached
    // token is the answer, no second exchange (mirrors the in-flight branch).
    if (
      forceRefresh
      && this.cachedToken
      && !this.shouldRefresh(this.cachedToken)
      && options.rejectedAuthorization !== undefined
      && `Bearer ${this.cachedToken.accessToken}` !== options.rejectedAuthorization
    ) {
      return this.cachedToken
    }
    if (this.inFlight) {
      const current = await this.inFlight
      if (!forceRefresh) return current
      if (options.rejectedAuthorization === undefined || `Bearer ${current.accessToken}` !== options.rejectedAuthorization) {
        return current
      }
    }
    const exchange = this.exchangeToken()
    this.inFlight = exchange
    try {
      const token = await exchange
      this.cachedToken = token
      return token
    } finally {
      if (this.inFlight === exchange) this.inFlight = undefined
    }
  }

  private shouldRefresh(token: PlatformToken): boolean {
    return nowMs() + token.refreshWindowMs >= token.expiresAtMs
  }

  private async exchangeToken(): Promise<PlatformToken> {
    const timeout = timeoutSignal(this.requestTimeout ?? DEFAULT_REQUEST_TIMEOUT_MS, undefined)
    const headers = {
      ...authHeaders(this.jwtToken),
      'Content-Type': 'application/json',
    }
    const url = `${this.baseURL.replace(/\/$/, '')}/auth/token`
    let bundle: OtelBundle | undefined
    let span: Span | undefined
    if (this.otelEnabled) {
      bundle = await loadOtelBundle(this.otelApi)
      span = bundle.tracer.startSpan('daytona.post /auth/token', {
        kind: bundle.api.SpanKind.CLIENT,
        attributes: spanAttributes({
          'http.request.method': 'POST',
          'url.template': '/auth/token',
          'server.address': serverAddress(this.baseURL),
        }),
      })
      injectTraceHeaders(bundle, span, headers)
    }
    let spanFinalized = false
    const finalize = (statusCode?: number, error?: unknown): void => {
      if (!bundle || !span || spanFinalized) return
      finalizeSpan(bundle, span, statusCode, error)
      spanFinalized = true
    }
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          grant_type: TOKEN_EXCHANGE_GRANT_TYPE,
          subject_token: this.jwtToken,
          subject_token_type: TOKEN_EXCHANGE_SUBJECT_TOKEN_TYPE,
          project_id: this.projectId,
        }),
        signal: timeout.signal,
      })
      if (!response.ok) {
        const error = await errorFromFetchResponse(response)
        finalize(response.status, error)
        throw error
      }
      const exchange = tokenExchange(await response.text())
      if (exchange instanceof BadRequestError) {
        finalize(response.status, exchange)
        throw exchange
      }
      const { accessToken, expiresIn } = exchange
      finalize(response.status)
      return credentialSafeDiagnostics({
        accessToken,
        expiresAtMs: nowMs() + (expiresIn * 1000),
        // Short token lifetimes (<= 30 s) would otherwise re-exchange on every request.
        refreshWindowMs: Math.min(TOKEN_REFRESH_WINDOW_MS, (expiresIn * 1000) / 2),
      }, () => ({ type: 'PlatformToken' }))
    } catch (error) {
      if (error instanceof DaytonaError) {
        finalize(error.statusCode, error)
        throw error
      }
      if (isAbortError(error) && timeout.timedOut()) {
        const timeoutError = new ClientTimeoutError()
        finalize(timeoutError.statusCode, timeoutError)
        throw timeoutError
      }
      if (timeout.signal.aborted) {
        const reason = timeout.signal.reason instanceof Error ? timeout.signal.reason : error
        finalize(undefined, reason)
        throw reason
      }
      const connectionError = new ConnectionError(error instanceof Error ? error.message : String(error))
      finalize(connectionError.statusCode, connectionError)
      throw connectionError
    } finally {
      timeout.clearTimer()
      timeout.detach()
    }
  }
}

export function createPlatformTokenProvider(options: PlatformTokenProviderOptions): HttpAuthProvider {
  return new PlatformTokenProvider(options)
}

function requestHeadersObject(headers: HeadersInit | undefined): Record<string, string> {
  const normalized = new Headers(headers)
  const out: Record<string, string> = {}
  normalized.forEach((value, key) => { out[key] = value })
  return out
}

/** `Timeout.unref()` exists in Node only; browsers return a number. */
function unrefTimer(timer: ReturnType<typeof setTimeout>): void {
  if (typeof timer === 'object' && timer !== null && 'unref' in timer && typeof timer.unref === 'function') {
    timer.unref()
  }
}

/**
 * A per-call timeout in milliseconds, held to the request-timeout rule:
 * `undefined` keeps the request timeout, a negative or non-finite value is a
 * `BadRequestError`, and `0` or above the timer maximum means no limit (the
 * timer helpers read both as unbounded).
 */
function perCallTimeoutMs(timeout: number | undefined): number | undefined {
  if (timeout === undefined) return undefined
  if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0) {
    throw badRequest('timeout must be a non-negative finite number of milliseconds (0 = no limit)')
  }
  return timeout
}

function timeoutSignal(timeoutMs: number, signal: AbortSignalLike | null | undefined): {
  readonly signal: AbortSignal
  readonly clearTimer: () => void
  readonly timedOut: () => boolean
  readonly detach: () => void
} {
  const controller = new AbortController()
  let didTimeout = false
  const relayAbort = (): void => {
    controller.abort(signal?.reason)
  }
  if (signal?.aborted) {
    relayAbort()
  } else {
    signal?.addEventListener?.('abort', relayAbort, { once: true })
  }
  const delay = boundedTimerDelay(timeoutMs)
  const timer = delay === 0
    ? undefined
    : setTimeout(() => {
      didTimeout = true
      controller.abort(new DOMException('operation timed out', 'AbortError'))
    }, delay)
  // A pending deadline must not keep the Node event loop alive on its own
  // (a 24 h default would pin every worker that still has an exchange in
  // flight); the request promise itself keeps the process running.
  if (timer !== undefined) unrefTimer(timer)
  return {
    signal: controller.signal,
    clearTimer: () => {
      if (timer !== undefined) clearTimeout(timer)
    },
    // Detach the caller-signal relay. A long-lived caller signal would
    // otherwise retain one controller per completed request through the
    // still-registered listener. Must NOT run when headers arrive — a
    // streaming body has to stay abortable — only once the request is over.
    detach: () => {
      signal?.removeEventListener?.('abort', relayAbort)
    },
    timedOut: () => didTimeout,
  }
}

function assertRelativeRequestPath(path: string): string {
  if (ABSOLUTE_REQUEST_URL.test(path)) {
    throw createDaytonaError(RELATIVE_PATH_ERROR, 400, 'BAD_REQUEST', 'SDK')
  }
  return path
}

export function createHttpClient(baseURL: string, apiKey?: string, requestTimeout?: number, options?: HttpClientOptions): AxiosInstance {
  return createConfiguredHttpClient(baseURL, apiKey, requestTimeout, options)
}

let fetchCacheModeOverride: { readonly cache: 'no-store' } | null | undefined

/**
 * The `fetchOptions` axios' fetch adapter needs in this runtime, if any.
 *
 * axios 1.20 sends `cache: 'default'` on every fetch, which the workerd
 * runtime rejects ("Unsupported cache mode: default") while accepting
 * `no-store`. Probed once: only a runtime whose `Request` refuses `default` but
 * takes `no-store` gets the override, so Node, Deno, Bun and browsers send
 * requests unchanged.
 */
export function fetchCacheModeOptions(): { readonly cache: 'no-store' } | undefined {
  if (fetchCacheModeOverride === undefined) {
    const accepts = (cache: RequestCache): boolean => {
      try {
        new Request('http://localhost/', { cache })
        return true
      } catch {
        return false
      }
    }
    fetchCacheModeOverride = !accepts('default') && accepts('no-store') ? { cache: 'no-store' } : null
  }
  return fetchCacheModeOverride ?? undefined
}

/** Test seam: probe the runtime's `Request` again on the next client. */
export function resetFetchCacheModeProbeForTests(): void {
  fetchCacheModeOverride = undefined
}

function createConfiguredHttpClient(baseURL: string, apiKey?: string, requestTimeout?: number, options?: HttpClientOptions): AxiosInstance {
  const instance = axios.create({
    baseURL: baseURL.replace(/\/$/, ''),
    timeout: boundedTimerDelay(requestTimeout ?? DEFAULT_REQUEST_TIMEOUT_MS),
    headers: authHeaders(apiKey),
    fetchOptions: fetchCacheModeOptions(),
  })
  credentialSafeDiagnostics(instance, () => ({ type: 'HttpClient' }))
  credentialSafeDiagnostics(instance.defaults, () => ({ timeout: instance.defaults.timeout }))
  credentialSafeDiagnostics(instance.defaults.headers, () => ({ type: 'HttpHeaders' }))
  Object.defineProperty(instance, AXIOS_TRANSPORT_CONFIG, {
    value: credentialSafeDiagnostics({ apiKey, requestTimeout, httpOptions: options } satisfies AxiosTransportConfig,
      () => ({ type: 'HttpTransportConfig', requestTimeout })),
    configurable: true,
  })
  instance.interceptors.request.use(async (config) => {
    const armed = armRequestDeadline(config as DaytonaAxiosRequestConfig, instance)
    try {
      return await prepareAxiosRequest(armed, instance.defaults.baseURL, instance, options)
    } catch (error) {
      // A setup failure (the bearer, OTel) never reaches the response
      // interceptors' AxiosError path: release the deadline here.
      disarmRequestDeadline(armed)
      throw error
    }
  })
  instance.interceptors.response.use(
    (response) => {
      disarmRequestDeadline(response.config)
      // A caller `validateStatus` that accepts 401 must not let a stale token
      // slip past the one-retry mint/refresh path: route it through the error
      // branch like the default validator would (`postRaw` relies on this).
      if (response.status === 401 && (options?.auth || options?.authProvider)) {
        // finishAxiosError either resolves with the retried response or
        // rejects with the typed error — both are valid fulfilled-handler results.
        return finishAxiosError(
          instance,
          new AxiosError('Request failed with status code 401', AxiosError.ERR_BAD_REQUEST, response.config, response.request, response),
          options,
        ) as Promise<AxiosResponse>
      }
      finishAxiosSuccess(response, options)
      return response
    },
    (error: unknown) => {
      if (error instanceof AxiosError) disarmRequestDeadline(error.config)
      return finishAxiosError(instance, error, options)
    },
  )
  return instance
}

/** Pending deadline timers, by request config. */
const requestDeadlines = new WeakMap<AxiosRequestConfig, () => void>()
/**
 * The `ClientTimeoutError`s raised because a request's own budget (its
 * `timeout`) ran out, as opposed to a timeout an auth provider raised itself.
 * A caller that passed that budget can tell its deadline caused the error.
 */
const requestBudgetTimeouts = new WeakSet<ClientTimeoutError>()

function requestBudgetTimeout(): ClientTimeoutError {
  const error = new ClientTimeoutError()
  requestBudgetTimeouts.add(error)
  return error
}

/** Whether `error` is the timeout of a request's own budget (internal). */
export function isRequestBudgetTimeout(error: unknown): boolean {
  return error instanceof ClientTimeoutError && requestBudgetTimeouts.has(error)
}

/** The abort reasons of expired request deadlines. */
const deadlineReasons = new WeakSet<object>()
/** The caller's own signal behind each deadline signal (a 401 retry re-arms from it). */
const callerSignals = new WeakMap<AbortSignal, AbortSignalLike | undefined>()

/**
 * Bound the whole request (headers and body) by its timeout: the config's own
 * `timeout`, else the instance default; `0` = no bound. axios' `timeout` only
 * bounds the wait for the response headers and the idle time between reads in
 * Node, so the SDK adds the wall-clock bound itself. The abort carries a
 * `TimeoutError`, which `finishAxiosError` maps to `ClientTimeoutError`.
 */
function armRequestDeadline<T extends DaytonaAxiosRequestConfig>(config: T, instance: AxiosInstance): T {
  disarmRequestDeadline(config)
  // axios hands its timeout to a platform timer too: one above the timer
  // maximum would fire at once, so it means no bound there as well.
  if (typeof config.timeout === 'number') config.timeout = boundedTimerDelay(config.timeout)
  const timeoutMs = boundedTimerDelay(timeoutFromAxiosRequest(config, instance))
  // A retry (or a caller that already spent part of the budget) keeps the
  // original deadline instead of starting a new one.
  const deadlineAt = config.daytona?.deadlineAt ?? (timeoutMs === 0 ? undefined : nowMs() + timeoutMs)
  if (deadlineAt === undefined) return config
  config.daytona = { ...config.daytona, deadlineAt }
  const delay = Math.max(0, Math.ceil(deadlineAt - nowMs()))
  const current = config.signal as AbortSignal | undefined
  const callerSignal: AbortSignalLike | undefined = current !== undefined && callerSignals.has(current) ? callerSignals.get(current) : current
  const controller = new AbortController()
  callerSignals.set(controller.signal, callerSignal)
  const relay = (): void => controller.abort(callerSignal?.reason)
  if (callerSignal?.aborted) relay()
  else callerSignal?.addEventListener?.('abort', relay, { once: true })
  const expire = (): void => {
    const reason = new DOMException('request timed out', 'TimeoutError')
    deadlineReasons.add(reason)
    controller.abort(reason)
  }
  // A retry whose deadline already passed is aborted before it is sent.
  if (delay === 0) expire()
  const timer = delay === 0 ? undefined : setTimeout(expire, delay)
  if (timer !== undefined) unrefTimer(timer)
  requestDeadlines.set(config, () => {
    if (timer !== undefined) clearTimeout(timer)
    callerSignal?.removeEventListener?.('abort', relay)
  })
  config.signal = controller.signal
  return config
}

/**
 * What is left of a request's budget, in ms: its deadline's remainder (below
 * zero once it has passed), else its configured timeout (`0` = unbounded).
 */
function remainingBudgetMs(config: DaytonaAxiosRequestConfig, instance: AxiosInstance): number {
  const deadlineAt = config.daytona?.deadlineAt
  if (deadlineAt === undefined) return boundedTimerDelay(timeoutFromAxiosRequest(config, instance))
  const remaining = Math.ceil(deadlineAt - nowMs())
  return remaining > 0 ? remaining : -1
}

/** The caller's own signal behind a request's deadline signal (or the signal itself). */
function callerSignalOf(signal: AbortSignalLike | GenericAbortSignal | undefined): AbortSignalLike | undefined {
  const own = signal as AbortSignal | undefined
  return own !== undefined && callerSignals.has(own) ? callerSignals.get(own) : signal as AbortSignalLike | undefined
}

function disarmRequestDeadline(config: AxiosRequestConfig | undefined): void {
  if (config === undefined) return
  requestDeadlines.get(config)?.()
  requestDeadlines.delete(config)
}

/**
 * HTTP timeout (ms) for one request drawn from an absolute caller `deadline`.
 *
 * Returns the POSITIVE remainder, capped by a positive configured per-request
 * timeout; `0` when there is no deadline (the caller asked for unbounded —
 * axios reads `0` as "no timeout"); and `undefined` when the deadline has
 * already passed. Callers must check that last case and raise: a spent budget
 * is never turned into a number the transport could read as "no timeout" —
 * `0` is reserved for the caller's own unbounded spelling and is never
 * produced by arithmetic.
 */
export function boundedRequestTimeout(deadline: number | undefined, configuredTimeout: unknown): number | undefined {
  if (deadline === undefined) return 0
  const remaining = deadline - Date.now()
  if (remaining <= 0) return undefined
  const budget = Math.ceil(remaining)
  return typeof configuredTimeout === 'number' && Number.isFinite(configuredTimeout) && configuredTimeout > 0
    ? Math.min(budget, configuredTimeout)
    : budget
}

/**
 * Per-request bearer resolution for a sandbox transport.
 *
 * Attached by a sandbox handle to its toolbox transport:
 * `bearer()` resolves the token to send (minting or
 * refreshing a short-lived sandbox access token when needed), and
 * `onUnauthorized(failedToken)` is called at most once per request after a
 * 401 — returning a fresh token retries the request exactly once, returning
 * `undefined` surfaces the original error. Transports without a provider keep
 * their static token.
 */
export interface TransportAuth {
  bearer(): Promise<string | undefined>
  onUnauthorized(failedToken: string | undefined): Promise<string | undefined>
}

/**
 * `promise`, or `ClientTimeoutError` once the absolute deadline (`nowMs()`
 * clock) passes. The work itself is not cancelled; a missing deadline waits.
 */
async function withinDeadline<T>(promise: Promise<T>, deadlineAt: number | undefined): Promise<T> {
  if (deadlineAt === undefined) return promise
  const remaining = deadlineAt - nowMs()
  if (remaining <= 0) {
    promise.catch(() => undefined)
    throw new ClientTimeoutError()
  }
  let timer: ReturnType<typeof setTimeout> | undefined
  const expired = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new ClientTimeoutError()), Math.ceil(remaining))
    unrefTimer(timer)
  })
  try {
    return await Promise.race([promise, expired])
  } finally {
    clearTimeout(timer)
  }
}

/** Map an abort caused by the request's own deadline to `ClientTimeoutError`. */
async function timeoutOnAbort<T>(promise: Promise<T>, timeout: { readonly timedOut: () => boolean }): Promise<T> {
  try {
    return await promise
  } catch (error) {
    if (isAbortError(error) && timeout.timedOut()) throw new ClientTimeoutError()
    throw error
  }
}

/** Merge a resolved bearer into a request config (overriding the instance default). */
function withBearer(config: AxiosRequestConfig | undefined, token: string | undefined): AxiosRequestConfig | undefined {
  if (token === undefined) return config
  return { ...config, headers: { ...(config?.headers as Record<string, unknown> | undefined), Authorization: `Bearer ${token}` } }
}

export class HttpTransport {
  public readonly http: AxiosInstance
  public readonly baseUrl: string
  private readonly apiKey?: string
  private readonly requestTimeout?: number
  private readonly options?: HttpClientOptions

  constructor(baseUrl: string, apiKey?: string, requestTimeout?: number, options?: HttpClientOptions) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.apiKey = apiKey
    this.requestTimeout = requestTimeout
    this.options = options
    this.http = createConfiguredHttpClient(this.baseUrl, apiKey, requestTimeout, options)
    credentialSafeDiagnostics(this, () => ({ type: 'HttpTransport' }))
  }

  public get token(): string | undefined {
    return this.apiKey
  }

  public async resolveToken(): Promise<string | undefined> {
    if (this.options?.auth) return this.options.auth.bearer()
    if (this.options?.authProvider) {
      return bearerCredentialFromAuthorizationHeader(await this.options.authProvider.authorizationHeader())
    }
    return this.apiKey
  }

  public withBaseUrl(baseUrl: string): HttpTransport {
    return new HttpTransport(baseUrl, this.apiKey, this.requestTimeout, this.options)
  }

  public async bearerToken(options: AuthorizationHeaderOptions = {}): Promise<string | undefined> {
    if (this.options?.auth) return this.options.auth.bearer()
    if (this.options?.authProvider) {
      return bearerCredentialFromAuthorizationHeader(await this.options.authProvider.authorizationHeader(options))
    }
    return this.apiKey
  }

  public get defaultTimeout(): number {
    return this.requestTimeout ?? DEFAULT_REQUEST_TIMEOUT_MS
  }

  /**
   * Run one request with the provider-resolved bearer, retrying exactly once
   * on a 401 the provider can answer with a fresh token. Without a provider
   * the request runs untouched on the instance's static credential.
   */
  private async withAuth<T>(config: AxiosRequestConfig | undefined, run: (config?: AxiosRequestConfig) => Promise<T>): Promise<T> {
    const auth = this.options?.auth
    if (!auth) return run(config)
    // One deadline for the whole call: the bearer (a token mint), the request,
    // a 401 refresh and its retry. The request interceptor keeps `deadlineAt`.
    const budget = boundedTimerDelay(typeof config?.timeout === 'number' ? config.timeout : this.defaultTimeout)
    const deadlineAt = budget === 0 ? undefined : nowMs() + budget
    const bounded = (requestConfig: AxiosRequestConfig | undefined): AxiosRequestConfig | undefined =>
      deadlineAt === undefined
        ? requestConfig
        // Keep the caller's request meta (route template, sandbox id, retry flag).
        : { ...requestConfig, daytona: { ...(requestConfig as DaytonaAxiosRequestConfig | undefined)?.daytona, deadlineAt } } as DaytonaAxiosRequestConfig
    const token = await withinDeadline(auth.bearer(), deadlineAt)
    try {
      return await run(bounded(withBearer(config, token)))
    } catch (error) {
      if (!(error instanceof DaytonaError) || error.statusCode !== 401) throw error
      const fresh = await withinDeadline(auth.onUnauthorized(token), deadlineAt)
      if (fresh === undefined || fresh === token) throw error
      return run(bounded(withBearer(config, fresh)))
    }
  }

  public async get<T>(path: string, config?: AxiosRequestConfig): Promise<T> {
    return this.withAuth(config, async (requestConfig) => {
      const { data } = await this.http.get<T>(assertRelativeRequestPath(path), requestConfig)
      return data
    })
  }

  /** A byte download (a file transfer): not bounded by the request timeout. */
  public async getBytes(path: string): Promise<ArrayBuffer> {
    return this.withAuth({ responseType: 'arraybuffer', timeout: 0 }, async (requestConfig) => {
      const { data } = await this.http.get<ArrayBuffer>(assertRelativeRequestPath(path), requestConfig)
      return data
    })
  }

  public async post<T>(path: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return this.withAuth(config, async (requestConfig) => {
      const { data } = await this.http.post<T>(assertRelativeRequestPath(path), body, requestConfig)
      return data
    })
  }

  public async patch<T>(path: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return this.withAuth(config, async (requestConfig) => {
      const { data } = await this.http.patch<T>(assertRelativeRequestPath(path), body, requestConfig)
      return data
    })
  }

  /**
   * Like post, but returns the FULL response and accepts non-2xx statuses
   * (the caller interprets them — bulk endpoints carry errors in the body) —
   * EXCEPT a 401, which still throws into the one-retry mint path: a caller
   * that swallows every status with `validateStatus: () => true` would
   * otherwise ride a stale token straight past the refresh machinery.
   */
  public async postRaw<T>(path: string, body?: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>> {
    return this.withAuth(config, async (requestConfig) =>
      this.http.post<T>(assertRelativeRequestPath(path), body, {
        ...requestConfig,
        validateStatus: (status) => status !== 401,
      }),
    )
  }

  public async delete<T>(path: string, config?: AxiosRequestConfig): Promise<T> {
    return this.withAuth(config, async (requestConfig) => {
      const { data } = await this.http.delete<T>(assertRelativeRequestPath(path), requestConfig)
      return data
    })
  }

  public async fetch(path: string, init?: FetchRequestInit, checkStatus = true): Promise<Response> {
    const relativePath = assertRelativeRequestPath(path)
    const meta = requestMeta({ url: relativePath }, this.options ?? {})
    // Until the response headers arrive (a stream's body is not bounded), by
    // the call's own timeout or else the request timeout; never for a file
    // transfer, whose upload body is sent before them.
    const timeout = timeoutSignal(init?.transfer === true ? 0 : perCallTimeoutMs(init?.timeout) ?? this.defaultTimeout, init?.signal)
    // A successful response with a streaming body keeps its span open until
    // the body is fully read (or errors/cancels): a read failure after the
    // headers is still a failed request from the caller's point of view.
    type Sent = { readonly response: Response; readonly settle: (error?: unknown) => void }
    // One 401 retry per request, whichever credential source answers it: with
    // both `auth` and `authProvider` configured the two branches below would
    // otherwise re-arm each other on a persistent 401.
    let retriedUnauthorized = false
    const send = async (forceRefresh: boolean, rejectedAuthorization?: string, retryToken?: string): Promise<Sent> => {
      const headers: Record<string, string> = {
        ...authHeaders(this.apiKey),
        ...requestHeadersObject(init?.headers),
      }
      // Auth precedence: retryToken (from auth.onUnauthorized) > auth.bearer > authProvider > apiKey
      let currentAuthToken: string | undefined
      let sdkAuthorization: string | undefined
      if (retryToken !== undefined) {
        currentAuthToken = retryToken
        sdkAuthorization = `Bearer ${retryToken}`
      } else if (this.options?.auth) {
        currentAuthToken = await timeoutOnAbort(abortableWait(this.options.auth.bearer(), timeout.signal), timeout)
        if (currentAuthToken !== undefined) sdkAuthorization = `Bearer ${currentAuthToken}`
      } else if (this.options?.authProvider) {
        try {
          sdkAuthorization = await this.options.authProvider.authorizationHeader({ forceRefresh, rejectedAuthorization, signal: timeout.signal })
        } catch (error) {
          // The request deadline can fire while the token exchange is still
          // pending; that is the same timeout `send` maps below, not a raw abort.
          if (isAbortError(error) && timeout.timedOut()) {
            throw new ClientTimeoutError()
          }
          throw error
        }
      }
      // Exactly one credential goes on the wire: when the SDK has one, a
      // caller-supplied `authorization` (any casing) is dropped so fetch's
      // Headers normalisation cannot comma-join the two; when it has none,
      // the caller's header is kept rather than sending no credential.
      if (sdkAuthorization !== undefined) {
        for (const key of Object.keys(headers)) {
          if (key.toLowerCase() === 'authorization') delete headers[key]
        }
        headers.Authorization = sdkAuthorization
      }
      let bundle: OtelBundle | undefined
      let span: Span | undefined
      if (this.options?.otelEnabled) {
        bundle = await loadOtelBundle(this.options.otelApi)
        span = bundle.tracer.startSpan(`daytona.${(init?.method ?? 'GET').toLowerCase()} ${meta.routeTemplate}`, {
          kind: bundle.api.SpanKind.CLIENT,
          attributes: spanAttributes({
            'http.request.method': (init?.method ?? 'GET').toUpperCase(),
            'url.template': meta.routeTemplate,
            'server.address': serverAddress(this.baseUrl),
            'daytona.sandbox_id': meta.sandboxId,
          }),
        })
        injectTraceHeaders(bundle, span, headers)
      }
      let spanFinalized = false
      const finalize = (statusCode?: number, error?: unknown): void => {
        if (!bundle || !span || spanFinalized) return
        finalizeSpan(bundle, span, statusCode, error)
        spanFinalized = true
      }
      try {
        const request = init?.bodyFactory
          ? await fetchRequestForAttempt(init, headers, timeout.signal)
          : fetchRequestWithoutFactory(init, headers, timeout.signal)
        const response = await fetch(`${this.baseUrl}${relativePath}`, request.requestInit)
        if (response.status === 401 && this.options?.auth && retryToken === undefined && !retriedUnauthorized && request.canRetryUnauthorized) {
          let freshToken: string | undefined
          try {
            freshToken = await timeoutOnAbort(abortableWait(this.options.auth.onUnauthorized(currentAuthToken), timeout.signal), timeout)
          } catch (err) {
            await response.body?.cancel().catch(() => undefined)
            finalize(response.status, err instanceof Error ? err : new AuthenticationError('onUnauthorized raised an error', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' }))
            throw err instanceof Error ? err : new AuthenticationError('onUnauthorized raised an error', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
          }
          if (freshToken !== undefined && freshToken !== currentAuthToken) {
            await response.body?.cancel().catch(() => undefined)
            finalize(response.status, new AuthenticationError('request rejected before token refresh retry', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' }))
            retriedUnauthorized = true
            return send(false, undefined, freshToken)
          }
        } else if (response.status === 401 && this.options?.authProvider && forceRefresh === false && !retriedUnauthorized) {
          const rejectedAuthorization = headers.Authorization
          if (request.canRetryUnauthorized) {
            await response.body?.cancel().catch(() => undefined)
            finalize(response.status, new AuthenticationError('request rejected before token refresh retry', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' }))
            retriedUnauthorized = true
            return send(true, rejectedAuthorization)
          }
          await this.options.authProvider.authorizationHeader({ forceRefresh: true, rejectedAuthorization, signal: timeout.signal })
        }
        if (checkStatus && !response.ok) {
          const error = await errorFromFetchResponse(response)
          finalize(response.status, error)
          throw error
        }
        if (response.body === null) {
          finalize(response.status)
          return { response, settle: () => undefined }
        }
        return {
          response,
          settle: (error?: unknown) => {
            if (error === undefined) finalize(response.status)
            else finalize(undefined, error)
          },
        }
      } catch (error) {
        if (error instanceof DaytonaError) {
          finalize(error.statusCode, error)
          throw error
        }
        if (isAbortError(error) && timeout.timedOut()) {
          const timeoutError = new ClientTimeoutError()
          finalize(timeoutError.statusCode, timeoutError)
          throw timeoutError
        }
        if (isAbortError(error)) {
          finalize(undefined, error)
          throw error
        }
        const connectionError = new ConnectionError(error instanceof Error ? error.message : String(error))
        finalize(connectionError.statusCode, connectionError)
        throw connectionError
      }
    }
    let sent: Sent
    try {
      sent = await send(false)
    } catch (error) {
      timeout.clearTimer()
      timeout.detach()
      throw error
    }
    const { response, settle } = sent
    timeout.clearTimer()
    // The body may still be streaming: keep the caller's abort relayed and the
    // span open until the stream closes (or errors), then drop the listener.
    if (response.body === null) {
      timeout.detach()
      settle()
      return response
    }
    const source = response.body.getReader()
    const detachingBody = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await source.read()
          if (done) {
            timeout.detach()
            settle()
            controller.close()
            return
          }
          controller.enqueue(value)
        } catch (error) {
          timeout.detach()
          settle(error)
          controller.error(error)
        }
      },
      async cancel(reason) {
        timeout.detach()
        settle(reason instanceof Error ? reason : new Error('response body cancelled'))
        await source.cancel(reason)
      },
    })
    return new Response(detachingBody, { status: response.status, statusText: response.statusText, headers: response.headers })
  }
}

export function transportFromAxiosInstance(instance: AxiosInstance, config: HttpTransportConfig = {}): HttpTransport {
  const derived = axiosTransportConfig(instance)
  return new HttpTransport(
    instance.defaults.baseURL ?? '',
    config.apiKey ?? derived?.apiKey ?? apiKeyFromAxiosInstance(instance),
    config.requestTimeout ?? derived?.requestTimeout ?? timeoutFromAxiosInstance(instance),
    config.httpOptions ?? derived?.httpOptions,
  )
}

export interface SseMessage {
  event: string
  data: string
  id?: string
}

function normalizeSseNewlines(value: string): string {
  return value.replace(/\r\n|\r/g, '\n')
}

export async function* sseMessages(response: Response): AsyncIterable<SseMessage> {
  const body = response.body
  if (!body) return
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let streamDone = false
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) {
        streamDone = true
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let boundary = findSseBoundary(buffer)
      while (boundary) {
        const raw = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary.length)
        const message = parseSseMessage(raw)
        if (message) yield message
        boundary = findSseBoundary(buffer)
      }
    }
    const tail = buffer.replace(/\r$/, '')
    if (tail.trim()) {
      const message = parseSseMessage(tail)
      if (message) yield message
    }
  } finally {
    if (!streamDone) {
      await reader.cancel()
    }
    reader.releaseLock()
  }
}

function parseSseMessage(raw: string): SseMessage | undefined {
  let event = 'message'
  let id: string | undefined
  const data: string[] = []
  for (const line of normalizeSseNewlines(raw).split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon >= 0 ? line.slice(0, colon) : line
    const value = colon >= 0 ? line.slice(colon + 1).replace(/^ /, '') : ''
    if (field === 'event') event = value
    if (field === 'id') id = value
    if (field === 'data') data.push(value)
  }
  if (!data.length && !id) return undefined
  return { event, id, data: data.join('\n') }
}

function findSseBoundary(buffer: string): { readonly index: number; readonly length: number } | undefined {
  for (let index = 0; index < buffer.length; index += 1) {
    const first = lineTerminatorLength(buffer, index)
    if (!first) continue
    const second = lineTerminatorLength(buffer, index + first)
    if (second) return { index, length: first + second }
    index += first - 1
  }
  return undefined
}

function lineTerminatorLength(buffer: string, index: number): number {
  const char = buffer[index]
  if (char === '\r') return buffer[index + 1] === '\n' ? 2 : 1
  return char === '\n' ? 1 : 0
}

export function sandboxState(status: string | undefined): SandboxState {
  switch (status) {
    case 'queued':
      return 'queued'
    case 'pending':
    case 'assigned':
      return 'creating'
    case 'booting':
      return 'starting'
    case 'running':
      return 'started'
    case 'pausing':
      return 'pausing'
    case 'paused':
      return 'paused'
    case 'stopping':
      return 'stopping'
    case 'stopped':
      return 'stopped'
    case 'deleting':
      return 'destroying'
    case 'deleted':
      return 'destroyed'
    case 'failed':
    case 'lost':
      return 'error'
    default:
      return 'unknown'
  }
}

export function gbToMib(gb: number): number {
  return Math.round(gb * 1024)
}

export function mibToGb(mib: number | undefined): number {
  return Math.max(1, Math.ceil((mib ?? 1024) / 1024))
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// RFC 3986 strict percent-encoding for user-derived path segments: also encodes
// !'()* so every request URL is byte-identical to the Python SDK's
// quote(value, safe='') (parity contract).
export function strictUriEncode(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`)
}

export function encodeQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function dropUndefined(input: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined) out[key] = value
  }
  return out
}

type CursorPagePayload = {
  readonly next_cursor?: unknown
  readonly nextCursor?: unknown
}

export function runtimeUniqueId(prefix?: string): string {
  const randomUuid = globalThis.crypto?.randomUUID?.()
  let suffix: string
  if (typeof randomUuid === 'string') {
    suffix = randomUuid.replace(/-/g, '')
  } else {
    const cryptoObject = globalThis.crypto
    if (cryptoObject?.getRandomValues) {
      const bytes = new Uint8Array(16)
      cryptoObject.getRandomValues(bytes)
      suffix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    } else {
      suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
    }
  }
  return prefix === undefined ? suffix : `${prefix}-${suffix}`
}

export function nextCursorOf<T extends object>(payload: T & CursorPagePayload): string | undefined {
  if (typeof payload.next_cursor === 'string') return payload.next_cursor
  return typeof payload.nextCursor === 'string' ? payload.nextCursor : undefined
}

type LifecycleDurationField = 'timeoutSeconds' | 'idleExpirySeconds'
type LifecycleActionField = 'onTimeout' | 'onIdleTimeout'
type LifecycleField = LifecycleDurationField | LifecycleActionField

const LIFECYCLE_DURATION_FIELDS: readonly LifecycleDurationField[] = ['timeoutSeconds', 'idleExpirySeconds']
const LIFECYCLE_ACTION_FIELDS: readonly LifecycleActionField[] = ['onTimeout', 'onIdleTimeout']
const LIFECYCLE_FIELDS: readonly LifecycleField[] = ['timeoutSeconds', 'onTimeout', 'idleExpirySeconds', 'onIdleTimeout']
const LIFECYCLE_ACTIONS: readonly LifecycleAction[] = ['stop', 'pause', 'delete']
const LIFECYCLE_WIRE_NAMES: Record<LifecycleField, string> = {
  timeoutSeconds: 'timeout_seconds',
  onTimeout: 'on_timeout',
  idleExpirySeconds: 'idle_expiry_seconds',
  onIdleTimeout: 'on_idle_timeout',
}

/**
 * The server rejects durations above 2147483647 seconds. Check this before
 * sending a request; organization-specific timeout limits remain server-side.
 */
export const LIFECYCLE_MAX_SECS = 2147483647

/**
 * Validate a lifecycle policy and render its snake_case wire object, or undefined when empty.
 *
 * At creation, `idleExpirySeconds` and `onIdleTimeout` must be supplied
 * together. Updates may change either value independently when the other is
 * already configured; the server validates the resulting policy.
 */
export function lifecycleBody(
  lifecyclePolicy: LifecyclePolicyOptions | undefined | null,
  options: { creating?: boolean } = {},
): Record<string, number | boolean | string> | undefined {
  // `null` counts as NOT PROVIDED, like `undefined`: it is what a JSON config
  // round-trip produces for an omitted key, and the Python twin's `None` — the
  // same argument in the same position — already means exactly that. Throwing
  // here would make one SDK reject what the other accepts.
  if (lifecyclePolicy === undefined || lifecyclePolicy === null) return undefined
  // `typeof [] === 'object'`, so the array case needs its own clause.
  if (typeof lifecyclePolicy !== 'object' || lifecyclePolicy === null || Array.isArray(lifecyclePolicy)) {
    throw createDaytonaError('lifecyclePolicy must be an object', 400, 'BAD_REQUEST', 'SDK')
  }
  // Reject unknown fields before selecting the supported keys, so a typo
  // cannot silently create a sandbox without the intended policy.
  const unknownFields = Object.keys(lifecyclePolicy).filter((field) => !LIFECYCLE_FIELDS.includes(field as LifecycleField))
  if (unknownFields.length > 0) {
    throw createDaytonaError(`lifecyclePolicy contains unknown fields: ${unknownFields.join(', ')}`, 400, 'BAD_REQUEST', 'SDK')
  }
  const body: Record<string, number | boolean | string> = {}
  for (const field of LIFECYCLE_DURATION_FIELDS) {
    const value = lifecyclePolicy[field]
    if (value === undefined || value === null) continue
    if (typeof value !== 'number' || !Number.isInteger(value)) {
      throw createDaytonaError(`lifecyclePolicy.${field} must be an integer number of seconds`, 400, 'BAD_REQUEST', 'SDK')
    }
    if (field === 'timeoutSeconds' && value < 1) {
      throw createDaytonaError('lifecyclePolicy.timeoutSeconds must be at least 1; the timeout cannot be disabled', 400, 'BAD_REQUEST', 'SDK')
    }
    if (field === 'idleExpirySeconds' && value === 0) {
      throw createDaytonaError(`lifecyclePolicy.idleExpirySeconds: 0 is not a valid budget; use ${DISABLED} to disable`, 400, 'BAD_REQUEST', 'SDK')
    }
    if (value < DISABLED) throw createDaytonaError(`lifecyclePolicy.${field} must be >= ${DISABLED}: use ${DISABLED} to disable`, 400, 'BAD_REQUEST', 'SDK')
    if (value !== DISABLED && value > LIFECYCLE_MAX_SECS) {
      throw createDaytonaError(`lifecyclePolicy.${field} must not exceed ${LIFECYCLE_MAX_SECS} seconds`, 400, 'BAD_REQUEST', 'SDK')
    }
    body[LIFECYCLE_WIRE_NAMES[field]] = value
  }
  for (const field of LIFECYCLE_ACTION_FIELDS) {
    const action: unknown = lifecyclePolicy[field]
    if (action === undefined || action === null) continue
    if (typeof action !== 'string' || !LIFECYCLE_ACTIONS.includes(action as LifecycleAction)) {
      throw createDaytonaError(`lifecyclePolicy.${field} must be one of: ${LIFECYCLE_ACTIONS.join(', ')}`, 400, 'BAD_REQUEST', 'SDK')
    }
    body[LIFECYCLE_WIRE_NAMES[field]] = action
  }
  const idleWire = LIFECYCLE_WIRE_NAMES.idleExpirySeconds
  const idleActionWire = LIFECYCLE_WIRE_NAMES.onIdleTimeout
  // Disabling idle expiry and setting its action in the same request is invalid.
  if (body[idleWire] === DISABLED && idleActionWire in body) {
    throw createDaytonaError(
      `lifecyclePolicy.onIdleTimeout requires lifecyclePolicy.idleExpirySeconds: it cannot be set while clearing it with ${DISABLED}`,
      400,
      'BAD_REQUEST',
      'SDK',
    )
  }
  if (options.creating === true) {
    const armedIdle = idleWire in body && body[idleWire] !== DISABLED
    if (armedIdle && !(idleActionWire in body)) {
      throw createDaytonaError(
        `lifecyclePolicy.idleExpirySeconds requires lifecyclePolicy.onIdleTimeout (${LIFECYCLE_ACTIONS.join(' | ')}); the idle action has no default`,
        400,
        'BAD_REQUEST',
        'SDK',
      )
    }
    if (!armedIdle && idleActionWire in body) {
      throw createDaytonaError('lifecyclePolicy.onIdleTimeout requires lifecyclePolicy.idleExpirySeconds', 400, 'BAD_REQUEST', 'SDK')
    }
  }
  return Object.keys(body).length > 0 ? body : undefined
}

/**
 * Reject lifecycle actions the sandbox class and persistence forbid.
 *
 * `delete` is legal everywhere; `stop` requires a persistent macOS sandbox.
 * Use the explicit pause operation rather than a lifecycle-policy action.
 */
export function assertLifecycleClassSupport(
  lifecyclePolicy: LifecyclePolicyOptions | undefined,
  options: { sandboxClass: SandboxClass; persistent: boolean; action?: string },
): void {
  if (!lifecyclePolicy) return
  const { sandboxClass, persistent, action = 'create' } = options
  const offending: string[] = []
  for (const field of LIFECYCLE_ACTION_FIELDS) {
    const value: unknown = lifecyclePolicy[field]
    if (typeof value === 'string' && !lifecycleActionAllowed(value, sandboxClass, persistent)) offending.push(`lifecyclePolicy.${field}=${value}`)
  }
  if (offending.length === 0) return
  const label = sandboxClass === 'macos' && !persistent ? 'macos ephemeral' : sandboxClass
  throw createDaytonaError(`${label} ${action} does not accept: ${offending.join(', ')}`, 400, 'BAD_REQUEST', 'SDK')
}

/** Whether a lifecycle policy can use this action. */
function lifecycleActionAllowed(action: string, sandboxClass: SandboxClass, persistent: boolean): boolean {
  switch (action) {
    case 'delete':
      return true
    case 'stop':
      return persistent && sandboxClass === 'macos'
    case 'pause':
      return false
    default:
      // Unknown values never reach here — lifecycleBody rejects them.
      return true
  }
}

/** Parse a lifecycle object, accepting both bare and sandbox-nested shapes. */
export function parseSandboxLifecycle(payload: (SandboxLifecycleDto & { lifecycle_policy?: SandboxLifecycleDto }) | undefined): SandboxLifecycle {
  const data = payload?.lifecycle_policy ?? payload ?? {}
  return {
    timeoutSeconds: optionalInteger(data.timeout_seconds),
    onTimeout: optionalText(data.on_timeout),
    timeoutAt: optionalText(data.timeout_at),
    idleExpirySeconds: optionalInteger(data.idle_expiry_seconds),
    onIdleTimeout: optionalText(data.on_idle_timeout),
    idleTimeoutAt: optionalText(data.idle_timeout_at),
    lastActivityAt: optionalText(data.last_activity_at),
    lastActivitySource: optionalText(data.last_activity_source),
  }
}

function optionalInteger(value: number | null | undefined): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) ? value : undefined
}

function optionalText(value: string | null | undefined): string | undefined {
  return typeof value === 'string' ? value : undefined
}
