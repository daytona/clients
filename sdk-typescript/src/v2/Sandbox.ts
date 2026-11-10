/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { withoutNullOptions } from './clientOptionKeys.js'
import { acknowledgedId, checkResponse } from './responseFields.js'
import { pathSegment, usablePathId } from './pathSegment.js'
import type { ResponseFields } from './responseFields.js'
import { isBlank } from './pyText.js'
import type { AxiosInstance, AxiosRequestConfig } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { INTERNAL, assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { followBuildLogs, readBuildLogs } from './BuildLogs.js'
import type { BuildLogFollowOptions, BuildLogFollowResult } from './BuildLogs.js'
import { FileSystemService } from './FileSystem.js'
import type { SignedFileUrl } from './FileSystem.js'
import { Image, Snapshot, imageOptionsBody } from './Image.js'
import { strictUriEncode } from './http.js'
import { GitService } from './Git.js'
import { InterpreterService } from './Interpreter.js'
import { ComputerUseService } from './ComputerUse.js'
import { DockerService } from './Docker.js'
import { LspService } from './Lsp.js'
import { parseNetworkPolicyResponse, serializeNetworkPolicyInput } from './NetworkPolicy.js'
import { PortsService } from './Ports.js'
import { SandboxVolumesService } from './SandboxVolumes.js'
import { parseSandboxSecretsResponse, serializeSandboxSecretsInput } from './SandboxSecrets.js'
import { ProcessService, PtyService, WAIT_READ_SLACK_MS } from './Process.js'
import { systemMetricsFromWire } from './toolboxModels.js'
import { isRfc3339 } from './rfc3339.js'
import type * as api from './gen/api-types.js'
import type * as toolbox from './gen/toolbox-types.js'
import {
  assertLifecycleClassSupport,
  createHttpClient,
  HttpTransport,
  boundedRequestTimeout,
  sandboxState,
  type HttpClientOptions,
  lifecycleBody,
  parseSandboxLifecycle,
} from './http.js'
import { BadRequestError, ClientTimeoutError, DaytonaError, RequestTimeoutError, TransferCancelledError, UnsupportedOperationError, createDaytonaError } from './errors/DaytonaError.js'
import { eventDestinationFromHeaders } from './events.js'
import type { WaitHub } from './events.js'
import { assertIfMatchOptions, assertKnownOptions, assertOptionsObject, badRequest, isPlainStringRecord, optionKeys, optionalSeconds, RENAMED_OPTIONS, waitDeadline } from './options.js'
import type { IfMatchOptions } from './options.js'
import { deepFrozen } from './frozen.js'
import { assertSandboxResponse, isSandboxInfo, metadataFromInfo, metadataFromSandbox, parseLabels, sandboxHandleJSON } from './sandboxMetadata.js'
import type { SandboxMetadata } from './sandboxMetadata.js'
import { SandboxAuth, rfc3339EpochMs } from './sandboxAuth.js'
import { DEFAULT_SANDBOX_TERMINAL_STATES, PAUSE_TERMINAL_STATES, RESUME_TERMINAL_STATES, WAIT_FOR_STATE_SLICE_MS, waitForSandboxState, waitQueryState } from './sandboxWait.js'
import type { SandboxStateWaitOptions } from './sandboxWait.js'
import type {
  CreateAccessTokenOptions,
  InterpreterResult,
  InterpreterRunOptions,
  LifecyclePolicyOptions,
  LifecycleWaitOptions,
  ForkOptions,
  NetworkPolicyInfo,
  NetworkPolicyInput,
  SandboxSecretsInfo,
  RunOptions,
  ProcessResult,
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
  SandboxClass,
  SandboxDto,
  CaptureImageOptions,
  ProcessCommand,
} from './types.js'

export const CODE_TOOLBOX_LANGUAGE_LABEL = 'code-toolbox-language'
const UNSCOPED_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE = 'pre-signed URLs require a sandbox-scoped toolbox URL'

/**
 * Create()-path extras on the connect options: the create response's token is
 * refreshable (unlike a caller-supplied one) and may carry an expiry.
 * @internal
 */
export interface SandboxConnectOptionsFromCreate extends SandboxConnectOptions {
  /** Marks `accessToken` as the create response's value, not the caller's. */
  readonly accessTokenFromCreate?: boolean
  /** Expiry (RFC 3339) of the create response's `access_token`, when known. */
  readonly accessTokenExpiresAt?: string
  /**
   * The init object's toolbox URL came from the API (create response,
   * `get`/`list` Sandbox, fork). Without it the constructor ignores
   * every toolbox URL inside its metadata — stored or hand-built data
   * never picks where a credential goes; only `options.toolboxUrl`
   * (an explicit caller choice) is honored.
   */
  readonly toolboxUrlFromApi?: boolean
}

export interface UpdateEnvOptions {
  readonly unset?: readonly string[]
}

const LIFECYCLE_WAIT_KEYS = optionKeys<LifecycleWaitOptions>()({ timeoutSeconds: true })
const WAIT_KEYS = optionKeys<WaitOptions>()({ until: true, timeoutSeconds: true })
const FORK_KEYS = optionKeys<ForkOptions>()({ name: true, memory: true, waitSeconds: true, autoDelete: true })
// Only the id: the fork exists once it came back, as for a create. A missing
// or non-string status reads as `unknown`, `forked_from` falls back to the
// source's id, and the toolbox URL comes from the fork's detail read.
const FORK_RESPONSE: ResponseFields = { id: 'string' }
const CAPTURE_KEYS = optionKeys<CaptureImageOptions>()({ name: true, idleExpirySeconds: true })
const UPDATE_ENV_KEYS = optionKeys<UpdateEnvOptions>()({ unset: true })
const LIFECYCLE_POLICY_KEYS = optionKeys<LifecyclePolicyOptions>()({ timeoutSeconds: true, onTimeout: true, idleExpirySeconds: true, onIdleTimeout: true })

/** @internal */
interface SandboxInit {
  readonly sandbox: SandboxDto | SandboxInfo | string
  readonly api?: AxiosInstance
  readonly apiKey?: string
  readonly options?: SandboxConnectOptionsFromCreate
  /** Internal transport timeout in milliseconds. */
  readonly requestTimeout?: number
  readonly httpOptions?: HttpClientOptions
  readonly hub?: WaitHub
}

function unsupportedOperationError(message: string): UnsupportedOperationError {
  return new UnsupportedOperationError(message, { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
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
    throw badRequest(
      'the {sandboxId} placeholder in a toolbox URL is only supported in the path (e.g. https://toolbox.example/{sandboxId}), not in the scheme, host, query or fragment',
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

function emptyStringMap(): Record<string, string> {
  return Object.create(null) as Record<string, string>
}

function labelsBody(labels: unknown): Record<string, string> {
  if (!isPlainStringRecord(labels)) throw badRequest('setLabels requires a Record<string, string>')
  const output = emptyStringMap()
  for (const [key, value] of Object.entries(labels)) output[key] = value
  return output
}

// Only names no environment can hold are refused here; which names are
// reserved for the platform is checked by the API, which answers as a 400
// naming the key.
function validateEnvKey(key: string): string {
  if (key.length === 0 || key.includes('=') || key.includes('\0') || key.includes('\n') || key.includes('\r')) {
    throw new BadRequestError('env key is not a valid variable name', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return key
}

function validateEnvValue(value: string): string {
  if (value.includes('\0')) {
    throw new BadRequestError('environment values must not contain NUL', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value
}

function updateEnvBody(env: Record<string, string>, options: UpdateEnvOptions): toolbox.EnvRequest {
  if (typeof env !== 'object' || env === null || Array.isArray(env)) {
    throw new BadRequestError('updateEnv requires env to be a Record<string, string>', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  const outputEnv = emptyStringMap()
  for (const [key, value] of Object.entries(env)) {
    validateEnvKey(key)
    if (typeof value !== 'string') {
      throw new BadRequestError('updateEnv requires env to be a Record<string, string>', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    outputEnv[key] = validateEnvValue(value)
  }
  const unset = options.unset ?? []
  if (!Array.isArray(unset) || unset.some((key) => typeof key !== 'string')) {
    throw new BadRequestError('updateEnv requires unset to be an array of strings', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
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

// allow: SIZE_OK — Public handle facade preserves the toolbox namespaces; metadata,
// token ownership and wait engines are isolated in their own internal modules.
// The lifecycle echo of the policy update and the idle-timer reset (the
// contract's `SandboxLifecyclePolicy`), checked before it is mapped.
const LIFECYCLE_ECHO: ResponseFields = {
  id: 'string',
  lifecycle_policy: {
    fields: {
      timeout_seconds: 'int32?', on_timeout: 'string?', timeout_at: 'timestamp?', idle_expiry_seconds: 'int32?',
      on_idle_timeout: 'string?', idle_timeout_at: 'timestamp?', last_activity_at: 'timestamp?', last_activity_source: 'string?',
    },
    optional: false,
  },
}

function checkLifecycleEcho(data: unknown): { lifecycle_policy: SandboxLifecycleDto; id: string } {
  return checkResponse<{ lifecycle_policy: SandboxLifecycleDto; id: string }>(data, LIFECYCLE_ECHO, 'sandbox lifecycle policy')
}

export class Sandbox {
  private readonly sandboxId: string
  /** The sandbox id, fixed for the handle's lifetime (read-only at run time too). */
  public get id(): string { return this.sandboxId }
  private observed: SandboxMetadata
  private toolboxProxyUrlValue?: string
  private readonly auth: SandboxAuth
  /** @internal */
  public get toolboxProxyUrl(): string | undefined { return this.toolboxProxyUrlValue }
  public get name(): string | undefined { return this.observed.name }
  public get image(): ImageRef | null { return this.observed.image ?? null }
  public get snapshot(): SnapshotRef | null { return this.observed.snapshot ?? null }
  public get labels(): Readonly<Record<string, string>> { return this.observed.labels ?? {} }
  /** Reserved CPU (the guarantee), in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU). */
  public get cpu(): number | undefined { return this.observed.cpu }
  /** CPU limit (the boot shape), in cores; undefined when the API did not report it. */
  public get cpuMax(): number | undefined { return this.observed.cpuMax }
  public get memory(): number | undefined { return this.observed.memory }
  public get memoryMax(): number | undefined { return this.observed.memoryMax }
  public get disk(): number | undefined { return this.observed.disk }
  public get diskMax(): number | undefined { return this.observed.diskMax }
  public get state(): SandboxState | undefined { return this.observed.state }
  public get rawState(): string | undefined { return this.observed.rawState }
  public get error(): string | undefined { return this.observed.error }
  public get imageBuildId(): string | undefined { return this.observed.imageBuildId }
  /** Undefined before the class is known; future wire classes normalize to `unknown`. */
  public get sandboxClass(): SandboxClass | 'unknown' | undefined { return this.observed.sandboxClass }
  public get rawSandboxClass(): string | undefined { return this.observed.rawSandboxClass }
  public get user(): string | undefined { return this.observed.user }
  public get region(): string | undefined { return this.observed.region }
  /**
   * macOS only: how the sandbox resumed, as reported by the API. Null
   * for any other class and for a sandbox not resumed since it last booted.
   */
  public get macBootMode(): MacBootMode | null {
    return this.observed.macBootMode
  }
  /** macOS only: whether the sandbox preserves its disk when stopped; undefined on a
    *  handle whose metadata has not been loaded yet. */
  public get persistent(): boolean | undefined { return this.observed.persistent }
  public get spot(): boolean | undefined { return this.observed.spot }
  public get spotEvictedAt(): string | undefined { return this.observed.spotEvictedAt }
  public get lifecyclePolicy(): SandboxLifecycle | undefined { return this.observed.lifecyclePolicy }
  public get forkedFrom(): string | undefined { return this.observed.forkedFrom }
  /**
   * macOS only: the ID of the reservation selected for this sandbox.
   * Unset before a reservation is selected, and on search results
   * (`search()`), which omit it; `refreshData()` reads it.
   */
  public get reservationId(): string | undefined { return this.observed.reservationId }
  /** The sandbox-scoped bearer currently used: from create, a caller override, or minting.
   * Undefined until known; never serialized. */
  public get accessToken(): string | undefined { return this.auth.accessToken }
  /**
   * Expiry of `accessToken` (RFC 3339), when known. Undefined for the
   * opaque `dtnv_` create token (no known expiry) and for
   * handles without a token.
   */
  public get accessTokenExpiresAt(): string | undefined { return this.auth.accessTokenExpiresAt }
  private readonly autoDelete: boolean
  private readonly api?: AxiosInstance
  private readonly apiKey?: string
  private readonly requestTimeout?: number
  private readonly httpOptions?: HttpClientOptions
  private readonly explicitToolboxUrl?: string
  private toolboxTransport?: HttpTransport
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

  /** @internal */
  constructor(token: Internal, init: SandboxInit)
  /** Built by the SDK: get a sandbox from `daytona.v2.create*()`, `get()` or `list()`. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, init: SandboxInit) {
    assertInternal(token, 'Sandbox')
    const { sandbox: sandboxOrId, api, apiKey, options = {}, hub } = init
    const sandbox = typeof sandboxOrId === 'string' || isSandboxInfo(sandboxOrId) ? undefined : sandboxOrId
    const info = typeof sandboxOrId === 'string' || !isSandboxInfo(sandboxOrId) ? undefined : sandboxOrId
    this.sandboxId = typeof sandboxOrId === 'string' ? sandboxOrId : isSandboxInfo(sandboxOrId) ? sandboxOrId.sandboxId : sandboxOrId.id
    this.observed = sandbox ? metadataFromSandbox(sandbox) : metadataFromInfo(info ?? { sandboxId: this.id })
    this.api = api
    this.apiKey = apiKey
    this.requestTimeout = init.requestTimeout
    this.httpOptions = init.httpOptions
    const createOptions = options
    const inputUrlsTrusted = createOptions.toolboxUrlFromApi === true
    this.explicitToolboxUrl = options.toolboxUrl?.replace(/\/$/, '')
    this.auth = new SandboxAuth({ id: this.id, api, apiKey, httpOptions: this.httpOptions, options })
    this.autoDelete = options.autoDelete ?? false
    this._hub = hub
    const serializedScopedToolboxUrl = inputUrlsTrusted ? info?.toolboxProxyUrl?.replace(/\/$/, '') : undefined
    const sandboxToolboxUrl = inputUrlsTrusted ? sandbox?.toolbox_proxy_url ?? sandbox?.toolboxProxyUrl : undefined
    this.toolboxProxyUrlValue = this.explicitToolboxUrl
      ? scopedToolboxUrl(sandboxToolboxUrl, this.id, this.explicitToolboxUrl)
      : serializedScopedToolboxUrl ?? scopedToolboxUrl(sandboxToolboxUrl, this.id, undefined)
    if (this.toolboxProxyUrl) this.toolboxTransport = this.createToolboxTransport(this.toolboxProxyUrl)
    credentialSafeDiagnostics(this, () => ({ type: 'Sandbox', id: this.id }))
  }

  private createToolboxTransport(toolboxUrl: string): HttpTransport {
    const transportOptions = this.accessToken === undefined ? this.httpOptions : { ...this.httpOptions, authProvider: undefined }
    return new HttpTransport(toolboxUrl, this.accessToken ?? this.apiKey, this.requestTimeout, {
      ...transportOptions,
      auth: this.auth.transportAuth,
      sandboxId: this.id,
    })
  }

  private createApiTransport(): HttpTransport {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    return new HttpTransport(this.api.defaults.baseURL ?? '', this.apiKey, this.requestTimeout, this.httpOptions)
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
    this.fsNamespace ??= new FileSystemService(INTERNAL, this.requireToolbox(), (method, path, expiresAt) => this.signFileUrl(method, path, expiresAt))
    return this.fsNamespace
  }

  public get git(): GitService {
    this.gitNamespace ??= new GitService(INTERNAL, this.requireToolbox())
    return this.gitNamespace
  }

  public get process(): ProcessService {
    this.processNamespace ??= new ProcessService(INTERNAL, this.requireToolbox(), this.id)
    return this.processNamespace
  }

  public get interpreter(): InterpreterService {
    this.interpreterNamespace ??= new InterpreterService(INTERNAL, this.requireToolbox(), this.id)
    return this.interpreterNamespace
  }

  public get pty(): PtyService {
    this.ptyNamespace ??= new PtyService(INTERNAL, this.process, this.requireToolbox())
    return this.ptyNamespace
  }

  public get computerUse(): ComputerUseService {
    this.computerUseNamespace ??= new ComputerUseService(INTERNAL, this.requireToolbox())
    return this.computerUseNamespace
  }

  /** Docker Engine status inside the sandbox; see {@link DockerService}. */
  public get docker(): DockerService {
    this.dockerNamespace ??= new DockerService(INTERNAL, this.requireToolbox())
    return this.dockerNamespace
  }

  public get ports(): PortsService {
    this.portsNamespace ??= new PortsService(INTERNAL, this.toolboxTransport, this.api, this.id, (port) => this.auth.previewToken(port))
    return this.portsNamespace
  }

  public get lsp(): LspService {
    this.lspNamespace ??= new LspService(INTERNAL, this.requireToolbox())
    return this.lspNamespace
  }


  /** A snapshot of the sandbox's CPU, memory and disk usage. */
  public async metrics(): Promise<SystemMetrics> {
    return systemMetricsFromWire(await this.requireToolbox().get<toolbox.Metrics>('/system/metrics'))
  }

  public get volumes(): SandboxVolumesService {
    this.volumesNamespace ??= new SandboxVolumesService(INTERNAL, this.requireToolbox())
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
   * @param options.timeoutSeconds Caller wait budget in seconds. Omit to wait until
   *   the process ends. Does not kill the process on expiry.
   * @param options.executionTimeoutSeconds Server-side deadline in seconds. Execution
   *   kills the process tree when this expires.
   * @returns {@link ProcessResult} with `stdout`, `stderr`, `output`, `logs`, and `exitCode`.
   */
  public async exec(command: ProcessCommand, options: RunOptions = {}): Promise<ProcessResult> {
    return this.process.run(command, options)
  }

  /** Execute code and return its result: an alias for {@link InterpreterService.run}, same options. */
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
    return sandboxHandleJSON(this.id, this.observed)
  }

  /** Refresh sandbox metadata from the API. */
  public async refreshData(): Promise<void> {
    await this.refreshDataWithin()
  }

  private async refreshDataWithin(timeout?: number, wait?: SandboxWaitState, requestDeadline?: number, exact = false): Promise<void> {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const waitValue = waitQueryState(wait)
    // A long poll is governed by the wait budget, not the request timeout:
    // its read allows the hold plus network slack. A plain read is an
    // ordinary request, bounded by the smaller of its budget and the request timeout.
    const rootTimeout = this.api.defaults.timeout
    const rootMs = typeof rootTimeout === 'number' && rootTimeout > 0 ? rootTimeout : Infinity
    const hold = waitValue === undefined ? undefined : Math.ceil(timeout ?? WAIT_FOR_STATE_SLICE_MS)
    const config = hold !== undefined
      ? { timeout: hold + WAIT_READ_SLACK_MS, params: { [exact ? 'wait_exact' : 'wait']: waitValue, timeout_ms: hold } }
      : timeout === undefined ? undefined : { timeout: Math.min(timeout, rootMs) }
    const remaining = requestDeadline === undefined ? undefined : requestDeadline - Date.now()
    if (remaining !== undefined && remaining <= 0) {
      throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    }
    const cap = remaining === undefined ? undefined : Math.max(1, Math.ceil(remaining))
    // Read slack belongs to the long-poll slice, never beyond the caller's
    // deadline. Keep the wire timeout_ms unchanged when capping the HTTP read.
    const deadlineOwnsTimeout = cap !== undefined && (config?.timeout === undefined || config.timeout === 0 || cap <= config.timeout)
    const requestConfig = deadlineOwnsTimeout ? { ...config, timeout: cap } : config
    try {
      const sandbox = await this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(this.id)}`, requestConfig).then((response) => response.data)
      assertSandboxResponse(sandbox)
      this.observed = metadataFromSandbox(sandbox)
      this.refreshToolbox(sandbox)
    } catch (error) {
      // An earlier slice/transport timer and an API HTTP 408 remain request
      // failures; only expiry of this wait's own cap is a wait timeout.
      if (deadlineOwnsTimeout && error instanceof ClientTimeoutError && requestDeadline !== undefined && Date.now() >= requestDeadline) {
        throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      }
      throw error
    }
  }

  private refreshToolbox(sandbox: SandboxDto): void {
    if (this.explicitToolboxUrl) return
    const resolved = scopedToolboxUrl(sandbox.toolbox_proxy_url ?? sandbox.toolboxProxyUrl, this.id, undefined)
    if (!resolved || resolved === this.toolboxProxyUrl) return
    this.toolboxProxyUrlValue = resolved
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
    if (!this.toolboxTransport) throw badRequest('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)')
    return this.toolboxTransport
  }

  private requireApiForPresignedUrls(): AxiosInstance {
    if (!this.api) throw createDaytonaError('API is not available for pre-signed file URLs', 400, 'UNSUPPORTED_OPERATION', 'SDK')
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

  /** Read the class from the server once if this handle has none yet. */
  private async loadSandboxClass(): Promise<SandboxClass | 'unknown' | undefined> {
    if (this.sandboxClass === undefined) await this.refreshData()
    return this.sandboxClass
  }

  /** The class for a call that depends on it; refuses a class this SDK does not know rather than guess `linux`. */
  private async requireSandboxClass(): Promise<SandboxClass> {
    const sandboxClass = await this.loadSandboxClass()
    if (sandboxClass !== undefined && sandboxClass !== 'unknown') return sandboxClass
    throw unsupportedOperationError(`unsupported sandbox class ${this.rawSandboxClass ?? '(none)'}`)
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
    return this.auth.createAccessToken(options)
  }

  private async waitForState(states: readonly SandboxWaitState[], options: SandboxStateWaitOptions = {}): Promise<void> {
    await waitForSandboxState({
      id: this.id,
      region: this.region,
      hub: this._hub,
      read: async (timeoutMs, wait, requestDeadline, exact) => {
        await this.refreshDataWithin(timeoutMs, wait, requestDeadline, exact)
        return { state: this.state ?? 'unknown', rawState: this.rawState ?? '', reason: this.error }
      },
      current: () => ({ state: this.state ?? 'unknown', rawState: this.rawState ?? '', reason: this.error }),
      observe: (outcome) => {
        this.observed.state = outcome.state
        this.observed.rawState = outcome.rawState
      },
    }, states, options)
  }

  /** Start a stopped sandbox, or resume a paused one, and wait until it is started. */
  public async start(options: LifecycleWaitOptions = {}): Promise<void> {
    assertKnownOptions('LifecycleWaitOptions', options, LIFECYCLE_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    await this.refreshData()
    if (this.state === 'started') return
    let terminalStates: readonly SandboxState[] = DEFAULT_SANDBOX_TERMINAL_STATES
    if (this.state === 'paused') {
      const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/resume`)
      this.learnEventDestination(eventDestinationFromHeaders(r.headers))
      terminalStates = RESUME_TERMINAL_STATES
    } else if (this.state === 'stopped') {
      const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/start`)
      this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    }
    await this.waitForState(['started'], { timeoutSeconds, terminalStates })
  }

  /** Stop the sandbox and wait until it is stopped. */
  public async stop(options: LifecycleWaitOptions = {}): Promise<void> {
    assertKnownOptions('LifecycleWaitOptions', options, LIFECYCLE_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/stop`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitForState(['stopped'], { timeoutSeconds })
  }

  /**
   * Pause the sandbox with its memory and wait until it is paused.
   * A return to started means the pause failed.
   */
  public async pause(options: LifecycleWaitOptions = {}): Promise<void> {
    assertKnownOptions('LifecycleWaitOptions', options, LIFECYCLE_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/pause`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitForState(['paused'], { timeoutSeconds, terminalStates: PAUSE_TERMINAL_STATES })
  }

  /**
   * Resume a paused sandbox and wait until it is started.
   * A return to stopped means the resume failed.
   */
  public async resume(options: LifecycleWaitOptions = {}): Promise<void> {
    assertKnownOptions('LifecycleWaitOptions', options, LIFECYCLE_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const r = await this.api.post(`/sandboxes/${strictUriEncode(this.id)}/resume`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitForState(['started'], { timeoutSeconds, terminalStates: RESUME_TERMINAL_STATES })
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
  public async fork(options: ForkOptions = {}): Promise<Sandbox> {
    assertKnownOptions('ForkOptions', options, FORK_KEYS, { ...RENAMED_OPTIONS, timeout: 'waitSeconds' })
    // A null waitSeconds is refused, as Python refuses None: checked on the
    // caller's value, before `null` name / memory are read as omitted.
    const waitSeconds = optionalSeconds('waitSeconds', Reflect.get(options, 'waitSeconds') as number | undefined) ?? 60
    options = withoutNullOptions(options)
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    // Every argument is checked before any request (the class read included), as in Python.
    const body: api.ForkRequest = {}
    if (options.name !== undefined) {
      if (typeof options.name !== 'string' || isBlank(options.name)) throw badRequest('fork name must be a non-empty string when given')
      body.name = options.name
    }
    if (options.memory !== undefined) {
      if (typeof options.memory !== 'boolean') throw badRequest('fork memory must be a boolean')
      body.memory = options.memory
    }
    if (options.autoDelete !== undefined && typeof options.autoDelete !== 'boolean') throw badRequest('autoDelete must be a boolean')
    // waitSeconds 0 is one request, as for create: the route is the same for
    // every class, so an unknown class is not read first; the fork then carries
    // the source's class only when this handle knows it.
    if (waitSeconds > 0) {
      const classWasKnown = this.sandboxClass !== undefined
      const initialClass = await this.requireSandboxClass()
      // Loading an unknown class already fetched the source state; waiting on a
      // known Linux source needs exactly one fresh observation before the POST.
      if (initialClass === 'linux' && classWasKnown) await this.refreshData()
      if (this.sandboxClass === undefined || this.sandboxClass === 'unknown') {
        throw unsupportedOperationError(`unsupported sandbox class ${this.rawSandboxClass ?? '(none)'}`)
      }
    }
    const sandboxClass = this.sandboxClass
    const rawSandboxClass = this.rawSandboxClass
    const sourceState = this.state
    const response = await this.api.post<api.ForkResponse>(`/sandboxes/${strictUriEncode(this.id)}/fork`, body)
    this.learnEventDestination(eventDestinationFromHeaders(response.headers))
    // Checked before the fork handle or any further request, as for a create.
    const forked = checkResponse<api.ForkResponse>(response.data, FORK_RESPONSE, 'sandbox fork')
    // An empty or blank id is no id: nothing can follow without it, as for a create.
    if (!usablePathId(forked.id)) throw badRequest('sandbox fork response is missing the sandbox id')
    const rawForkStatus: unknown = Reflect.get(forked, 'status')
    const forkStatus = typeof rawForkStatus === 'string' ? rawForkStatus : undefined
    const rawForkedFrom: unknown = Reflect.get(forked, 'forked_from')
    const forkedFrom = typeof rawForkedFrom === 'string' && rawForkedFrom !== '' ? rawForkedFrom : this.id
    const rawForkToolbox: unknown = Reflect.get(forked, 'toolbox_proxy_url')
    const forkToolbox = typeof rawForkToolbox === 'string' && rawForkToolbox !== '' ? rawForkToolbox : undefined
    // The fork's metadata comes from its detail; its toolbox from the fork
    // response, never the source's per-sandbox override.
    // 0 is one request; above the timer maximum (about 24.8 days) the fork
    // waits with no deadline, as every wait budget does.
    const waits = waitSeconds > 0
    const deadline = waitDeadline(waitSeconds)
    const fork = new Sandbox(INTERNAL, {
      sandbox: {
        sandboxId: forked.id,
        rawState: forkStatus,
        state: sandboxState(forkStatus),
        // A fork has its source's class; it names its source.
        sandboxClass,
        rawSandboxClass,
        forkedFrom,
        toolboxProxyUrl: scopedProxyToolboxUrl(forkToolbox, forked.id),
      },
      api: this.api,
      apiKey: this.apiKey,
      options: { toolboxUrlFromApi: true, autoDelete: options.autoDelete ?? false },
      requestTimeout: this.requestTimeout,
      httpOptions: this.httpOptions,
      hub: this._hub,
    })
    fork.learnEventDestination(eventDestinationFromHeaders(response.headers))
    // waitSeconds 0 is one request, as for create: no detail read, and a zero
    // budget never becomes an unbounded read.
    if (!waits) return fork
    try {
      // With no deadline the detail read is an ordinary request, bounded by the request timeout.
      const timeout = deadline === undefined ? this.api.defaults.timeout : boundedRequestTimeout(deadline, this.api.defaults.timeout)
      if (timeout === undefined) return fork
      const detail = await this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(forked.id)}`, { timeout }).then((r) => r.data)
      assertSandboxResponse(detail)
      // A detail without `forked_from` keeps the source's id the fork already names.
      const observed = metadataFromSandbox(detail)
      fork.observed = observed.forkedFrom === undefined || observed.forkedFrom === '' ? { ...observed, forkedFrom } : observed
      fork.refreshToolbox({ ...detail, toolbox_proxy_url: forkToolbox ?? detail.toolbox_proxy_url })
    } catch (error) {
      if (error instanceof DaytonaError && !(error instanceof TransferCancelledError)) return fork
      throw error
    }
    const states: readonly SandboxWaitState[] = sandboxClass === 'macos' ? ['started', 'stopped']
      : sourceState === 'started' ? ['started']
        : sourceState === 'paused' && options.memory !== false ? ['paused'] : ['stopped']
    // `timeoutSeconds: 0` is the state wait's no deadline.
    const remainingSeconds = deadline === undefined ? 0 : (deadline - Date.now()) / 1000
    if ((deadline === undefined || remainingSeconds > 0) && !states.some((state) => state === fork.state)) {
      try {
        await fork.waitForState(states, { timeoutSeconds: remainingSeconds })
      } catch (error) {
        if (!(error instanceof DaytonaError) || error instanceof TransferCancelledError) throw error
      }
    }
    return fork
  }

  /**
   * Delete the sandbox and wait until destroyed (or no longer found). An
   * `error` state throws {@link SandboxTerminalStateError}; one the events hub
   * reports is confirmed with one read first, while the wait has budget left
   * and unless it already came from a full read.
   * Returning, a `destroyed` state, or a subsequent 404 does not guarantee that
   * the image can already be deleted. Image deletion may still return 409.
   */
  public async delete(options: LifecycleWaitOptions = {}): Promise<void> {
    assertKnownOptions('LifecycleWaitOptions', options, LIFECYCLE_WAIT_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const r = await this.api.delete(`/sandboxes/${strictUriEncode(this.id)}`)
    this.learnEventDestination(eventDestinationFromHeaders(r.headers))
    await this.waitForState(['destroyed'], { timeoutSeconds, terminalStates: ['error'], missingIsDestroyed: true })
  }

  public async getNetworkPolicy(): Promise<NetworkPolicyInfo> {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const response = await this.api.get(`/sandboxes/${encodeURIComponent(this.id)}/network-policy`)
    return parseNetworkPolicyResponse(response)
  }

  public async updateNetworkPolicy(
    networkPolicy: NetworkPolicyInput,
    options: IfMatchOptions,
  ): Promise<NetworkPolicyInfo> {
    assertIfMatchOptions(options)
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const response = await this.api.put(
      `/sandboxes/${encodeURIComponent(this.id)}/network-policy`,
      serializeNetworkPolicyInput(networkPolicy),
      { headers: { 'If-Match': options.ifMatch } },
    )
    return parseNetworkPolicyResponse(response)
  }

  public async resetNetworkPolicy(options: IfMatchOptions): Promise<NetworkPolicyInfo> {
    assertIfMatchOptions(options)
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
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
    if (!this.api) throw createDaytonaError('API is not available for this rehydrated sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
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
  public async replaceSecrets(secrets: readonly string[], options: IfMatchOptions): Promise<SandboxSecretsInfo> {
    assertIfMatchOptions(options)
    if (!this.api) throw createDaytonaError('API is not available for this rehydrated sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
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
    assertOptionsObject('LifecyclePolicyOptions', options)
    assertKnownOptions('LifecyclePolicyOptions', options, LIFECYCLE_POLICY_KEYS)
    const body = lifecycleBody(options)
    if (!body) throw createDaytonaError('updateLifecyclePolicy requires at least one lifecycle field', 400, 'BAD_REQUEST', 'SDK')
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
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
    const policy = Object.freeze(parseSandboxLifecycle(checkLifecycleEcho(resp.data)))
    this.observed.lifecyclePolicy = policy
    return policy
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
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const response = await this.api
      .post<unknown>(`/sandboxes/${strictUriEncode(this.id)}/reset-idle-timer`)
      .then((r) => checkLifecycleEcho(r.data))
    const fresh = Object.freeze(parseSandboxLifecycle(response))
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
      this.observed.lifecyclePolicy = fresh
    }
    return fresh
  }

  /**
   * Wait for one or more public sandbox states.
   *
   * `timeoutSeconds` is a caller wait budget in seconds. The default is 60; `0`,
   * or a value above about 24.8 days (the timer maximum), waits without a deadline. Timeout raises {@link RequestTimeoutError}, and
   * a terminal state before the target raises {@link SandboxTerminalStateError}.
   * A terminal state the events hub reports is confirmed with one read before it raises,
   * while the wait has budget left, unless that state already came from a full read or
   * is `destroyed`. When the events hub has reported nothing decisive, the last part
   * of a bounded budget (up to 5 s) reads the state from the server, so a state the
   * hub missed still ends the wait; the same holds for the other lifecycle waits and
   * the create and fork waits.
   */
  public async wait(options: WaitOptions = {}): Promise<this> {
    assertKnownOptions('WaitOptions', options, WAIT_KEYS)
    // Only an omitted `until` defaults; `null` is refused, as Python refuses `None`.
    const until: unknown = options.until === undefined ? 'started' : options.until
    if (typeof until !== 'string' && !Array.isArray(until)) throw badRequest('until must be a sandbox wait state or an array of them')
    // SAFETY: each member is checked against the wait states by waitForState before any request.
    const states = (typeof until === 'string' ? [until] : until) as readonly SandboxWaitState[]
    await this.waitForState(states, { timeoutSeconds: options.timeoutSeconds })
    return this
  }

  /**
   * Snapshot this sandbox's filesystem as a new {@link Image}
   * (`POST /sandboxes/{id}/images`). The result boots new sandboxes
   * (`daytona.create(image.id)`); a Linux sandbox keeps running. A macOS sandbox must be persistent and
   * stopped; it cannot start until the snapshot is `ready`.
   */
  public async captureImage(options: CaptureImageOptions = {}): Promise<Image> {
    assertKnownOptions('CaptureImageOptions', options, CAPTURE_KEYS)
    const body: api.CreateImageCaptureRequest = imageOptionsBody({ name: options.name, idleExpirySeconds: options.idleExpirySeconds })
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const { data } = await this.api.post<api.AsyncOperation>(`/sandboxes/${strictUriEncode(this.id)}/images`, body)
    const operationId = acknowledgedId(data, 'image capture')
    const image = await this.api.get<api.ImageListItem>(`/images/${pathSegment(operationId, 'image id')}`).then((response) => response.data)
    return new Image(INTERNAL, image)
  }

  public async createSnapshot(name: string): Promise<Snapshot> {
    const snapshotName: unknown = name
    if (typeof snapshotName !== 'string' || isBlank(snapshotName)) throw badRequest('name must be a non-empty string')
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    // The snapshot response carries its class.
    const http = this.api
    const encodedId = strictUriEncode(this.id)
    const response = await http.post<api.CreateSnapshotResponse>(`/sandboxes/${encodedId}/snapshots`, { name })
    const operationId = acknowledgedId(response.data, 'snapshot create')
    const snapshot = await http.get<api.Snapshot>(`/snapshots/${pathSegment(operationId, 'snapshot id')}`).then((result) => result.data)
    return new Snapshot(INTERNAL, snapshot)
  }

  /** Replace the sandbox's full label set. Pass `{}` to clear all labels; resolves to the applied labels. */
  public async setLabels(labels: Record<string, string>): Promise<Readonly<Record<string, string>>> {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const body = labelsBody(labels)
    const requestConfig = { daytona: { routeTemplate: '/sandboxes/{id}/labels', sandboxId: this.id } } as AxiosRequestConfig
    const response = await this.api.put<{ labels?: unknown }>(
      `/sandboxes/${strictUriEncode(this.id)}/labels`,
      { labels: body },
      requestConfig,
    ).then((result) => checkResponse<{ readonly labels: unknown }>(result.data, { labels: 'object' }, 'sandbox labels'))
    this.observed.labels = parseLabels(response.labels, 'sandbox labels')
    return this.labels
  }

  /** Return the sandbox entrypoint log as plain text. */
  public async entrypointLogs(): Promise<string> {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const requestConfig = {
      responseType: 'text',
      daytona: { routeTemplate: '/sandboxes/{id}/logs', sandboxId: this.id },
    } as AxiosRequestConfig
    const logs: unknown = await this.api.get<string>(`/sandboxes/${strictUriEncode(this.id)}/logs`, requestConfig).then((response) => response.data)
    if (typeof logs !== 'string') throw badRequest('entrypoint logs response is not text')
    return logs
  }

  /** Update the extra environment layer used by processes created after this call. */
  public async updateEnv(env: Record<string, string> = {}, options: UpdateEnvOptions = {}): Promise<void> {
    assertKnownOptions('UpdateEnvOptions', options, UPDATE_ENV_KEYS)
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
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    return readBuildLogs(this.createApiTransport(), `/sandboxes/${strictUriEncode(this.id)}/build-logs-url`)
  }

  /**
   * Observe this sandbox's build without changing its execution lifetime.
   * Discovery failures, including a 404 for a sandbox with no image build,
   * throw BuildLogInterruptedError with the HTTP status and last cursor.
   * They do not fall back to another log source.
   */
  public async followBuildLogs(options: BuildLogFollowOptions): Promise<BuildLogFollowResult> {
    if (!this.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    return followBuildLogs(this.createApiTransport(), `/sandboxes/${strictUriEncode(this.id)}/build-logs-url`, options)
  }
}


function readSignedFileUrl(payload: api.SignedFileUrlResponse | unknown): SignedFileUrl {
  // Typed against the vendored wire model, but still checked at runtime (a
  // JSON `null` body is a valid axios `data`): a response that breaks the
  // contract is `BadRequestError` naming the field, with the Python SDK's
  // messages.
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) throw badRequest('signed file URL response is not a JSON object')
  const { signature, expires_at: expiresAt } = payload as { readonly signature?: unknown; readonly expires_at?: unknown }
  if (typeof signature !== 'string' || signature.length === 0) throw badRequest('signed file URL response signature must be a non-empty string')
  if (typeof expiresAt !== 'string' || !isRfc3339(expiresAt)) throw badRequest('signed file URL response expires_at must be an RFC 3339 timestamp')
  return { signature, expiresAt }
}
