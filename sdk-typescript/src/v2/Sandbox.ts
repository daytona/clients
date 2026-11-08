/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance, AxiosRequestConfig } from 'axios'
import { credentialSafeDiagnostics, credentialSafeInspection } from '../diagnostics.js'
import { followBuildLogs, readBuildLogs } from './BuildLogs.js'
import type { BuildLogFollowOptions, BuildLogFollowResult } from './BuildLogs.js'
import { FileSystemService } from './FileSystem.js'
import type { SignedFileUrl } from './FileSystem.js'
import { Image, Snapshot, artifactRefOf } from './Image.js'
import { strictUriEncode } from './http.js'
import { GitService } from './Git.js'
import { InterpreterService } from './Interpreter.js'
import { ComputerUseService } from './ComputerUse.js'
import { DockerService } from './Docker.js'
import { LspService } from './Lsp.js'
import { createNetworkPolicyCreateField, parseNetworkPolicyResponse, serializeNetworkPolicyInput } from './NetworkPolicy.js'
import { PortsService } from './Ports.js'
import { SandboxVolumesService } from './SandboxVolumes.js'
import { parseSandboxSecretsResponse, serializeSandboxSecretsInput } from './SandboxSecrets.js'
import { ProcessService, PtyService, WAIT_READ_SLACK_MS } from './Process.js'
import type * as api from './gen/api-types.js'
import type * as toolbox from './gen/toolbox-types.js'
import {
  assertLifecycleClassSupport,
  createHttpClient,
  dropUndefined,
  HttpTransport,
  type HttpClientOptions,
  lifecycleBody,
  mibToGb,
  parseSandboxLifecycle,
  sandboxState,
  sleep,
} from './http.js'
import type { TransportAuth } from './http.js'
import { BadRequestError, DaytonaError, NotFoundError, RequestTimeoutError, SandboxTerminalStateError, UnsupportedOperationError } from './errors/DaytonaError.js'
import { eventDestinationFromHeaders, regionOf, targetPredicate, waitQueryKey } from './events.js'
import type { WaitHub, WaitOutcome } from './events.js'
import { WAIT_FOR_STATE_MIN_INTERVAL_MS } from './waitConstants.js'
import type {
  CreateAccessTokenOptions,
  InterpreterResult,
  InterpreterRunOptions,
  LifecyclePolicyOptions,
  NetworkPolicyInfo,
  NetworkPolicyInput,
  SandboxSecretsInfo,
  RunOptions,
  RunResult,
  MacBootMode,
  SandboxAccessToken,
  SandboxConnectOptions,
  SandboxHandleJSON,
  ImageRef,
  SnapshotRef,
  SandboxInfo,
  SandboxLifecycle,
  SandboxLifecycleDto,
  SandboxState,
  SandboxWaitState,
  SystemMetrics,
  WaitOptions,
  WaitForStateOptions,
  SandboxClass,
  SandboxDto,
  CaptureImageOptions,
  ProcessCommand,
} from './types.js'

// Node's timer ceiling: a larger request timeout overflows to ~1 ms.
const MAX_REQUEST_TIMEOUT_MS = 2_147_483_647

type LinuxForkRequest = api.ForkRequest & {
  readonly network_policy?: Record<string, unknown> | null
}

export const CODE_TOOLBOX_LANGUAGE_LABEL = 'code-toolbox-language'
const UNSCOPED_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE = 'pre-signed URLs require a sandbox-scoped toolbox URL'
// States from which no wait target can ever be reached again. `stopped` /
// `paused` are deliberately NOT here: they are resumable, so a caller waiting
// for `started` may legitimately expect someone to start() the sandbox. Pass
// `terminalStates` explicitly to fail fast on those.
const DEFAULT_SANDBOX_TERMINAL_STATES = ['destroying', 'destroyed', 'error'] as const satisfies readonly SandboxState[]
// macOS: a failed pause returns to `started` and an unfinished resume ends `stopped`;
// after those requests each state means failure, not something to keep waiting past.
const MACOS_PAUSE_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'started'] as const satisfies readonly SandboxState[]
const MACOS_RESUME_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'stopped'] as const satisfies readonly SandboxState[]
const WAIT_FOR_STATE_SLICE_MS = 30_000
// Mint a fresh sandbox token when the held one expires within this window
// (refresh with a 60 s lead).
const SANDBOX_TOKEN_REFRESH_LEAD_MS = 60_000

/**
 * Create()-path extras on the connect options: the create response's token is
 * refreshable (unlike a caller-supplied one) and may carry an expiry.
 * @internal
 */
export interface SandboxConnectOptionsFromCreate extends SandboxConnectOptions {
  /** Marks `accessToken` as the create response's value, not the caller's. */
  accessTokenFromCreate?: boolean
  /** Expiry (RFC 3339) of the create response's `access_token`, when known. */
  accessTokenExpiresAt?: string
  /**
   * The constructor input's toolbox URL came from the API (create
 * response, `get`/`list` Sandbox, fork). Without it the constructor ignores
   * every toolbox URL inside its first argument — stored or hand-built data
   * never picks where a credential goes; only `options.toolboxUrl`
   * (an explicit caller choice) is honored.
   */
  toolboxUrlFromApi?: boolean
}

/** `POST …/access-token` response payload. */
interface SandboxAccessTokenPayload {
  access_token?: string
  token_type?: string
  expires_at?: string
  operations?: string[]
  ports?: number[] | null
}

function sandboxAccessTokenFromPayload(payload: SandboxAccessTokenPayload | null | undefined): SandboxAccessToken {
  const accessToken = payload?.access_token
  const expiresAt = payload?.expires_at
  if (typeof accessToken !== 'string' || accessToken.length === 0 || typeof expiresAt !== 'string') {
    throw new DaytonaError('API returned an invalid access token payload', 500)
  }
  return credentialSafeInspection({
    accessToken,
    tokenType: typeof payload?.token_type === 'string' ? payload.token_type : 'Bearer',
    expiresAt,
    operations: Array.isArray(payload?.operations) ? payload.operations.filter((operation): operation is string => typeof operation === 'string') : [],
    ports: Array.isArray(payload?.ports) ? payload.ports.filter((port): port is number => Number.isInteger(port)) : undefined,
  }, () => ({ type: 'SandboxAccessToken', expiresAt }))
}

/**
 * When the access-token route is unavailable, use `accessToken ?? apiKey`
 * instead of failing every sandbox request.
 */
function mintRouteUnsupported(error: unknown): boolean {
  return error instanceof DaytonaError && (error.statusCode === 404 || error.statusCode === 405 || error.statusCode === 501)
}

function rfc3339EpochMs(value: string | undefined): number | undefined {
  if (!value) return undefined
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? undefined : parsed
}

type LifecycleWaitOptions = { readonly timeout?: number }

const SERIALIZED_SANDBOX_INFO_FIELDS = [
  'name',
  'image',
  'snapshot',
  'target',
  'cpu',
  'cpuMax',
  'memory',
  'memoryMax',
  'disk',
  'diskMax',
  'state',
  'error',
  'imageBuildId',
  'sandboxClass',
  'labels',
  'region',
  'persistent',
  'spot',
  'spotEvictedAt',
  'lifecyclePolicy',
  'forkedFrom',
  'reservationId',
  'user',
] as const satisfies readonly (keyof SandboxInfo)[]

type SerializedSandboxInfoField = (typeof SERIALIZED_SANDBOX_INFO_FIELDS)[number]

function hasSandboxInfoField(info: SandboxInfo, field: keyof SandboxInfo): boolean {
  return Object.prototype.hasOwnProperty.call(info, field)
}

export interface UpdateEnvOptions {
  readonly unset?: readonly string[]
}

function remainingRequestBudget(deadline: number | undefined): number | undefined {
  return deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
}

function includesWaitState(states: readonly SandboxWaitState[], state: SandboxState): boolean {
  return states.some((target) => target === state)
}

function unsupportedOperationError(message: string): UnsupportedOperationError {
  return new UnsupportedOperationError(message, 400, 'UNSUPPORTED_OPERATION', 'SDK')
}

// Both placeholder spellings are documented (README.md) and must work in
// either SDK: a template is often copied between a Python and a TypeScript
// integration verbatim.
const SANDBOX_ID_PLACEHOLDERS = ['{sandboxId}', '{sandbox_id}'] as const

function hasSandboxIdPlaceholder(url: string): boolean {
  return SANDBOX_ID_PLACEHOLDERS.some((placeholder) => url.includes(placeholder))
}

// The placeholder is a PATH segment: `…/{sandboxId}`. A placeholder in
// the scheme/host/port (`https://{sandboxId}.example.com/…`) describes a
// hostname outside the supported path-template syntax; refuse it rather than
// silently constructing an unsupported URL.
function assertPlaceholderInPath(url: string): void {
  // Split the raw string rather than parsing: WHATWG `new URL()` either rejects a
  // brace in the host ("Invalid URL") or percent-encodes it, so the placeholder
  // would be unrecognisable by the time we looked. Scheme + authority end at the
  // first '/' after '://'; query starts at the first '?', fragment at the first '#'.
  // The authority ends at the FIRST of '/', '?' or '#' after '://': a URL like
  // `https://h?x=/{id}` has no path at all, and a '/' inside the query must not
  // be mistaken for the path start.
  const schemeEnd = url.indexOf('://')
  const from = schemeEnd >= 0 ? schemeEnd + 3 : 0
  const firstIndexOf = (s: string, chars: readonly string[], start: number): number =>
    chars.map((c) => s.indexOf(c, start)).filter((i) => i >= 0).reduce((a, b) => Math.min(a, b), s.length)
  const pathStart = firstIndexOf(url, ['/', '?', '#'], from)
  const authority = url.slice(0, pathStart)
  const rest = url.slice(pathStart)
  // The path itself ends at the first '?' or '#'; everything after is outside.
  const pathEnd = rest.startsWith('/') ? firstIndexOf(rest, ['?', '#'], 0) : 0
  const outsidePath = [authority, rest.slice(pathEnd)]
  if (outsidePath.some(hasSandboxIdPlaceholder)) {
    throw new DaytonaError(
      'the {sandboxId} placeholder in a toolbox URL is only supported in the path (e.g. https://toolbox.example/{sandboxId}), not in the scheme, host, query or fragment',
      400,
      'BAD_REQUEST',
      'SDK',
    )
  }
}

function expandSandboxIdPlaceholder(url: string, sandboxId: string): string {
  assertPlaceholderInPath(url)
  // Same RFC 3986 strict encoder the presigned path uses, so a template expands
  // byte-identically to Python's quote(id, safe='') — including !'()*, which
  // encodeURIComponent leaves bare.
  const encoded = strictUriEncode(sandboxId)
  return SANDBOX_ID_PLACEHOLDERS.reduce((acc, placeholder) => acc.split(placeholder).join(encoded), url)
}

/** Scope a create response's toolbox base URL to its sandbox ID (see Daytona.create). */
export function scopedProxyToolboxUrl(base: string | undefined, sandboxId: string): string | undefined {
  return scopedToolboxUrl(base, sandboxId, undefined)
}

function scopedToolboxUrl(configured: string | undefined, sandboxId: string, direct: string | undefined): string | undefined {
  if (direct) {
    const trimmedDirect = direct.replace(/\/$/, '')
    if (hasSandboxIdPlaceholder(trimmedDirect)) return expandSandboxIdPlaceholder(trimmedDirect, sandboxId)
    return trimmedDirect
  }
  if (!configured) return undefined
  const trimmed = configured.replace(/\/$/, '')
  if (hasSandboxIdPlaceholder(trimmed)) return expandSandboxIdPlaceholder(trimmed, sandboxId)
  return `${trimmed}/${strictUriEncode(sandboxId)}`
}

function artifactRefFromInfo(value: ImageRef | SnapshotRef | string | undefined): { readonly id: string; readonly name: string | null } | null {
  if (value === undefined || value === null) return null
  if (typeof value === 'string') return null
  return { id: value.id, name: value.name ?? null }
}

function isScopedProxyToolboxUrl(toolboxUrl: string, sandboxId: string): boolean {
  const trimmed = toolboxUrl.replace(/\/$/, '')
  if (hasSandboxIdPlaceholder(trimmed)) return true
  // A URL WHATWG cannot parse (e.g. schemeless `toolbox.example/abc`)
  // must classify, not throw: Python's urlsplit treats the whole string as the
  // path, so the suffix rule below runs on the raw string for parity.
  let pathname: string
  try {
    pathname = new URL(trimmed).pathname.replace(/\/$/, '')
  } catch {
    pathname = trimmed
  }
  const encodedSandboxId = strictUriEncode(sandboxId)
  if (
    pathname.endsWith(`/${sandboxId}`)
    || pathname.endsWith(`/${encodedSandboxId}`)
  ) {
    return true
  }
  return false
}

function waitQueryState(state: SandboxWaitState | undefined): string | undefined {
  switch (state) {
    case undefined:
      return undefined
    case 'queued':
      return 'queued'
    case 'starting':
      return 'booting'
    case 'started':
      return 'running'
    case 'pausing':
    case 'paused':
    case 'stopping':
    case 'stopped':
      return state
    case 'destroying':
      return 'deleting'
    case 'destroyed':
      return 'deleted'
    default:
      return state
  }
}

function validateTimeout(timeout: number): void {
  if (!Number.isFinite(timeout) || timeout < 0) {
    throw new DaytonaError('timeout must be a non-negative finite number', 400, 'BAD_REQUEST')
  }
}

function secondsToMilliseconds(timeout: number): number {
  validateTimeout(timeout)
  return timeout === 0 ? 0 : Math.ceil(timeout * 1000)
}

const SANDBOX_CLASSES: readonly string[] = ['linux', 'macos'] satisfies readonly SandboxClass[]

function isSandboxClass(value: string): value is SandboxClass {
  return SANDBOX_CLASSES.includes(value)
}

/**
 * The wire class: `class`, defaulting to Linux when absent. It may be
 * unknown to this SDK, so callers that route by class must refuse it rather than guess.
 */
function wireSandboxClass(sandbox: SandboxDto): string {
  if (typeof sandbox.class === 'string' && sandbox.class !== '') return sandbox.class
  return 'linux'
}

function isSandboxInfo(value: SandboxDto | SandboxInfo): value is SandboxInfo {
  return 'sandboxId' in value
}

function emptyStringMap(): Record<string, string> {
  return Object.create(null) as Record<string, string>
}

function parseLabels(value: unknown): Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {}
  const labels = emptyStringMap()
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'string') labels[key] = entry
  }
  return labels
}

function labelsBody(labels: unknown): Record<string, string> {
  if (typeof labels !== 'object' || labels === null || Array.isArray(labels)) {
    throw new BadRequestError('setLabels requires a Record<string, string>', 400, 'BAD_REQUEST', 'SDK')
  }
  const output = emptyStringMap()
  for (const [key, value] of Object.entries(labels)) {
    if (typeof value !== 'string') {
      throw new DaytonaError('setLabels requires a Record<string, string>', 400, 'BAD_REQUEST', 'SDK')
    }
    output[key] = value
  }
  return output
}

// Only names no environment can hold are refused here; which names are
// reserved for the platform is checked by the API, which answers as a 400
// naming the key.
function validateEnvKey(key: string): string {
  if (key.length === 0 || key.includes('=') || key.includes('\0') || key.includes('\n') || key.includes('\r')) {
    throw new BadRequestError('env key is not a valid variable name', 400, 'BAD_REQUEST', 'SDK')
  }
  return key
}

function validateEnvValue(value: string): string {
  if (value.includes('\0')) {
    throw new BadRequestError('environment values must not contain NUL', 400, 'BAD_REQUEST', 'SDK')
  }
  return value
}

function updateEnvBody(env: Record<string, string>, options: UpdateEnvOptions): toolbox.EnvRequest {
  if (typeof env !== 'object' || env === null || Array.isArray(env)) {
    throw new BadRequestError('updateEnv requires env to be a Record<string, string>', 400, 'BAD_REQUEST', 'SDK')
  }
  const outputEnv = emptyStringMap()
  for (const [key, value] of Object.entries(env)) {
    validateEnvKey(key)
    if (typeof value !== 'string') {
      throw new BadRequestError('updateEnv requires env to be a Record<string, string>', 400, 'BAD_REQUEST', 'SDK')
    }
    outputEnv[key] = validateEnvValue(value)
  }
  const unset = options.unset ?? []
  if (!Array.isArray(unset) || unset.some((key) => typeof key !== 'string')) {
    throw new BadRequestError('updateEnv requires unset to be an array of strings', 400, 'BAD_REQUEST', 'SDK')
  }
  return {
    env: outputEnv,
    unset: unset.map((key) => validateEnvKey(key)),
  }
}

/** Sub-millisecond microseconds of an RFC 3339 timestamp (digits 4-6 of the fraction), for breaking Date.parse millisecond ties across mixed-precision renderings. */
function subMsMicros(value: string | undefined): number {
  const frac = value?.match(/\.(\d+)/)?.[1] ?? ''
  return Number(frac.padEnd(6, '0').slice(3, 6))
}

export class Sandbox {
  public readonly id: string
  public toolboxProxyUrl?: string
  public name?: string
  public image: ImageRef | null = null
  public snapshot: SnapshotRef | null = null
  public target?: string
  public labels: Record<string, string> = {}
  /** Reserved CPU (the guarantee), in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU). */
  public cpu?: number
  /** CPU limit (the boot shape), in cores; undefined when the API did not report it. */
  public cpuMax?: number
  public memory?: number
  public memoryMax?: number
  public disk?: number
  public diskMax?: number
  public state?: SandboxState
  public rawState?: string
  public error?: string
  public errorReason?: string
  private macBootModeValue: MacBootMode | null = null
  public imageBuildId?: string
  /** Undefined until fetched, and for a class this SDK does not know (calls that depend on the class then throw). */
  public sandboxClass?: SandboxClass
  public user?: string
  public region?: string
  /**
   * macOS only: how the sandbox resumed, as reported by the API. Null
   * for any other class and for a sandbox not resumed since it last booted.
   */
  public get macBootMode(): MacBootMode | null {
    return this.macBootModeValue
  }
  private wireClass?: string
  /** macOS only: whether the sandbox preserves its disk when stopped; undefined on a
    *  handle whose metadata has not been loaded yet. */
  public persistent?: boolean
  public spot?: boolean
  public spotEvictedAt?: string
  public lifecyclePolicy?: SandboxLifecycle
  public forkedFrom?: string
  /**
   * macOS only: the ID of the reservation selected for this sandbox.
   * Unset before a reservation is selected, and on search results
   * (`search()`, a search-routed `list()`), which omit it; `refreshData()` reads it.
   */
  public reservationId?: string
  public accessToken?: string
  /**
   * Expiry of `accessToken` (RFC 3339), when known. Undefined for the
   * opaque `dtnv_` create token (no known expiry) and for
   * handles without a token.
   */
  public accessTokenExpiresAt?: string
  private readonly accessTokenCallerSupplied: boolean
  private readonly sandboxTokenMintable: boolean
  private readonly sandboxAuth?: TransportAuth
  private sandboxTokenMintUnsupported = false
  private sandboxTokenMint?: Promise<string>
  private readonly autoDelete: boolean
  private readonly api?: AxiosInstance
  private readonly apiKey?: string
  private readonly explicitToolboxUrl?: string
  private toolboxTransport?: HttpTransport
  private readonly serializedInfoFields = new Set<keyof SandboxInfo>()
  private fsNamespace?: FileSystemService
  private gitNamespace?: GitService
  private processNamespace?: ProcessService
  private interpreterNamespace?: InterpreterService
  private ptyNamespace?: PtyService
  private computerUseNamespace?: ComputerUseService
  private dockerNamespace?: DockerService
  private portsNamespace?: PortsService
  private volumesNamespace?: SandboxVolumesService
  private lspNamespace?: LspService
  private _hub?: WaitHub

  /**
   * Low-level constructor; prefer `daytona.create*()`, `get(id)` or `list()`.
   * Any toolbox URL inside `sandboxOrId` (a stored `toJSON()` blob or an
   * API-shaped object) is ignored, so stored data never chooses where the
   * SDK sends a credential. Pass `options.toolboxUrl` to set one explicitly —
   * the toolbox credential (the API key, a project token, a minted sandbox
   * token, or `options.accessToken`) goes there, so pass only a URL you trust.
   * The third parameter, `toolboxBaseUrl`, is unused: pass the URL as
   * `options.toolboxUrl`.
   */
  constructor(sandboxOrId: SandboxDto | SandboxInfo | string, api: AxiosInstance | undefined, toolboxBaseUrl: string | undefined, apiKey: string | undefined, target?: string, options?: SandboxConnectOptions, requestTimeout?: number, httpOptions?: HttpClientOptions)
  /** @internal The `hub` parameter is not part of the public API. */
  constructor(sandboxOrId: SandboxDto | SandboxInfo | string, api: AxiosInstance | undefined, toolboxBaseUrl: string | undefined, apiKey: string | undefined, target: string | undefined, options: SandboxConnectOptions | undefined, requestTimeout: number | undefined, httpOptions: HttpClientOptions | undefined, hub: WaitHub | undefined)
  constructor(sandboxOrId: SandboxDto | SandboxInfo | string, api: AxiosInstance | undefined, toolboxBaseUrl: string | undefined, apiKey: string | undefined, target?: string, options: SandboxConnectOptions = {}, private readonly requestTimeout?: number, private readonly httpOptions?: HttpClientOptions, hub?: WaitHub) {
    const sandbox = typeof sandboxOrId === 'string' || isSandboxInfo(sandboxOrId) ? undefined : sandboxOrId
    const info = typeof sandboxOrId === 'string' || !isSandboxInfo(sandboxOrId) ? undefined : sandboxOrId
    this.id = typeof sandboxOrId === 'string' ? sandboxOrId : isSandboxInfo(sandboxOrId) ? sandboxOrId.sandboxId : sandboxOrId.id
    this.api = api
    this.apiKey = apiKey
    const createOptions = options as SandboxConnectOptionsFromCreate
    const inputUrlsTrusted = createOptions.toolboxUrlFromApi === true
    this.explicitToolboxUrl = options.toolboxUrl?.replace(/\/$/, '')
    this.target = target
    this.accessToken = options.accessToken
    this.accessTokenExpiresAt = createOptions.accessTokenFromCreate ? createOptions.accessTokenExpiresAt : undefined
    // A caller-supplied token is NEVER silently replaced; everything else with
    // a project API key mints and refreshes sandbox access tokens
    // transparently.
    this.accessTokenCallerSupplied = options.accessToken !== undefined && createOptions.accessTokenFromCreate !== true
    const hasCreationToken = createOptions.accessTokenFromCreate === true && options.accessToken !== undefined
    const hasApiCredential = (apiKey !== undefined && apiKey.length > 0) || (this.httpOptions?.authProvider !== undefined && hasCreationToken)
    this.sandboxTokenMintable = !this.accessTokenCallerSupplied && api !== undefined && hasApiCredential
    if (this.sandboxTokenMintable) {
      this.sandboxAuth = {
        bearer: () => this.sandboxBearer(),
        onUnauthorized: (failedToken) => this.sandboxRemint(failedToken),
      }
    }
    this.autoDelete = options.autoDelete ?? false
    this._hub = hub
    if (sandbox) this.applySandbox(sandbox)
    if (info) this.applyInfo(info)
    if (this.region === undefined) {
      const region = regionOf(this.id)
      this.region = region === '_' ? undefined : region
      if (this.region !== undefined) this.rememberSerializedInfoField('region')
    }
    const serializedScopedToolboxUrl = inputUrlsTrusted ? info?.toolboxProxyUrl?.replace(/\/$/, '') : undefined
    const sandboxToolboxUrl = inputUrlsTrusted ? sandbox?.toolbox_proxy_url ?? sandbox?.toolboxProxyUrl : undefined
    this.toolboxProxyUrl = this.explicitToolboxUrl
      ? scopedToolboxUrl(sandboxToolboxUrl, this.id, this.explicitToolboxUrl)
      : serializedScopedToolboxUrl ?? scopedToolboxUrl(sandboxToolboxUrl, this.id, undefined)
    if (this.toolboxProxyUrl) this.toolboxTransport = this.createToolboxTransport(this.toolboxProxyUrl)
    credentialSafeDiagnostics(this, () => ({ type: 'Sandbox', id: this.id }))
  }

  private createToolboxTransport(toolboxUrl: string): HttpTransport {
    const transportOptions = this.accessToken === undefined ? this.httpOptions : { ...this.httpOptions, authProvider: undefined }
    return new HttpTransport(toolboxUrl, this.accessToken ?? this.apiKey, this.requestTimeout, {
      ...transportOptions,
      auth: this.sandboxAuth,
      sandboxId: this.id,
    })
  }

  private createApiTransport(): HttpTransport {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    return new HttpTransport(this.api.defaults.baseURL ?? '', this.apiKey, this.requestTimeout, this.httpOptions)
  }

  private rememberSerializedInfoField(field: keyof SandboxInfo): void {
    this.serializedInfoFields.add(field)
  }

  private rememberSerializedInfoFields(fields: readonly (keyof SandboxInfo)[]): void {
    for (const field of fields) {
      this.serializedInfoFields.add(field)
    }
  }

  private serializedField<K extends keyof SandboxInfo>(field: K, value: SandboxInfo[K]): SandboxInfo[K] | undefined {
    return this.serializedInfoFields.has(field) ? value : undefined
  }

  /** @internal */
  public learnEventDestination(destination: string | undefined): void {
    if (destination && this._hub !== undefined) this._hub.learn(this.id, destination)
  }

  /** @internal */
  public attachHub(hub: WaitHub): void {
    if (this._hub === undefined || this._hub.closed) this._hub = hub
  }

  public get fs(): FileSystemService {
    this.fsNamespace ??= new FileSystemService(this.requireToolbox(), (method, path, expiresAt) => this.signFileUrl(method, path, expiresAt))
    return this.fsNamespace
  }

  public get git(): GitService {
    this.gitNamespace ??= new GitService(this.requireToolbox())
    return this.gitNamespace
  }

  public get process(): ProcessService {
    this.processNamespace ??= new ProcessService(this.requireToolbox(), this.id)
    return this.processNamespace
  }

  public get interpreter(): InterpreterService {
    this.interpreterNamespace ??= new InterpreterService(this.requireToolbox(), this.process)
    return this.interpreterNamespace
  }

  public get pty(): PtyService {
    this.ptyNamespace ??= new PtyService(this.process)
    return this.ptyNamespace
  }

  public get computerUse(): ComputerUseService {
    this.computerUseNamespace ??= new ComputerUseService(this.requireToolbox())
    return this.computerUseNamespace
  }

  /** Docker Engine status inside the sandbox; see {@link DockerService}. */
  public get docker(): DockerService {
    this.dockerNamespace ??= new DockerService(this.requireToolbox())
    return this.dockerNamespace
  }

  public get ports(): PortsService {
    this.portsNamespace ??= new PortsService(this.toolboxTransport, this.api, this.id, (port) => this.mintPreviewToken(port))
    return this.portsNamespace
  }

  public get lsp(): LspService {
    this.lspNamespace ??= new LspService(this.requireToolbox())
    return this.lspNamespace
  }


  public async metrics(): Promise<SystemMetrics> {
    return this.requireToolbox().get<SystemMetrics>('/system/metrics')
  }

  public get volumes(): SandboxVolumesService {
    this.volumesNamespace ??= new SandboxVolumesService(this.requireToolbox())
    return this.volumesNamespace
  }

  /**
   * Run a command, wait for its shell to exit, and collect its output.
   *
   * A convenience alias for {@link ProcessService.run} — identical behaviour,
   * same parameters, same return value.
   *
   * **Shell selection.** Passing a string runs the command through the sandbox
   * shell: `bash` when the image has it, else `sh`. Pass `shell` to pin another
   * interpreter, and quote globs and brackets (`pip install -e '.[test]'`)
   * so the command means the same under any shell. Pass `{ argv: [...] }` to
   * bypass the shell entirely, which is the safe form for untrusted arguments.
   * Use `process.createSession()` when commands need shared shell state.
   *
   * **Background processes.** The call returns when the shell exits. If the
   * command launches a background child that holds its output descriptors open
   * (e.g. `python3 -m http.server 8000 &`), execution waits at most ~2
   * seconds for output to drain and then returns; output the child writes later
   * is not part of the result. For a long-running server use
   * {@link ProcessService.start}, which returns a {@link ProcessHandle}
   * immediately (logs, wait, kill). Alternatively redirect:
   * `nohup cmd > server.log 2>&1 &`.
   *
   * @param command Shell command string, or `{ argv }` for shell-free execution.
   * @param options.shell Shell binary to use (e.g. `'bash'`); overrides auto-detection.
   * @param options.timeout Caller wait budget in milliseconds. Omit to wait until
   *   the process ends. Does not kill the process on expiry.
   * @param options.executionTimeout Server-side deadline in milliseconds. Execution
   *   kills the process tree when this expires.
   * @returns {@link RunResult} with `stdout`, `stderr`, `output`, and `exitCode`.
   */
  public async exec(command: ProcessCommand, options: RunOptions = {}): Promise<RunResult> {
    return this.process.run(command, options)
  }

  public async runCode(code: string, options: InterpreterRunOptions = {}): Promise<InterpreterResult> {
    return this.interpreter.run(code, options)
  }

  /**
   * The sandbox's data as JSON. A snapshot, not a way back: resume a stored
   * sandbox with `daytona.get(info.sandboxId)`. It carries no toolbox URL —
   * where a credential goes always comes from the API or an explicit
   * caller option, never from stored data.
   */
  public toJSON(): SandboxHandleJSON {
    const info = dropUndefined({
      sandboxId: this.id,
      name: this.serializedField('name', this.name),
      image: this.serializedField('image', this.image ?? undefined),
      snapshot: this.serializedField('snapshot', this.snapshot ?? undefined),
      target: this.serializedField('target', this.target),
      cpu: this.serializedField('cpu', this.cpu),
      cpuMax: this.serializedField('cpuMax', this.cpuMax),
      memory: this.serializedField('memory', this.memory),
      memoryMax: this.serializedField('memoryMax', this.memoryMax),
      disk: this.serializedField('disk', this.disk),
      diskMax: this.serializedField('diskMax', this.diskMax),
      state: this.serializedField('state', this.state),
      error: this.serializedField('error', this.error),
      imageBuildId: this.serializedField('imageBuildId', this.imageBuildId),
      sandboxClass: this.serializedField('sandboxClass', this.sandboxClass),
      labels: this.serializedField('labels', this.labels),
      region: this.serializedField('region', this.region),
      persistent: this.serializedField('persistent', this.persistent),
      spot: this.serializedField('spot', this.spot),
      spotEvictedAt: this.serializedField('spotEvictedAt', this.spotEvictedAt),
      lifecyclePolicy: this.serializedField('lifecyclePolicy', this.lifecyclePolicy),
      forkedFrom: this.serializedField('forkedFrom', this.forkedFrom),
      reservationId: this.serializedField('reservationId', this.reservationId),
      user: this.serializedField('user', this.user),
    }) as unknown as SandboxInfo
    return info
  }

  /** Refresh sandbox metadata from the API. */
  public async refreshData(): Promise<void> {
    await this.refreshDataWithin()
  }

  private async refreshDataWithin(timeout?: number, wait?: SandboxWaitState): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const waitValue = waitQueryState(wait)
    const waitKey = waitValue === undefined ? 'wait' : waitQueryKey(waitValue)
    const config = timeout === undefined
      ? waitValue === undefined ? undefined : { timeout: WAIT_FOR_STATE_SLICE_MS + WAIT_READ_SLACK_MS, params: { [waitKey]: waitValue, timeout_ms: WAIT_FOR_STATE_SLICE_MS } }
      : waitValue === undefined ? { timeout } : { timeout: timeout + WAIT_READ_SLACK_MS, params: { [waitKey]: waitValue, timeout_ms: timeout } }
    const sandbox = await this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(this.id)}`, config).then((response) => response.data)
    this.applySandbox(sandbox)
    this.refreshToolbox(sandbox)
  }

  private applySandbox(sandbox: SandboxDto): void {
    this.rememberSerializedInfoFields(SERIALIZED_SANDBOX_INFO_FIELDS)
    this.name = sandbox.name
    this.image = artifactRefOf(sandbox.image)
    this.snapshot = artifactRefOf(sandbox.snapshot)
    this.labels = parseLabels(sandbox.labels)
    this.cpu = sandbox.cpu
    this.cpuMax = sandbox.cores_max ?? undefined
    // macOS reports its size exactly (4608 MiB -> 4.5 GiB); Linux rounds up
    // to whole GiB.
    const wireClass = wireSandboxClass(sandbox)
    const exactGib = wireClass === 'macos'
    this.memory = exactGib && sandbox.memory_mib !== undefined ? sandbox.memory_mib / 1024 : mibToGb(sandbox.memory_mib)
    this.memoryMax = sandbox.memory_max_mib === null || sandbox.memory_max_mib === undefined ? undefined : exactGib ? sandbox.memory_max_mib / 1024 : mibToGb(sandbox.memory_max_mib)
    this.disk = exactGib && sandbox.disk_mib !== undefined ? sandbox.disk_mib / 1024 : mibToGb(sandbox.disk_mib)
    this.diskMax = sandbox.disk_max_mib === null || sandbox.disk_max_mib === undefined ? undefined : exactGib ? sandbox.disk_max_mib / 1024 : mibToGb(sandbox.disk_max_mib)
    this.rawState = sandbox.status ?? undefined
    this.state = sandboxState(sandbox.status)
    this.error = sandbox.error ?? undefined
    this.errorReason = sandbox.error ?? undefined
    this.macBootModeValue = sandbox.mac_boot_mode ?? null
    this.imageBuildId = sandbox.image_build_id ?? undefined
    this.wireClass = wireClass
    this.sandboxClass = isSandboxClass(wireClass) ? wireClass : undefined
    const region = regionOf(this.id)
    this.region = sandbox.region_id || (region === '_' ? undefined : region)
    this.target = this.region ?? this.target
    this.persistent = sandbox.persistent ?? undefined
    this.user = sandbox.user ?? undefined
    this.spot = sandbox.spot ?? undefined
    this.spotEvictedAt = sandbox.spot_evicted_at ?? undefined
    this.lifecyclePolicy = sandbox.lifecycle_policy ? parseSandboxLifecycle(sandbox.lifecycle_policy) : undefined
    this.forkedFrom = sandbox.forked_from ?? undefined
    this.reservationId = sandbox.reservation_id ?? undefined
  }

  private applyInfo(info: SandboxInfo): void {
    for (const field of SERIALIZED_SANDBOX_INFO_FIELDS) {
      if (!hasSandboxInfoField(info, field)) continue
      this.rememberSerializedInfoField(field)
      this.applyInfoField(field, info)
    }
  }

  private applyInfoField(field: SerializedSandboxInfoField, info: SandboxInfo): void {
    switch (field) {
      case 'name': this.name = info.name; return
      case 'image': this.image = artifactRefFromInfo(info.image); return
      case 'snapshot': this.snapshot = artifactRefFromInfo(info.snapshot); return
      case 'target': this.target = info.target; return
      case 'cpu': this.cpu = info.cpu; return
      case 'cpuMax': this.cpuMax = info.cpuMax; return
      case 'memory': this.memory = info.memory; return
      case 'memoryMax': this.memoryMax = info.memoryMax; return
      case 'disk': this.disk = info.disk; return
      case 'diskMax': this.diskMax = info.diskMax; return
      case 'state': this.state = info.state; return
      case 'error': this.error = info.error; this.errorReason = info.error; return
      case 'imageBuildId': this.imageBuildId = info.imageBuildId; return
      case 'sandboxClass': this.sandboxClass = info.sandboxClass; return
      case 'labels': this.labels = parseLabels(info.labels); return
      case 'region': {
        const region = regionOf(this.id)
        this.region = info.region || (region === '_' ? undefined : region)
        this.target = this.region ?? this.target
        return
      }
      case 'persistent': this.persistent = info.persistent; return
      case 'spot': this.spot = info.spot; return
      case 'spotEvictedAt': this.spotEvictedAt = info.spotEvictedAt; return
      case 'lifecyclePolicy': this.lifecyclePolicy = info.lifecyclePolicy; return
      case 'forkedFrom': this.forkedFrom = info.forkedFrom; return
      case 'reservationId': this.reservationId = info.reservationId; return
      case 'user': this.user = info.user; return
      default: {
        const unhandled: never = field
        throw new Error(`unhandled SandboxInfo field ${String(unhandled)}`)
      }
    }
  }

  private refreshToolbox(sandbox: SandboxDto): void {
    if (this.explicitToolboxUrl) return
    const resolved = scopedToolboxUrl(sandbox.toolbox_proxy_url ?? sandbox.toolboxProxyUrl, this.id, undefined)
    if (!resolved || resolved === this.toolboxProxyUrl) return
    this.toolboxProxyUrl = resolved
    this.toolboxTransport = this.createToolboxTransport(resolved)
    this.fsNamespace = undefined
    this.gitNamespace = undefined
    this.processNamespace = undefined
    this.interpreterNamespace = undefined
    this.ptyNamespace = undefined
    this.computerUseNamespace = undefined
    this.dockerNamespace = undefined
    this.portsNamespace = undefined
    this.volumesNamespace = undefined
    this.lspNamespace = undefined
  }

  private requireToolbox(): HttpTransport {
    if (!this.toolboxTransport) throw new DaytonaError('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)', 400, 'BAD_REQUEST', 'SDK')
    return this.toolboxTransport
  }

  private requireApiForPresignedUrls(): AxiosInstance {
    if (!this.api) throw new DaytonaError('API is not available for pre-signed file URLs', 400, 'UNSUPPORTED_OPERATION')
    return this.api
  }

  private assertScopedToolboxUrlForPresignedUrls(): void {
    if (this.usesDirectToolboxUrlForPresignedUrls()) {
      throw unsupportedOperationError(UNSCOPED_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE)
    }
  }

  private usesDirectToolboxUrlForPresignedUrls(): boolean {
    if (this.explicitToolboxUrl === undefined) return false
    return !isScopedProxyToolboxUrl(this.explicitToolboxUrl, this.id)
  }

  // `method` is `GET` for a
  // download link and `POST` for an upload link; `expiresAt` is RFC 3339 and is
  // bounded by the caller (`SIGNED_FILE_MAX_TTL_SECONDS`) before this runs.
  private async signFileUrl(method: 'GET' | 'POST', path: string, expiresAt: string): Promise<SignedFileUrl> {
    this.assertScopedToolboxUrlForPresignedUrls()
    const body: api.SignFileUrlRequest = { method, path, expires_at: expiresAt }
    const response = await this.requireApiForPresignedUrls().post<api.SignedFileUrlResponse>(
      `/sandboxes/${strictUriEncode(this.id)}/files/sign`,
      body,
    )
    return readSignedFileUrl(response.data)
  }

  /**
   * Rotate the sandbox's signing key: every pre-signed URL minted so far stops
   * working within ~30 s; new URLs from presignedDownloadUrl/UploadUrl use the
   * new key. The key itself is never returned.
   */
  public async rotateSigningKey(): Promise<void> {
    // The same toolbox URL requirement applies to signing and rotation.
    // Refuse an unsupported URL before sending the request.
    this.assertScopedToolboxUrlForPresignedUrls()
    await this.requireApiForPresignedUrls().post(
      `/sandboxes/${strictUriEncode(this.id)}/signing-key/rotate`,
    )
  }

  /** The class, read from the server once when this handle has none yet; undefined for a class this SDK does not know. */
  private async loadSandboxClass(): Promise<SandboxClass | undefined> {
    if (this.sandboxClass === undefined && this.wireClass === undefined) await this.refreshData()
    return this.sandboxClass
  }

  /** The class for a call that depends on it; refuses a class this SDK does not know rather than guess `linux`. */
  private async requireSandboxClass(): Promise<SandboxClass> {
    const sandboxClass = await this.loadSandboxClass()
    if (sandboxClass !== undefined) return sandboxClass
    throw unsupportedOperationError(`unsupported sandbox class ${this.wireClass ?? '(none)'}`)
  }

  private async requirePersistentMacos(operation: 'stop' | 'pause' | 'resume'): Promise<void> {
    const sandboxClass = await this.requireSandboxClass()
    if (sandboxClass === 'macos' && this.persistent === undefined) await this.refreshData()
    if (sandboxClass !== 'macos' || this.persistent !== true) {
      throw unsupportedOperationError(`${operation} requires a persistent macOS sandbox`)
    }
  }

  /**
   * Mint a scoped, short-lived access token for THIS sandbox.
   *
   * The token authorizes sandbox services (toolbox, SSH, desktop,
   * private previews) and the self-service API routes, for exactly the
   * granted operations and ports — hand it to a collaborator, a CI job, or a
   * browser instead of a project API key. It expires at `expiresAt`, cannot be
   * refreshed, and can never mint further credentials; mint a fresh one here
   * instead. An unknown operation or an over-cap `ttlSeconds` is a 400, never a
   * silent narrowing.
   */
  public async createAccessToken(options: CreateAccessTokenOptions = {}): Promise<SandboxAccessToken> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    if (options.ports !== undefined) {
      for (const port of options.ports) {
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
          throw new DaytonaError(`port must be an integer in 1..65535, got ${port}`, 400, 'BAD_REQUEST', 'SDK')
        }
      }
    }
    if (options.ttlSeconds !== undefined && (!Number.isInteger(options.ttlSeconds) || options.ttlSeconds <= 0)) {
      throw new DaytonaError('ttlSeconds must be a positive integer', 400, 'BAD_REQUEST', 'SDK')
    }
    // Mint directly without an extra metadata request.
    const body: Record<string, unknown> = {}
    if (options.operations !== undefined) body.operations = [...options.operations]
    if (options.ports !== undefined) body.ports = [...options.ports]
    if (options.ttlSeconds !== undefined) body.ttl_seconds = options.ttlSeconds
    const response = await this.api.post<SandboxAccessTokenPayload>(`/sandboxes/${strictUriEncode(this.id)}/access-token`, body)
    return sandboxAccessTokenFromPayload(response.data)
  }

  /**
   * Whether the held token can be sent as-is. A token with no known expiry
   * (such as an opaque `dtnv_` from create) is used as-is — a 401 is
   * what re-mints it. A known expiry within the 60 s lead re-mints proactively.
   */
  private sandboxTokenFresh(): boolean {
    if (!this.accessToken) return false
    const expiresAtMs = rfc3339EpochMs(this.accessTokenExpiresAt)
    if (expiresAtMs === undefined) return true
    return expiresAtMs - Date.now() > SANDBOX_TOKEN_REFRESH_LEAD_MS
  }

  private async sandboxBearer(): Promise<string | undefined> {
    if (this.sandboxTokenFresh()) return this.accessToken
    if (this.sandboxTokenMintUnsupported) return this.compatSandboxBearer()
    try {
      return await this.refreshSandboxToken(undefined)
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.sandboxTokenMintUnsupported = true
        return this.compatSandboxBearer()
      }
      // A failed proactive refresh must not fail requests a still-valid
      // token would have served: inside the 60 s refresh lead but before
      // `expiresAt`, use the held token until expiry and let the next call
      // retry the mint. Only a token
      // that is actually gone surfaces the mint error.
      const expiresAtMs = rfc3339EpochMs(this.accessTokenExpiresAt)
      if (this.accessToken && expiresAtMs !== undefined && expiresAtMs > Date.now()) {
        return this.accessToken
      }
      throw error
    }
  }

  /**
   * The bearer on a deployment without the mint routes: the held token until
   * it is known to have expired, then the API credential (the API key, or
   * the project token of a JWT client).
   */
  private async compatSandboxBearer(): Promise<string | undefined> {
    const expiresAtMs = rfc3339EpochMs(this.accessTokenExpiresAt)
    if (this.accessToken && (expiresAtMs === undefined || expiresAtMs > Date.now())) return this.accessToken
    return (await this.apiCredential()) ?? this.accessToken
  }

  private async apiCredential(): Promise<string | undefined> {
    if (this.apiKey) return this.apiKey
    const header = await this.httpOptions?.authProvider?.authorizationHeader()
    return header?.replace(/^Bearer\s+/i, '') || undefined
  }

  /**
   * Without the mint routes a rejected token is retried once with the API
   * credential. A rejected sandbox token is dropped, so later requests go
   * straight to the API credential instead of repeating the 401.
   */
  private async apiCredentialRetry(failedToken: string | undefined): Promise<string | undefined> {
    let fallback = await this.apiCredential()
    const authProvider = this.httpOptions?.authProvider
    if (fallback !== undefined && fallback === failedToken && !this.apiKey && authProvider) {
      // The rejected token IS the cached project token: re-exchange it.
      const header = await authProvider.authorizationHeader({ forceRefresh: true, rejectedAuthorization: `Bearer ${failedToken}` })
      fallback = header.replace(/^Bearer\s+/i, '') || undefined
    }
    if (fallback === undefined || fallback === failedToken) return undefined
    if (failedToken !== undefined && failedToken === this.accessToken) {
      this.accessToken = undefined
      this.accessTokenExpiresAt = undefined
    }
    return fallback
  }

  /** One re-mint after a sandbox request returns 401; `undefined` surfaces the 401. */
  private async sandboxRemint(failedToken: string | undefined): Promise<string | undefined> {
    if (!this.sandboxTokenMintable) return undefined
    if (this.sandboxTokenMintUnsupported) return this.apiCredentialRetry(failedToken)
    try {
      return await this.refreshSandboxToken(failedToken)
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.sandboxTokenMintUnsupported = true
        return this.apiCredentialRetry(failedToken)
      }
      if (error instanceof DaytonaError && error.statusCode === 403) {
        // A FORBIDDEN mint is the actionable error — this credential cannot
        // mint for this sandbox — and surfacing the original toolbox 401
        // instead would point at the wrong credential.
        throw error
      }
      // The sandbox request's 401 is the actionable error; a failed re-mint must not
      // mask it.
      return undefined
    }
  }

  /**
   * Mint a full-grant token, single-flighted per handle: concurrent callers
   * coalesce on one in-flight mint. On the 401 path (`failedToken` set) a
   * stored token that already differs from the failed one was minted by a
   * concurrent caller and is returned as-is.
   */
  private async refreshSandboxToken(failedToken: string | undefined): Promise<string> {
    if (failedToken === undefined) {
      if (this.sandboxTokenFresh() && this.accessToken) return this.accessToken
    } else if (this.accessToken && this.accessToken !== failedToken) {
      return this.accessToken
    }
    this.sandboxTokenMint ??= this.mintSandboxTokenNow().finally(() => {
      this.sandboxTokenMint = undefined
    })
    return this.sandboxTokenMint
  }

  private async mintSandboxTokenNow(): Promise<string> {
    const minted = await this.createAccessToken()
    this.accessToken = minted.accessToken
    this.accessTokenExpiresAt = minted.expiresAt
    return minted.accessToken
  }

  /**
   * A ports-scoped token for one private preview port, or `undefined` when
   * this handle cannot mint (caller-supplied token, no key, or a deployment
   * without the mint routes) — the ports namespace then falls back to the
   * route-returned preview token.
   */
  private async mintPreviewToken(port: number): Promise<SandboxAccessToken | undefined> {
    if (!this.sandboxTokenMintable || this.sandboxTokenMintUnsupported) return undefined
    try {
      return await this.createAccessToken({ operations: [], ports: [port] })
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.sandboxTokenMintUnsupported = true
        return undefined
      }
      throw error
    }
  }

  public async waitForState(states: readonly SandboxWaitState[], options: WaitForStateOptions = {}): Promise<void> {
    if (states.length === 0) throw new DaytonaError('waitForState() requires at least one target state', 400, 'BAD_REQUEST', 'SDK')
    const timeout = options.timeout
    if (timeout !== undefined) validateTimeout(timeout)
    const terminalStates = options.terminalStates ?? DEFAULT_SANDBOX_TERMINAL_STATES
    const deadline = timeout === undefined || timeout === 0 ? undefined : Date.now() + timeout
    const waitTarget = new Set(states).size === 1 ? states[0] : undefined
    const effectiveTerminal = states.includes('destroyed') ? terminalStates.filter((s) => s !== 'destroying') : terminalStates

    if (this._hub !== undefined && this._hub.enabled) {
      const predicate = targetPredicate(
        new Set(states as readonly SandboxState[]),
        new Set(effectiveTerminal),
      )
      const hub = this._hub
      // The hub's long-poll fallback: one refresh per call, paced like the
      // polling loop below (the back-off floor sits BETWEEN two polls, never
      // after a resolving one). The hub judges the state.
      let lastPollEnd: number | undefined
      // Whether the latest outcome came from a full GET (the fallback poll)
      // rather than a socket frame, which carries only the state.
      let refreshedByPoll = false
      const sandboxPollFn = async (): Promise<WaitOutcome> => {
        if (lastPollEnd !== undefined) {
          let pause = WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - lastPollEnd)
          if (deadline !== undefined) pause = Math.min(pause, Math.max(0, deadline - Date.now()))
          if (pause > 0) await sleep(pause)
        }
        const remaining = deadline === undefined ? undefined : deadline - Date.now()
        if (remaining !== undefined && remaining <= 0) {
          throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', 408, 'REQUEST_TIMEOUT', 'SDK')
        }
        const sliceMs = remaining === undefined ? WAIT_FOR_STATE_SLICE_MS : Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(remaining))
        await this.refreshDataWithin(sliceMs, waitTarget)
        lastPollEnd = Date.now()
        refreshedByPoll = true
        return { state: this.state ?? 'unknown', rawState: this.rawState ?? '' }
      }
      const observeOutcome = (outcome: WaitOutcome): void => {
        this.state = outcome.state
        this.rawState = outcome.rawState
      }
      const onObserved = (outcome: WaitOutcome): void => {
        refreshedByPoll = false
        observeOutcome(outcome)
      }
      try {
        const regionHint = this.target?.trim().toLowerCase() === 'local' ? undefined : this.target
        const resolved = await hub.wait(this.id, predicate, { timeout, regionHint, sandboxPollFn, onObserved })
        observeOutcome(resolved)
        // A socket frame carries only the state. Callers (create, start,
        // resume) hand back this handle, so fill in the rest — toolbox URL,
        // region, resources, error reason — with one GET, as the polling path
        // does, and within the same budget. A destroyed sandbox has nothing
        // to fill in (the GET would be a 404).
        if (!refreshedByPoll && resolved.state !== 'destroyed') {
          const remaining = remainingRequestBudget(deadline)
          if (remaining !== undefined && remaining <= 0) {
            throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', 408, 'REQUEST_TIMEOUT', 'SDK')
          }
          await this.refreshDataWithin(remaining === undefined ? undefined : Math.min(Math.ceil(remaining), MAX_REQUEST_TIMEOUT_MS))
        }
        return
      } catch (error) {
        if (error instanceof SandboxTerminalStateError) {
          this.state = error.state
          // Fill in the error reason within the wait's budget. A destroyed
          // sandbox has nothing to fill in (the GET would 404), and a spent
          // budget skips it: the terminal state is the outcome either way.
          let freshRead = refreshedByPoll
          const remaining = remainingRequestBudget(deadline)
          if (!refreshedByPoll && error.state !== 'destroyed' && (remaining === undefined || remaining > 0)) {
            try {
              await this.refreshDataWithin(remaining === undefined ? undefined : Math.min(Math.ceil(remaining), MAX_REQUEST_TIMEOUT_MS))
              freshRead = true
            } catch {
              // The terminal state is the error to report; a failed or
              // timed-out refresh must not replace it.
            }
          }
          // The hub reports only the state. When the read that followed
          // still shows that state, name the server's explanation for it (why
          // a deletion did not finish, say) in the error.
          if (freshRead && error.reason === undefined && this.errorReason && this.state === error.state) {
            throw new SandboxTerminalStateError(this.id, error.state, error.rawState, this.errorReason)
          }
        }
        throw error
      }
    }

    let fresh = false
    for (;;) {
      if (fresh) {
        if (this.state !== undefined && includesWaitState(states, this.state)) return
        if (this.state !== undefined && effectiveTerminal.includes(this.state)) {
          throw new SandboxTerminalStateError(this.id, this.state, this.rawState, this.errorReason)
        }
      }
      const refreshTimeout = remainingRequestBudget(deadline)
      if (refreshTimeout !== undefined && refreshTimeout <= 0) {
        throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', 408, 'REQUEST_TIMEOUT', 'SDK')
      }
      const requestTimeout = deadline === undefined ? WAIT_FOR_STATE_SLICE_MS : Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(refreshTimeout ?? 0))
      const pollStarted = Date.now()
      await this.refreshDataWithin(requestTimeout, waitTarget)
      fresh = true
      if (this.state !== undefined && includesWaitState(states, this.state)) return
      if (this.state !== undefined && effectiveTerminal.includes(this.state)) {
        throw new SandboxTerminalStateError(this.id, this.state, this.rawState, this.errorReason)
      }
      const elapsed = Date.now() - pollStarted
      if (elapsed < WAIT_FOR_STATE_MIN_INTERVAL_MS) {
        let pause = WAIT_FOR_STATE_MIN_INTERVAL_MS - elapsed
        if (deadline !== undefined) pause = Math.min(pause, Math.max(0, deadline - Date.now()))
        if (pause > 0) await sleep(pause)
      }
    }
  }

  private async waitUntilStarted(timeout = 60_000, terminalStates: readonly SandboxState[] = DEFAULT_SANDBOX_TERMINAL_STATES): Promise<void> {
    await this.waitForState(['started'], { timeout, terminalStates })
  }

  /** Start a stopped sandbox, or resume a paused one, and wait until it is started. */
  public async start(options: LifecycleWaitOptions = {}): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const timeout = options.timeout ?? 60_000
    validateTimeout(timeout)
    await this.refreshData()
    if (this.state === 'started') return
    let terminalStates: readonly SandboxState[] = DEFAULT_SANDBOX_TERMINAL_STATES
    if (this.state === 'paused') {
      await this.requirePersistentMacos('resume')
      const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/resume`)
      this.learnEventDestination(eventDestinationFromHeaders(r.headers))
      terminalStates = MACOS_RESUME_TERMINAL_STATES
    } else if (this.state === 'stopped') {
      const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/start`)
      this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    }
    await this.waitUntilStarted(timeout, terminalStates)
  }

  /** Stop a persistent macOS sandbox. */
  public async stop(): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    await this.requirePersistentMacos('stop')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/stop`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.refreshData()
  }

  /**
   * Pause a persistent macOS sandbox with its memory. Waits up to `timeout` for `paused` and throws
   * `SandboxTerminalStateError` if the pause fails. A paused macOS sandbox keeps its Mac slot
   * but does not keep its reservation renewing.
   */
  public async pause(options: LifecycleWaitOptions = {}): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const timeout = options.timeout ?? 60_000
    validateTimeout(timeout)
    await this.requirePersistentMacos('pause')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/pause`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitForState(['paused'], { timeout, terminalStates: MACOS_PAUSE_TERMINAL_STATES })
  }

  /**
   * Resume a paused persistent macOS sandbox and wait until it is started. A
   * macOS resume the Mac cannot finish ends `stopped`; this then throws
   * `SandboxTerminalStateError`.
   */
  public async resume(options: LifecycleWaitOptions = {}): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const timeout = options.timeout ?? 60_000
    validateTimeout(timeout)
    await this.requirePersistentMacos('resume')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/resume`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitUntilStarted(timeout, MACOS_RESUME_TERMINAL_STATES)
  }

  /**
   * Fork this sandbox and return the fork handle.
   *
   * By default the fork continues from the source's memory (running processes
   * included). `memory: false` copies only the disk, after which the fork boots
   * fresh. A stopped sandbox is always forked
   * disk-only. Only a fork of a RUNNING sandbox is started automatically; a
   * fork of a paused source rests `paused` (memory fork, call `resume()`) or
   * `stopped` (`memory: false`), and a fork of a stopped source rests
   * `stopped` — call `start()` on those.
   *
   * A macOS fork copies the disk only: omit `memory` or pass `memory: false`
   * (`memory: true` is refused with `NOT_SUPPORTED_FOR_CLASS`). A fork of a
   * running macOS sandbox boots with a fresh machine identity; a fork of a
   * paused or stopped one rests `stopped`. The fork may wait for capacity,
   * as a create does; insufficient capacity can return `NO_FREE_SLOT`.
   */
  public async fork(options: { name?: string; memory?: boolean; timeout?: number } = {}): Promise<Sandbox> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const sandboxClass = await this.requireSandboxClass()
    const body: LinuxForkRequest = {}
    if (options.name) body.name = options.name
    if (options.memory !== undefined) body.memory = options.memory
    // Only a fork of a RUNNING source comes up by itself; the others rest
    // (`paused`/`stopped`). With a timeout, wait for the state the fork will
    // actually settle in. The source can change state while the request is
    // in flight. A macOS fork settles `started` or `stopped`, so accept either.
    let sourceRunning = false
    if (options.timeout !== undefined) {
      validateTimeout(options.timeout)
      if (sandboxClass !== 'macos') {
        await this.refreshData()
        sourceRunning = this.state === 'started'
      }
    }
    const response = await this.api.post<api.ForkResponse>(`/sandboxes/${strictUriEncode(this.id)}/fork`, body)
    this.learnEventDestination(eventDestinationFromHeaders(response.headers))
    const sandbox = await this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(response.data.id)}`).then((r) => r.data)
    // The fork's metadata comes from its detail; its toolbox from the fork
    // response, never the parent's per-sandbox override.
    const apiSourced: SandboxConnectOptionsFromCreate = { toolboxUrlFromApi: true }
    const fork = new Sandbox({ ...sandbox, toolbox_proxy_url: response.data.toolbox_proxy_url || sandbox.toolbox_proxy_url }, this.api, undefined, this.apiKey, this.target, apiSourced, this.requestTimeout, this.httpOptions, this._hub)
    if (options.timeout !== undefined) {
      if (sandboxClass === 'macos') await fork.waitForState(['started', 'stopped'], { timeout: options.timeout })
      else if (sourceRunning) await fork.waitUntilStarted(options.timeout)
      else await fork.waitForState(['paused', 'stopped'], { timeout: options.timeout })
    }
    return fork
  }

  /**
   * Request deletion.
   * Returning, a `destroyed` state, or a subsequent 404 does not guarantee that
   * the image can already be deleted. Image deletion may still return 409.
   */
  public async delete(): Promise<void> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const r = await this.api.delete(`/sandboxes/${strictUriEncode(this.id)}`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    try {
      await this.refreshData()
    } catch (error) {
      if (error instanceof NotFoundError) {
        this.rememberSerializedInfoField('state')
        this.state = 'destroyed'
      }
      else throw error
    }
  }

  public async getNetworkPolicy(): Promise<NetworkPolicyInfo> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api.get(`/sandboxes/${encodeURIComponent(this.id)}/network-policy`)
    return parseNetworkPolicyResponse(response)
  }

  public async updateNetworkPolicy(
    networkPolicy: NetworkPolicyInput,
    options: { ifMatch: string },
  ): Promise<NetworkPolicyInfo> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api.put(
      `/sandboxes/${encodeURIComponent(this.id)}/network-policy`,
      serializeNetworkPolicyInput(networkPolicy),
      { headers: { 'If-Match': options.ifMatch } },
    )
    return parseNetworkPolicyResponse(response)
  }

  public async resetNetworkPolicy(options: { ifMatch: string }): Promise<NetworkPolicyInfo> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api.put(`/sandboxes/${encodeURIComponent(this.id)}/network-policy`, 'null', {
      headers: { 'If-Match': options.ifMatch, 'Content-Type': 'application/json' },
    })
    return parseNetworkPolicyResponse(response)
  }

  /**
   * Read this sandbox's attached secret names and their apply state (`GET /sandboxes/{id}/secrets`).
   * Returns names and revisions only, never values. `etag` is the `ifMatch` value for
   * `replaceSecrets`; `status` is `pending` until the sandbox has applied `desiredRevision`.
   */
  public async getSecrets(): Promise<SandboxSecretsInfo> {
    if (!this.api) throw new DaytonaError('API is not available for this rehydrated sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api.get(`/sandboxes/${encodeURIComponent(this.id)}/secrets`)
    return parseSandboxSecretsResponse(response)
  }

  /**
   * Replace the full set of project secrets attached to this sandbox (`PUT /sandboxes/{id}/secrets`).
   * `secrets` names project secrets. `ifMatch` is the
   * `etag` of the last read; a stale one throws `PreconditionFailedError`. The change applies
   * asynchronously: poll `getSecrets` until `status` is `applied`. Running processes keep their
   * environment; processes started afterwards see the new names.
   */
  public async replaceSecrets(secrets: readonly string[], options: { ifMatch: string }): Promise<SandboxSecretsInfo> {
    if (!this.api) throw new DaytonaError('API is not available for this rehydrated sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api.put(
      `/sandboxes/${encodeURIComponent(this.id)}/secrets`,
      serializeSandboxSecretsInput(secrets),
      { headers: { 'If-Match': options.ifMatch } },
    )
    return parseSandboxSecretsResponse(response)
  }

  /** Dispose by deleting the sandbox when constructed with `autoDelete`. */
  public async [Symbol.asyncDispose](): Promise<void> {
    if (!this.autoDelete) return
    await this.delete()
  }

  /**
   * Update this sandbox's lifecycle policy; omitted knobs stay unchanged.
   *
   * `timeoutSeconds` re-anchors the deadline from now (`>= 1`; the timeout cannot
   * be disabled). `idleExpirySeconds: DISABLED` clears the idle pair; arming it on
   * a sandbox with no idle policy requires `onIdleTimeout`.
   *
   * Setting either action to `'delete'` — or touching a pair whose current or
   * resulting action is `'delete'` — requires the API key to hold
   * `delete:sandboxes` in addition to `write:sandboxes`.
   */
  public async updateLifecyclePolicy(options: LifecyclePolicyOptions): Promise<SandboxLifecycle> {
    const body = lifecycleBody(options)
    if (!body) throw new DaytonaError('updateLifecyclePolicy requires at least one lifecycle field', 400, 'BAD_REQUEST')
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const sandboxClass = await this.requireSandboxClass()
    if (sandboxClass === 'macos' && this.persistent === undefined) await this.refreshData()
    assertLifecycleClassSupport(options, {
      sandboxClass,
      persistent: this.persistent === true,
      action: 'updateLifecyclePolicy',
    })
    const resp = await this.api
      .patch<{ lifecycle_policy: SandboxLifecycleDto; id: string }>(`/sandboxes/${strictUriEncode(this.id)}/lifecycle-policy`, body)
    this.learnEventDestination(eventDestinationFromHeaders(resp.headers))
    this.rememberSerializedInfoField('lifecyclePolicy')
    this.lifecyclePolicy = parseSandboxLifecycle(resp.data)
    return this.lifecyclePolicy
  }

  /**
   * Reset this sandbox's idle timer: an explicit keepalive for work the
   * platform cannot see.
   *
   * Bumps the sandbox's activity clock (`lastActivityAt`, attributed
   * server-side to the `api` source), which restarts the idle countdown —
   * `idleTimeoutAt` re-anchors to now + `idleExpirySeconds`. The wall-clock
   * `timeoutAt` is NOT affected. Succeeds whether or not an idle timeout is
   * armed (an unarmed sandbox echoes `idleTimeoutAt: undefined`), and in any
   * state — a never-started sandbox already carries its creation-time
   * activity stamp, which the reset advances.
   *
   * The echo is the full post-reset lifecycle, the same shape
   * `updateLifecyclePolicy()` returns. Advance-only: a newer concurrent activity bump
   * keeps the clock, and what comes back is the state that won.
   */
  public async resetIdleTimer(): Promise<SandboxLifecycle> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const response = await this.api
      .post<{ lifecycle_policy: SandboxLifecycleDto; id: string }>(`/sandboxes/${strictUriEncode(this.id)}/reset-idle-timer`)
      .then((r) => r.data)
    const fresh = parseSandboxLifecycle(response)
    // Concurrent resets can return out of order, and a late-arriving older
    // echo must not roll the cached clock backwards. A real reset always
    // echoes a clock, so a degenerate echo whose clock is missing or
    // unparseable is evidence of something wrong and never replaces a valid
    // cached one; a cache that is empty or unparseable always takes the fresh
    // echo. Date.parse truncates the server's microsecond timestamps to
    // milliseconds, so a millisecond tie is broken on the parsed sub-millisecond
    // fraction: string order is chronological only when both sides use the
    // fixed-width RFC 3339 micros+Z rendering, which a cached or
    // hand-constructed value need not.
    const cachedStr = this.lifecyclePolicy?.lastActivityAt
    const echoedStr = fresh.lastActivityAt
    const cached = Date.parse(cachedStr ?? '')
    const echoed = Date.parse(echoedStr ?? '')
    const echoAdvances =
      Number.isNaN(cached) ||
      (!Number.isNaN(echoed) &&
        (echoed > cached || (echoed === cached && subMsMicros(echoedStr) >= subMsMicros(cachedStr))))
    if (echoAdvances) {
      this.lifecyclePolicy = fresh
    }
    return fresh
  }

  /**
   * Wait for one or more public sandbox states.
   *
   * `timeout` is a caller wait budget in seconds. The default is 60; `0`
   * waits without a deadline. Timeout raises {@link RequestTimeoutError}, and
   * a terminal state before the target raises {@link SandboxTerminalStateError}.
   */
  public async wait(options: WaitOptions = {}): Promise<Sandbox> {
    const until = options.until ?? 'started'
    const states = Array.isArray(until) ? [...until] : [until]
    await this.waitForState(states, { timeout: secondsToMilliseconds(options.timeout ?? 60) })
    return this
  }

  /**
   * Snapshot this sandbox's filesystem as a new {@link Image}
   * (`POST /sandboxes/{id}/images`). The result boots new sandboxes
   * (`daytona.create(image.id)`); a Linux sandbox keeps running. A macOS sandbox must be persistent and
   * stopped; it cannot start until the snapshot is `ready`.
   */
  public async snapshotFilesystem(options: CaptureImageOptions = {}): Promise<Image> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const { data } = await this.api.post<api.AsyncOperation>(`/sandboxes/${strictUriEncode(this.id)}/images`, {
      name: options.name,
      idle_expiry_seconds: options.idleExpirySeconds,
    } satisfies api.CreateImageCaptureRequest)
    const image = await this.api.get<api.ImageListItem>(`/images/${strictUriEncode(data.id)}`).then((response) => response.data)
    return new Image(image)
  }

  public async createSnapshot(name: string): Promise<Snapshot> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    // The snapshot response carries its class.
    const http = this.api
    const encodedId = strictUriEncode(this.id)
    const response = await http.post<api.CreateSnapshotResponse>(`/sandboxes/${encodedId}/snapshots`, { name })
    const snapshot = await http.get<api.Snapshot>(`/snapshots/${strictUriEncode(response.data.id)}`).then((result) => result.data)
    return new Snapshot(snapshot)
  }

  /** Replace the sandbox's full label set. Pass `{}` to clear all labels; resolves to the applied labels. */
  public async setLabels(labels: Record<string, string>): Promise<Record<string, string>> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const body = labelsBody(labels)
    const requestConfig = { daytona: { routeTemplate: '/sandboxes/{id}/labels', sandboxId: this.id } } as AxiosRequestConfig
    const response = await this.api.put<{ labels?: unknown }>(
      `/sandboxes/${strictUriEncode(this.id)}/labels`,
      { labels: body },
      requestConfig,
    ).then((result) => result.data)
    this.rememberSerializedInfoField('labels')
    this.labels = parseLabels(response.labels)
    return this.labels
  }

  /** Return the sandbox entrypoint log as plain text. */
  public async entrypointLogs(): Promise<string> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    const requestConfig = {
      responseType: 'text',
      daytona: { routeTemplate: '/sandboxes/{id}/logs', sandboxId: this.id },
    } as AxiosRequestConfig
    return this.api.get<string>(`/sandboxes/${strictUriEncode(this.id)}/logs`, requestConfig).then((response) => response.data)
  }

  /** Update the extra environment layer used by processes created after this call. */
  public async updateEnv(env: Record<string, string> = {}, options: UpdateEnvOptions = {}): Promise<void> {
    await this.requireToolbox().post<void>('/env', updateEnvBody(env, options))
  }

  /**
   * Return retained build output as one-shot text, not a live follower.
   * The body includes the current build state and available output.
   * A sandbox with no image build (for example a registry image) throws
   * `NotFoundError`. Output can be incomplete while the build is in progress;
   * truncation is indicated in the text.
   * Use followBuildLogs for live output and completeness metadata.
   */
  public async buildLogs(): Promise<string> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    return readBuildLogs(this.createApiTransport(), `/sandboxes/${strictUriEncode(this.id)}/build-logs-url`)
  }

  /**
   * Observe this sandbox's build without changing its execution lifetime.
   * Discovery failures, including a 404 for a sandbox with no image build,
   * throw BuildLogInterruptedError with the HTTP status and last cursor.
   * They do not fall back to another log source.
   */
  public async followBuildLogs(options: BuildLogFollowOptions): Promise<BuildLogFollowResult> {
    if (!this.api) throw new DaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION')
    return followBuildLogs(this.createApiTransport(), `/sandboxes/${strictUriEncode(this.id)}/build-logs-url`, options)
  }
}

export { createHttpClient }

function readSignedFileUrl(payload: api.SignedFileUrlResponse | unknown): SignedFileUrl {
  // Typed against the vendored wire model, but still checked at runtime: a
  // JSON `null` body is a valid axios `data`, and dereferencing it would
  // surface a raw TypeError instead of the documented invalid-payload error.
  if (payload !== null && typeof payload === 'object' && 'signature' in payload && 'expires_at' in payload) {
    const { signature, expires_at: expiresAt } = payload
    if (typeof signature === 'string' && signature.length > 0 && typeof expiresAt === 'string' && rfc3339EpochMs(expiresAt) !== undefined) {
      return { signature, expiresAt }
    }
  }
  throw new DaytonaError('API returned an invalid signed file URL payload', 500)
}
