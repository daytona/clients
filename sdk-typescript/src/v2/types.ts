/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { OtelApiLike } from './http.js'
import { optionKeys } from './options.js'
/** Public V2 SDK types. */

import type * as api from './gen/api-types.js'
import {
  BuildLogDoneReason as API_BUILD_LOG_DONE_REASONS,
  BuildLogGapReason as API_BUILD_LOG_GAP_REASONS,
  ImageRetention as API_IMAGE_RETENTIONS,
  SearchTotalRelation as API_SEARCH_TOTAL_RELATIONS,
  SnapshotKind as API_SNAPSHOT_KINDS,
  VolumeStatus as API_VOLUME_STATUSES,
  VolumeType as API_VOLUME_TYPES,
} from './gen/api-enums.js'
import {
  ComputerUseActivityStatus as TOOLBOX_COMPUTER_USE_ACTIVITY_STATUSES,
  ComputerUseDesktopFailureCode as TOOLBOX_COMPUTER_USE_DESKTOP_FAILURE_CODES,
  ComputerUseDesktopState as TOOLBOX_COMPUTER_USE_DESKTOP_STATES,
  DockerOwner as TOOLBOX_DOCKER_OWNERS,
  DockerState as TOOLBOX_DOCKER_STATES,
  KeepLogs as TOOLBOX_KEEP_LOGS,
  Kind as TOOLBOX_PROCESS_KINDS,
  PullVolumeStatus as TOOLBOX_PULL_VOLUME_STATUSES,
  Reason as TOOLBOX_PROCESS_REASONS,
  State as TOOLBOX_PROCESS_STATES,
  StdinMode as TOOLBOX_PROCESS_STDIN_MODES,
} from './gen/toolbox-enums.js'

type IsExact<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2)
    ? ((<T>() => T extends B ? 1 : 2) extends (<T>() => T extends A ? 1 : 2) ? true : false)
    : false

type AssertExact<T extends true> = T

/**
 * Configuration for the sandbox-state events hub (WebSocket-based waiting).
 *
 * `enabled` defaults to `true`. `reconcileIntervalSeconds` defaults to 60;
 * `0` disables the periodic re-subscribe timer. `idleCloseSeconds` closes the
 * events socket that long after its last waiter leaves; unset, the socket
 * stays open. In Node an idle socket does not keep the process alive, so a
 * script that never calls `daytona.close()` still exits; a runtime where the
 * socket cannot be released that way (no `ws` package) closes it after
 * 10 seconds instead. `0` keeps the socket open until `close()`.
 */
export interface SandboxEventsConfig {
  /** Enable WebSocket-based waiting; default `true`. */
  readonly enabled?: boolean
  /**
   * Reconcile timer interval in seconds; default 60; `0` disables. A negative
   * or non-finite value is a `BadRequestError`; values above about 24 days
   * clamp to that maximum.
   */
  readonly reconcileIntervalSeconds?: number
  /**
   * Close an idle events socket after this many seconds. Default: never,
   * except 10 seconds where an idle socket would keep the process alive.
   * `0` = never. A negative or non-finite value is a `BadRequestError`;
   * values above about 24 days clamp to that maximum.
   */
  readonly idleCloseSeconds?: number
}

/** The known keys of {@link SandboxEventsConfig}. */
export const SANDBOX_EVENTS_CONFIG_KEYS = optionKeys<SandboxEventsConfig>()({
  enabled: true,
  reconcileIntervalSeconds: true,
  idleCloseSeconds: true,
})

/**
 * The v2 client's configuration (built by the root `Daytona` from its own
 * config). There is no API URL field: `DAYTONA_V2_API_URL` (a regional
 * endpoint or a test environment) selects the endpoint.
 */
export interface DaytonaConfig {
  /** Bearer API key for API and sandbox requests. */
  readonly apiKey?: string
  /** Signed-in OIDC JWT exchanged lazily for a short-lived project token. */
  readonly jwtToken?: string
  /** Project id required with `jwtToken`; scopes the exchanged platform token. */
  readonly projectId?: string
  /**
   * The total time one ordinary request may take, in seconds (default 60;
   * `0`, or a value above 2,147,483.647 (about 24.8 days), means no limit).
   * Waits and long polls use their own budget, streams are
   * bounded only until their response headers arrive, and file transfers have
   * no total bound but fail once no bytes move for this long (with `0`,
   * transfers are not bounded at all).
   */
  readonly requestTimeoutSeconds?: number
  /**
   * The cap on a response body the SDK holds in memory whole, in bytes:
   * an integer up to `Number.MAX_SAFE_INTEGER`; `undefined` or `null` keep the 1 GiB default, `0` means no limit, and any other value (negative, fractional, boolean, string, too large) is a `BadRequestError`. A success response over it is refused with
   * `PayloadTooLargeError` before it is read in full; an error response keeps
   * its status's error. The in-memory file reads take it per call too.
   * Streamed bodies (`downloadFile`, log and event streams) are never capped.
   */
  readonly maxResponseBytes?: number | null
  /** Emit OpenTelemetry spans around SDK HTTP requests when the optional API package is installed. */
  readonly otelEnabled?: boolean
  /**
   * Your application's `@opentelemetry/api` module (`import * as otel from
   * '@opentelemetry/api'`). Optional in Node, where the SDK resolves the
   * optional peer itself; required for `otelEnabled` in bundled browser apps.
   */
  readonly otelApi?: OtelApiLike
  /** Node-only keep-alive socket pool size for axios-backed HTTP requests. */
  readonly connectionPoolMaxsize?: number
  /**
   * When `true` (default) all `Daytona` instances with matching connection
   * settings (API URL, credential identity, request timeout, response body
   * cap) and events settings share one `WaitHub`. When `false` each instance gets its own private hub.
   */
  readonly sharedHub?: boolean
  /** Customise the sandbox-state events hub. `undefined` uses the defaults. */
  readonly events?: SandboxEventsConfig
}

/** The known keys of the v2 {@link DaytonaConfig}. */
export const V2_CONFIG_KEYS = optionKeys<DaytonaConfig>()({
  apiKey: true,
  jwtToken: true,
  projectId: true,
  requestTimeoutSeconds: true,
  maxResponseBytes: true,
  otelEnabled: true,
  otelApi: true,
  connectionPoolMaxsize: true,
  sharedHub: true,
  events: true,
})

export interface SandboxHandleJSON {
  readonly sandboxId: string
  readonly name?: string
  readonly image?: ImageRef
  readonly snapshot?: SnapshotRef
  readonly cpu?: number
  readonly cpuMax?: number
  readonly memory?: number
  readonly memoryMax?: number
  readonly disk?: number
  readonly diskMax?: number
  readonly state?: SandboxState
  readonly rawState?: string
  readonly error?: string
  readonly imageBuildId?: string
  readonly sandboxClass?: SandboxClass | 'unknown'
  readonly rawSandboxClass?: string
  readonly labels?: Readonly<Record<string, string>>
  readonly region?: string
  readonly persistent?: boolean
  readonly spot?: boolean
  readonly spotEvictedAt?: string
  readonly lifecyclePolicy?: SandboxLifecycle
  readonly forkedFrom?: string
  readonly reservationId?: string
  readonly user?: string
}

/** @internal */
export interface SandboxInfo extends SandboxHandleJSON {
  readonly toolboxProxyUrl?: string
}

/** A process handle's serialized identity (`handle.toJSON()`), the same shape the Python SDK writes. Reconnect with `sandbox.process.connect(value)`. */
export interface ProcessHandleJSON {
  readonly sandboxId: string
  readonly processId: string
  readonly kind?: ProcessKind
}

export interface ListResult<T> {
  readonly items: readonly T[]
  readonly nextCursor?: string
}

/**
 * The result of `sandbox.fs.glob()` / `sandbox.fs.grep()`: one page (no
 * `nextCursor`) plus `skipped`, the absolute paths below the searched root
 * that the sandbox user cannot read, so they were not searched (at most 1000;
 * empty when nothing was skipped).
 */
export interface FileSearchResult<T> extends ListResult<T> {
  readonly skipped: readonly string[]
}

/**
 * One page of `GET /sandboxes/search` (`Daytona.search()`).
 *
 * `total` is exact when `totalRelation === 'eq'` and a lower bound when it is
 * `'gte'`.
 */
export interface SearchResult<T> {
  readonly items: readonly T[]
  readonly nextCursor?: string
  readonly total: number
  readonly totalRelation: SearchTotalRelation
  readonly rawTotalRelation: string
}

export type SearchTotalRelation = (typeof API_SEARCH_TOTAL_RELATIONS)[number] | 'unknown'

export type CpuArch = 'amd64' | 'arm64'
export type SandboxState =
  | 'queued'
  | 'creating'
  | 'starting'
  | 'started'
  | 'pausing'
  | 'stopping'
  | 'stopped'
  | 'paused'
  | 'destroying'
  | 'destroyed'
  | 'error'
  | 'unknown'

export type SandboxWaitState = Exclude<SandboxState, 'creating' | 'error' | 'unknown'>

/** How a macOS sandbox resumed, as reported by the API. */
export type MacBootMode = string

// This vocabulary accepts arbitrary API values.
export type ImageStatus = 'pending' | 'building' | 'ready' | 'failed' | (string & {})
export type ArtifactRetention = (typeof API_IMAGE_RETENTIONS)[number] | 'unknown'
export type SnapshotClass = 'linux' | 'unknown'
export type ImageOrigin = 'registry' | 'dockerfile' | 'capture' | (string & {})
export type SnapshotKind = (typeof API_SNAPSHOT_KINDS)[number] | 'unknown'

export type SandboxClass = 'linux' | 'macos'

export interface ImageRef {
  readonly id: string
  readonly name?: string
}

export interface SnapshotRef {
  readonly id: string
  readonly name?: string
}

/**
 * Sandbox resources; CPU is cores (1 core = 2 vCPUs), memory and disk are GiB.
 * `cpu`/`memory`/`disk` are the request: what is reserved and
 * billed as a floor. `cpuMax`/`memoryMax`/`diskMax` are the limits: the boot
 * shape and the ceiling, the system cap when omitted. Set each limit equal to
 * its request for a fixed shape. Requests below the platform minimums are
 * raised to them, so a fixed shape below a minimum is refused.
 */
export interface Resources {
  /** CPU request in cores (1 core = 2 vCPUs), fractional allowed. */
  readonly cpu?: number
  /** CPU limit in cores, a multiple of 0.5: the sandbox boots `cpuMax * 2` vCPUs. */
  readonly cpuMax?: number
  /** Memory in GiB. */
  readonly memory?: number
  /** Memory limit in GiB. */
  readonly memoryMax?: number
  /** Scratch disk in GiB. */
  readonly disk?: number
  /** Scratch disk limit in GiB. */
  readonly diskMax?: number
}

export interface MacosResources {
  readonly cpu?: number
  readonly memory?: number
  readonly disk?: number
}

export interface PortMapping {
  readonly port: number
  readonly proto?: 'tcp' | 'udp'
}

export interface NetworkPolicyInput {
  readonly blockAll?: boolean | null
  readonly allowCidrs?: readonly string[]
  readonly allowDomains?: readonly string[] | null
}

export interface VolumeMount {
  readonly volumeId: string
  readonly mountPath: string
  readonly subpath?: string
  /** Mount without write access. Only bucket volumes support `true`. */
  readonly readOnly?: boolean
}

export interface NetworkPolicy {
  readonly version: number
  readonly blockAll: boolean
  readonly allowCidrs: readonly string[]
  readonly allowDomains?: readonly string[]
}

export interface NetworkPolicyInfo {
  readonly desired?: NetworkPolicy
  readonly desiredRevision: number
  readonly etag: string
  readonly applied?: NetworkPolicy
  readonly appliedRevision?: number
  readonly appliedStartId?: string
  readonly status?: string
  readonly error?: string
}

export interface SandboxSecretsInfo {
  readonly secrets: readonly string[]
  readonly desiredRevision: number
  readonly etag: string
  readonly appliedRevision?: number
  readonly status?: string
  readonly error?: string
  readonly captureStatus?: string
  readonly captureUnresolvedCount?: number
}

/**
 * The action a lifecycle trigger fires: `onTimeout` / `onIdleTimeout`.
 *
 * `delete` is available for every sandbox; `stop` requires a persistent macOS
 * sandbox. Pause a macOS sandbox with its explicit `pause()` operation.
 */
export type LifecycleAction = 'stop' | 'pause' | 'delete'

/**
 * Clears `LifecyclePolicyOptions.idleExpirySeconds` — and ONLY that field.
 *
 * `idleExpirySeconds` value space, identical at create and on
 * `sandbox.updateLifecyclePolicy()`: omitted = unchanged / disabled (create);
 * `DISABLED` (-1) = clear the idle pair (`onIdleTimeout` with it);
 * `n > 0` = whole seconds, sent verbatim on the wire; a value
 * below 60 raised to 60 server-side and read back
 * raised; `0` and anything below -1 are rejected.
 *
 * `timeoutSeconds` does NOT accept it: the timeout cannot be disabled, so
 * `timeoutSeconds` must be `>= 1` (`-1` and `0` are rejected).
 */
export const DISABLED = -1

/**
 * Per-sandbox lifecycle policy: a wall-clock timeout and an idle timeout, each
 * paired with the action it fires.
 *
 * `timeoutSeconds` (`>= 1`, never disabled) fires `onTimeout` after that many seconds
 * after create, after an `updateLifecyclePolicy()` that supplies it, and again after
 * every accepted transition to running (start or resume) — the full budget
 * re-arms each time. Omitted `onTimeout` uses the server's default for the
 * sandbox class and persistence.
 *
 * `idleExpirySeconds` is opt-in and fires `onIdleTimeout` after that many seconds
 * without activity; `DISABLED` clears it. `onIdleTimeout` has no default —
 * arming `idleExpirySeconds` from nothing requires it.
 *
 * At Linux create, each field you omit is inherited first from the project's
 * lifecycle defaults (set in the dashboard's project settings), then — for
 * `timeoutSeconds` only — from the platform default timeout when enabled.
 * macOS uses platform defaults, not project lifecycle defaults.
 * An action follows its own interval: a
 * `timeoutSeconds` you pass without `onTimeout` gets the class default, not the
 * project's action. Pass `DISABLED` as `idleExpirySeconds` to opt out of an
 * inherited idle timeout. Project defaults apply to new sandboxes only;
 * `updateLifecyclePolicy()` never consults them.
 *
 * Rejected before posting when inapplicable to the sandbox class: see
 * `LifecycleAction` for the supported actions.
 */
export interface LifecyclePolicyOptions {
  readonly timeoutSeconds?: number
  readonly onTimeout?: LifecycleAction
  readonly idleExpirySeconds?: number
  readonly onIdleTimeout?: LifecycleAction
}

/**
 * Lifecycle policy as the API reports it.
 *
 * `timeoutSeconds` is the re-arm budget in seconds; `timeoutAt` is the
 * current absolute deadline it derives.
 * `timeoutAt`, `idleTimeoutAt`, `lastActivityAt` and `lastActivitySource` are
 * server-derived and read-only. An action the SDK does not know yet (a newer
 * server) is passed through as its wire string.
 */
export interface SandboxLifecycle {
  readonly timeoutSeconds?: number
  readonly onTimeout?: string
  readonly timeoutAt?: string
  readonly idleExpirySeconds?: number
  readonly onIdleTimeout?: string
  /**
   * When `onIdleTimeout` becomes eligible to fire:
   * `lastActivityAt + idleExpirySeconds`, server-derived and read-only.
   * Undefined when the idle pair is not armed or the sandbox has never been
   * active. Any activity — traffic, SSH, a non-PTY process or code execution
   * started through the process API that is still running (`exec`,
   * `process.start`, `runCode` and interpreter cells included), or
   * `sandbox.resetIdleTimer()` — advances `lastActivityAt` and pushes it.
   */
  readonly idleTimeoutAt?: string
  readonly lastActivityAt?: string
  readonly lastActivitySource?: string
}

/** Wire shape of the lifecycle object on sandbox DTOs and the lifecycle PATCH response. */
export interface SandboxLifecycleDto {
  timeout_seconds?: number | null
  on_timeout?: string | null
  timeout_at?: string | null
  idle_expiry_seconds?: number | null
  on_idle_timeout?: string | null
  idle_timeout_at?: string | null
  last_activity_at?: string | null
  last_activity_source?: string | null
}

export interface LifecycleWaitOptions {
  /** Wait budget in seconds; default 60; `0`, or a value above about 24.8 days, waits without a deadline. */
  readonly timeoutSeconds?: number
}

export interface ForkOptions {
  readonly name?: string
  readonly memory?: boolean
  /** Wait budget in seconds; default 60; `0` returns immediately after acceptance; a value above about 24.8 days (the timer maximum) waits with no deadline. */
  readonly waitSeconds?: number
  readonly autoDelete?: boolean
}

export interface CreateRequestBaseOptions {
  readonly name?: string
  readonly labels?: Readonly<Record<string, string>>
  readonly ports?: readonly PortMapping[]
  /**
   * `null` is accepted and means "no policy", exactly like omitting the key —
   * a JSON config round-trip produces it, and the Python SDK's `None` (the same
   * argument in the same position) already reads that way.
   */
  readonly lifecyclePolicy?: LifecyclePolicyOptions | null
  /** Region selector for this create request. */
  readonly region?: string
  readonly waitSeconds?: number
  readonly createTimeoutSeconds?: number
  /** Delete on disposal only when explicitly enabled; default false. */
  readonly autoDelete?: boolean
  /**
   * Identifies this create to the server: a second `create()` with the same
   * key and request (in the same project and API endpoint) returns the
   * sandbox the first one made instead
   * of a new one, so a create whose response was lost can be retried safely.
   * Reusing a key with changed parameters can return `ConflictError`.
   * Reuse the same parameters when retrying and use a fresh key for a
   * different sandbox. A retried create returns
   * no fresh access token; the project API key manages the sandbox as usual.
   * When omitted the SDK mints a random key per call and reports it on
   * {@link SandboxCreateRequestTimeoutError} / {@link SandboxCreateConnectionError}
   * if the request itself fails before the server answers. The key is sent
   * verbatim; the server rejects a blank key, a key longer than 256 characters,
   * or one with characters outside printable ASCII with `400 BAD_REQUEST` (it
   * travels as an HTTP header).
   */
  readonly idempotencyKey?: string
}

/**
 * Options for {@link Daytona.create}.
 *
 * **Visible resources.** Burstable sandboxes expose the burst
 * ceiling (`resources.cpuMax` / `memoryMax`, or the deployment cap when
 * omitted), so `nproc` and `/proc/meminfo` report that ceiling, not the
 * guaranteed `cpu`. Tools that size themselves from the CPU count
 * (`pytest -n auto`, `make -j$(nproc)`) start one worker per visible CPU.
 * Pass an explicit worker count when you need the guarantee instead of the
 * ceiling.
 */
export interface CreateSandboxOptions extends CreateRequestBaseOptions {
  readonly volumes?: readonly VolumeMount[]
  readonly env?: Readonly<Record<string, string>>
  readonly resources?: Resources
  readonly cpuArch?: CpuArch
  readonly cpuType?: string
  readonly secrets?: readonly string[]
  readonly spot?: boolean
  readonly user?: string
  readonly networkPolicy?: NetworkPolicyInput | null
}

export interface CreateFromBuildOptions extends CreateSandboxOptions {
  readonly idleExpirySeconds?: number
  /** Image build budget in seconds; omitted, `0` or a value above about 24.8 days waits without a deadline. */
  readonly buildTimeoutSeconds?: number
}

export interface CreateSandboxFromSnapshotOptions extends CreateRequestBaseOptions {}

/**
 * Options for a macOS create. `resources` sizes the sandbox; omitted axes use
 * the API defaults.
 * CPU selectors do not apply.
 */
export interface CreateMacosSandboxOptions extends CreateRequestBaseOptions {
  /** Read-write disk volumes. Cannot be combined with `persistent: true`. */
  readonly volumes?: readonly VolumeMount[]
  readonly env?: Readonly<Record<string, string>>
  /**
   * Project secret names to attach, as on `create`. Values are write-only and
   * restricted to each secret's allowed domains. Needs `read:secrets` permission.
   */
  readonly secrets?: readonly string[]
  /** Physical cores and GiB; omitted axes use the platform defaults. */
  readonly resources?: MacosResources
  /**
   * Keep the sandbox's disk and machine across stop and start, and its memory
   * across pause and resume. A start can run on any Mac of the organization;
   * after a start on a different Mac, iCloud asks the user to sign in again.
   * Delete erases the disk. A reservation end or a Mac failure stops a
   * persistent sandbox that has started and never deletes it; a Mac failure
   * can lose its most recent writes.
   */
  readonly persistent?: boolean
  /**
   * Which Mac reservation the sandbox runs on. Omitted: any fitting reservation of the
   * organization in the region; when none fits and the organization's
   * auto-reserve setting allows it, the server buys a reservation. `'new'`: buy a
   * reservation of the cheapest model that fits. Any other value is a reservation
   * id as `reservations.list()` returns it.
   * A reservation ID uses only that reservation. An accepted create can wait
   * for space on a Mac until `createTimeoutSeconds`.
   */
  readonly reservation?: 'new' | (string & {})
  /**
   * Only with `reservation: 'new'`: whether the bought reservation renews itself
   * each period. The server defaults it to the organization's auto-reserve
   * setting. Rejected before posting otherwise.
   */
  readonly autoRenew?: boolean
  /**
   * Network policy, as on `create`, effective from the sandbox's first boot.
   * Change it later with `sandbox.updateNetworkPolicy`.
   */
  readonly networkPolicy?: NetworkPolicyInput | null
}

export interface BuildImageOptions {
  /**
   * Makes the image named and permanent. It may not start with a resource id
   * prefix (`img_`, `snap_`, `sbx_`, `vol_`, `fn_`; `ImageNameReservedError`).
   * A name that already exists for different content or import options throws
   * `ImageNameTakenError` and changes nothing; an identical named import
   * returns the existing image (a failed one is imported again).
   */
  readonly name?: string
  /**
   * Idle-retention window in seconds for an ANONYMOUS image: the image is
   * deleted once this long has passed without a sandbox being created from it
   * (every create restarts the clock). Only without `name`. Omitted ⇒ the
   * server maximum.
   */
  readonly idleExpirySeconds?: number
  /**
   * The region, by create's rule: omitted, blank or `local` (any case, any
   * surrounding whitespace) uses the project's default region and sends no
   * `region_id`; any other value is sent as given and must be printable ASCII
   * with no leading or trailing whitespace.
   */
  readonly region?: string
  readonly cpuArch?: CpuArch
}

export interface CaptureImageOptions {
  /** Makes the image named and permanent; same rules as `BuildImageOptions.name`, and a name that already exists throws `ImageNameTakenError`. */
  readonly name?: string
  /**
   * Idle-retention window in seconds for an ANONYMOUS capture: deleted once
   * this long has passed without a sandbox being created from it. Only without
   * `name`. Same rule as `images.build`: omitted or `-1` ⇒ the server maximum
   * (7 days by default); above it ⇒ 400. Promote the image to keep it.
   */
  readonly idleExpirySeconds?: number
}

export interface WaitOptions {
  readonly until?: SandboxWaitState | readonly SandboxWaitState[]
  readonly timeoutSeconds?: number
}

export interface SandboxConnectOptions {
  /**
   * Toolbox URL already scoped to this sandbox.
   * The SDK sends this sandbox's toolbox credential there (the API key, a
   * project token, or a minted sandbox token), so pass only a URL you trust —
   * never one read from stored or user-supplied data. Omit it to use the URL
   * the API returns.
   */
  readonly toolboxUrl?: string
  /**
   * Token override for this sandbox's toolbox requests. A caller-supplied token is
   * used verbatim and never silently replaced: its expiry surfaces as a 401.
   * Without one, a handle with an API key or a JWT client's project token mints and refreshes
   * short-lived sandbox access tokens transparently.
   */
  readonly accessToken?: string
  /** Auto-delete when disposed with `await using`; defaults to false. */
  readonly autoDelete?: boolean
}

export interface SandboxDto {
  id: string
  /** Sandbox class (`linux` | `macos`); the authoritative class when present. */
  class?: SandboxClass | string
  name?: string
  image?: { readonly id: string; readonly name?: string | null } | null
  status?: string
  /** Reserved CPU (the guarantee), in cores (1 core = 2 vCPUs). */
  cpu?: number
  /** CPU limit (the boot shape), in cores, a multiple of 0.5. */
  cores_max?: number | null
  memory_mib?: number
  memory_max_mib?: number | null
  disk_mib?: number
  disk_max_mib?: number | null
  error?: string | null
  labels?: unknown
  spot?: boolean | null
  spot_evicted_at?: string | null
  toolboxProxyUrl?: string
  toolbox_proxy_url?: string | null
  region_id?: string
  image_build_id?: string | null
  user?: string | null
  snapshot?: { readonly id: string; readonly name?: string | null } | null
  /** macOS only: set on the `running` that ends a resume; null otherwise. */
  mac_boot_mode?: string | null
  /** macOS only: the reservation selected for this sandbox. `GET /sandboxes/search` items omit it. */
  reservation_id?: string | null
  /** macOS only; absent on `GET /sandboxes/batch-wait` items. */
  persistent?: boolean | null
  lifecycle_policy?: SandboxLifecycleDto | null
  /**
   * Present on `GET /sandboxes/search` items; absent
   * on the plain list. `created_at` is always set on a search item; the other
   * timestamps and `forked_from` are `null` when the sandbox has no such event.
   */
  created_at?: string | null
  updated_at?: string | null
  last_activity_at?: string | null
  started_at?: string | null
  stopped_at?: string | null
  forked_from?: string | null
  /** Per-item cursor on search results. */
  cursor?: string
}

/** @internal */
export type CreateSandboxResponse = api.CreateSandboxResponse

/** Options for {@link Sandbox.createAccessToken}. */
export interface CreateAccessTokenOptions {
  /**
   * Requested operation subset (the sandbox-operation vocabulary: `toolbox`,
   * `toolbox:exec`, `toolbox:fs`, `toolbox:git`, `toolbox:lsp`, `desktop`,
   * `ssh`, `sandbox:stop`, `sandbox:pause`, `sandbox:delete`). Omitted = the
   * minter's full grantable set; `[]` is a valid ports-only grant. Unknown
   * operations are a 400, never a silent narrowing.
   */
  readonly operations?: readonly string[]
  /** Private-preview ports the token may reach. Omitted = all; `[]` = none. */
  readonly ports?: readonly number[]
  /** Token lifetime in seconds; omitted = the deployment default, capped server-side. */
  readonly ttlSeconds?: number
}

/**
 * A short-lived per-sandbox access token (`POST …/access-token`).
 *
 * `accessToken` is a bearer for sandbox services (toolbox, SSH, desktop,
   * private previews) and for the self-service API routes named by
 * `operations`. It expires at `expiresAt` (RFC 3339) and cannot be refreshed —
 * mint a fresh one with {@link Sandbox.createAccessToken} instead. `ports` is
 * the granted private-preview port set; `undefined` means every port.
 */
export interface SandboxAccessToken {
  readonly accessToken: string
  readonly tokenType: string
  readonly expiresAt: string
  readonly operations: readonly string[]
  readonly ports?: readonly number[]
}

export type { AuditActionList, AuditActionGroup, ProjectApiKey, ProjectApiKeyContext, ProjectBudget, OrganizationLimits, UsageLimit, SandboxLimits, ImageBuildLimits, StorageLimits, SandboxResourceLimits, MemberLimits } from './accountTypes.js'

export type JsonValue = string | number | boolean | null | readonly JsonValue[] | { readonly [key: string]: JsonValue }

export interface FindByNameOptions {
  readonly region?: string
}

/** Options for `Daytona.getLimits`. */
export interface GetLimitsOptions {
  /**
   * The organization to read. Defaults to the API key's organization;
   * required when the client authenticates with `jwtToken`.
   */
  readonly organizationId?: string
}

export type SearchSort = 'created_at' | 'updated_at' | 'last_activity_at' | 'name' | 'cores' | 'memory_mib' | 'disk_mib'
export type SearchOrder = 'asc' | 'desc'
/**
 * A search bound: an RFC 3339 timestamp without a leap second (year 0001-9999,
 * a real calendar date), checked and sent exactly as written; or a `Date`,
 * sent in UTC (`toISOString()`), so its UTC instant must fall in years 0001-9999.
 */
export type SearchTimestamp = Date | string

/**
 * Filters for `Daytona.search()`.
 *
 * `states`, `sandboxClass` and `region` take a single value or a list. `states`
 * uses the SDK lifecycle names. `labels` is sent as a JSON object string
 * (containment match); numeric bounds and timestamp starts are inclusive;
 * timestamp ends are exclusive.
 */
export interface SandboxSearchFilters {
  /** Free text over name and image; also an exact match when it is a sandbox id. */
  readonly q?: string
  /** SDK lifecycle states; `creating` and `error` each match multiple server states. */
  readonly states?: Exclude<SandboxState, 'unknown'> | readonly Exclude<SandboxState, 'unknown'>[]
  /** A single value or a list (any-of). */
  readonly sandboxClass?: SandboxClass | readonly SandboxClass[]
  /** A single value or a list (any-of) over region slugs. */
  readonly region?: string | readonly string[]
  readonly imageRef?: string
  readonly imageRefPrefix?: string
  /** Containment: every listed label must match. */
  readonly labels?: Readonly<Record<string, string>>
  readonly spot?: boolean
  /** Sandboxes forked from this sandbox id. */
  readonly forkedFrom?: string
  /** Inclusive bounds on `cores_max` (the CPU limit), in cores (1 core = 2 vCPUs). */
  readonly minCores?: number
  readonly maxCores?: number
  readonly minMemoryMib?: number
  readonly maxMemoryMib?: number
  readonly minDiskMib?: number
  readonly maxDiskMib?: number
  readonly createdStartAt?: SearchTimestamp
  readonly createdEndAt?: SearchTimestamp
  readonly updatedStartAt?: SearchTimestamp
  readonly updatedEndAt?: SearchTimestamp
  readonly lastActivityStartAt?: SearchTimestamp
  readonly lastActivityEndAt?: SearchTimestamp
  /** Defaults to `created_at`. */
  readonly sort?: SearchSort
  /** Defaults to `desc`. */
  readonly order?: SearchOrder
}

/** Every `Daytona.search()` option (`GET /sandboxes/search`). */
export interface SearchSandboxesOptions extends SandboxSearchFilters {
  /** Server-side prefix on the name. */
  readonly namePrefix?: string
  /** Server-side prefix on the id. */
  readonly idPrefix?: string
  /** An integer from 1 to 200; anything else is `BadRequestError` before any request. */
  readonly limit?: number
  /** Opaque; bound to the filter + sort that produced it. */
  readonly cursor?: string
}

/** One page of `Daytona.list()`; filtering belongs to `search()`. */
export interface ListSandboxesOptions {
  readonly cursor?: string
  readonly limit?: number
}

export interface PreviewEndpoint {
  readonly port: number
  readonly public: boolean
  readonly url: string
  readonly createdAt?: string
}

/**
 * Preview URL plus an access token for one sandbox port.
 *
 * Send `token` in the `X-Daytona-Preview-Token` header to reach a private
 * endpoint; without a credential the request returns 401. When `token` is a
 * sandbox access token (a JWT) it also works as `Authorization: Bearer` or
 * `?daytona_token=`; an opaque preview token works only in the header.
 * Public endpoints set `token` to `''` and need no credential.
 *
 * For a shareable URL that works in a browser without extra headers, use
 * {@link PortsService.signedPreviewLink} instead.
 *
 * @example
 * ```ts
 * const link = await sandbox.ports.previewLink(8080)
 * const response = await fetch(link.url, {
 *   headers: { 'X-Daytona-Preview-Token': link.token },
 * })
 * ```
 */
export interface PreviewLink {
  readonly url: string
  readonly token: string
  /** Absent when the preview token has no readable expiry. */
  readonly expiresAt?: string
}

/**
 * Self-contained signed URL for one sandbox port.
 *
 * The token is embedded in the hostname — no extra headers or credentials are
 * needed. The link expires at `expiresAt` (RFC 3339). Revoke it early with
 * {@link PortsService.expireSignedPreviewLink}.
 */
export interface SignedPreviewLink {
  readonly url: string
  readonly token: string
  /** When the link stops resolving (RFC 3339). The contract requires it: a
   * response without it is a `BadRequestError`. */
  readonly expiresAt: string
}

export interface SystemMetrics {
  readonly timestamp: string
  readonly cpuCount: number
  readonly cpuUtilization: number
  readonly memoryUsageBytes: number
  readonly memoryTotalBytes: number
  readonly diskUsageBytes: number
  readonly diskTotalBytes: number
}

export type ProcessKind = 'exec' | 'pty' | 'code' | 'session'
export type ProcessReportedKind = ProcessKind | 'unknown'
export const PROCESS_KINDS = TOOLBOX_PROCESS_KINDS
type _ProcessKindsMatchContract = AssertExact<IsExact<Exclude<ProcessReportedKind, 'unknown'>, (typeof TOOLBOX_PROCESS_KINDS)[number]>>

export type ProcessState = 'starting' | 'provisioning' | 'running' | 'terminal' | 'unknown'
export const PROCESS_STATES = TOOLBOX_PROCESS_STATES
type _ProcessStatesMatchContract = AssertExact<IsExact<Exclude<ProcessState, 'unknown'>, (typeof TOOLBOX_PROCESS_STATES)[number]>>

export type ProcessSignal = 'TERM' | 'KILL' | 'INT' | 'HUP' | 'QUIT' | 'USR1' | 'USR2'
export type ProcessReason = 'exited' | 'signaled' | 'timed_out' | 'lost' | 'failed' | 'unknown'
export const PROCESS_REASONS = TOOLBOX_PROCESS_REASONS
type _ProcessReasonsMatchContract = AssertExact<IsExact<Exclude<ProcessReason, 'unknown'>, (typeof TOOLBOX_PROCESS_REASONS)[number]>>

export type KeepLogs = 'until_cleanup' | 'on_exit_ttl' | 'none'
export type ProcessKeepLogs = KeepLogs | 'unknown'
export const PROCESS_KEEP_LOGS = TOOLBOX_KEEP_LOGS
type _ProcessKeepLogsMatchContract = AssertExact<IsExact<Exclude<ProcessKeepLogs, 'unknown'>, (typeof TOOLBOX_KEEP_LOGS)[number]>>

export type ProcessStdinMode = 'closed' | 'open' | 'unknown'
export const PROCESS_STDIN_MODES = TOOLBOX_PROCESS_STDIN_MODES
type _ProcessStdinModesMatchContract = AssertExact<IsExact<Exclude<ProcessStdinMode, 'unknown'>, (typeof TOOLBOX_PROCESS_STDIN_MODES)[number]>>

export type BuildLogDoneReason = 'terminal' | 'deadline' | 'not_applicable' | 'resource_deleted' | 'unknown'
export const BUILD_LOG_DONE_REASONS = API_BUILD_LOG_DONE_REASONS
type _BuildLogDoneReasonsMatchContract = AssertExact<IsExact<Exclude<BuildLogDoneReason, 'unknown'>, (typeof API_BUILD_LOG_DONE_REASONS)[number]>>

export type BuildLogGapReason = 'truncated' | 'unknown'
export const BUILD_LOG_GAP_REASONS = API_BUILD_LOG_GAP_REASONS
type _BuildLogGapReasonsMatchContract = AssertExact<IsExact<Exclude<BuildLogGapReason, 'unknown'>, (typeof API_BUILD_LOG_GAP_REASONS)[number]>>

export type DockerState = 'not_present' | 'disabled' | 'unmanaged' | 'deferred' | 'starting' | 'ready' | 'failed' | 'unsupported' | 'unknown'
export const DOCKER_STATES = TOOLBOX_DOCKER_STATES
type _DockerStatesMatchContract = AssertExact<IsExact<Exclude<DockerState, 'unknown'>, (typeof TOOLBOX_DOCKER_STATES)[number]>>

export type DockerOwner = 'none' | 'daemon' | 'entrypoint' | 'unknown'
export const DOCKER_OWNERS = TOOLBOX_DOCKER_OWNERS
type _DockerOwnersMatchContract = AssertExact<IsExact<Exclude<DockerOwner, 'unknown'>, (typeof TOOLBOX_DOCKER_OWNERS)[number]>>

export interface DockerStatus {
  readonly owner: DockerOwner
  readonly rawOwner: string
  readonly processId?: string
  readonly reason?: string
  readonly state: DockerState
  readonly rawState: string
}
export type PullVolumeStatus = 'pulled' | 'unchanged' | 'pending' | 'unknown'
export const PULL_VOLUME_STATUSES = TOOLBOX_PULL_VOLUME_STATUSES
type _PullVolumeStatusesMatchContract = AssertExact<IsExact<Exclude<PullVolumeStatus, 'unknown'>, (typeof TOOLBOX_PULL_VOLUME_STATUSES)[number]>>

export type ComputerUseDesktopFailureCode =
  | 'desktop_exited'
  | 'addon_crashed'
  | 'start_failed'
  | 'start_interrupted'
  | 'session_ended'
  | 'session_replaced'
  | 'state_corrupt'
  | 'state_persistence_failed'
  | 'unknown'
export const COMPUTER_USE_DESKTOP_FAILURE_CODES = TOOLBOX_COMPUTER_USE_DESKTOP_FAILURE_CODES
type _ComputerUseDesktopFailureCodesMatchContract = AssertExact<IsExact<Exclude<ComputerUseDesktopFailureCode, 'unknown'>, (typeof TOOLBOX_COMPUTER_USE_DESKTOP_FAILURE_CODES)[number]>>

export type ComputerUseActivityStatus = 'active' | 'inactive' | 'unknown'
export const COMPUTER_USE_ACTIVITY_STATUSES = TOOLBOX_COMPUTER_USE_ACTIVITY_STATUSES
type _ComputerUseActivityStatusesMatchContract = AssertExact<IsExact<Exclude<ComputerUseActivityStatus, 'unknown'>, (typeof TOOLBOX_COMPUTER_USE_ACTIVITY_STATUSES)[number]>>

export type ComputerUseDesktopState = 'not_started' | 'starting' | 'ready' | 'failed' | 'unknown'
export const COMPUTER_USE_DESKTOP_STATES = TOOLBOX_COMPUTER_USE_DESKTOP_STATES
type _ComputerUseDesktopStatesMatchContract = AssertExact<IsExact<Exclude<ComputerUseDesktopState, 'unknown'>, (typeof TOOLBOX_COMPUTER_USE_DESKTOP_STATES)[number]>>

/**
 * PTY terminal dimensions, in character cells. A size you pass has `cols` and
 * `rows` each an integer from 1 to 65535 (checked before the request); a
 * Windows sandbox takes at most 32767 and refuses a larger one with
 * `BadRequestError`. A size the sandbox reports (a process's `terminal`) may be
 * any positive 32-bit integer.
 */
export interface TerminalSize {
  readonly cols: number
  readonly rows: number
}

/**
 * Process metadata, as `process.get()` / `process.list()` report it.
 * Enum-backed fields are normalized to the values this release knows plus
 * `'unknown'`; the wire value is kept on the `raw*` sibling. `signal` is a
 * bare name (`'TERM'`), like {@link WaitResult.signal}. Timestamps are
 * RFC 3339 strings.
 */
export interface ProcessInfo {
  readonly id: string
  readonly kind: ProcessReportedKind
  readonly rawKind: string
  readonly state: ProcessState
  readonly rawState: string
  readonly cwd: string
  readonly stdin: ProcessStdinMode
  readonly rawStdin: string
  readonly keepLogs: ProcessKeepLogs
  readonly rawKeepLogs: string
  readonly createdAt: string
  readonly system: boolean
  readonly name?: string
  /** The tag the process was created with; a session command carries its session's tag. Never changes. */
  readonly tag?: string
  readonly pid?: number
  readonly sessionId?: string
  readonly argv?: readonly string[]
  readonly shellCommand?: string
  readonly code?: string
  readonly language?: string
  readonly shell?: string
  readonly resolvedShell?: string
  readonly login?: boolean
  readonly user?: string
  /** Server-side deadline in seconds. */
  readonly executionTimeoutSeconds?: number
  readonly terminal?: TerminalSize
  readonly exitCode?: number
  readonly signal?: string
  readonly reason?: ProcessReason
  readonly rawReason?: string
  readonly startedAt?: string
  readonly exitedAt?: string
  readonly truncatedHead: boolean
  readonly firstAvailableCursor?: string
  readonly lastCursor?: string
}

/**
 * How a process ended, or `reason: 'timed_out'` when the wait expired first.
 * `exitCode` and `signal` are set only when the sandbox reported them; the
 * SDK never fills them in. `reason` is normalized (`'unknown'` for a value
 * this release does not know, kept on `rawReason`).
 */
export interface WaitResult {
  readonly reason: ProcessReason
  readonly exitCode?: number
  readonly signal?: string
  readonly rawReason?: string
}

/**
 * What to execute, as the first argument of `process.start()` / `process.run()` /
 * `sandbox.exec()`: a shell command string, or an object naming exactly one of
 * `cmd` (shell) or `argv` (exec, no shell — the safe form for untrusted
 * arguments). Mirrors Python's `command=` / `argv=` keywords.
 */
export type ProcessCommand = string | { readonly cmd: string; readonly argv?: never } | { readonly argv: readonly string[]; readonly cmd?: never }

/** Retention for `run()` / `session.run()` / `sandbox.exec()`: the output is read back from the log, so `'none'` is not accepted. */
export type RunKeepLogs = Exclude<KeepLogs, 'none'>

export interface ProcessStartOptions {
  readonly name?: string
  /** Groups this process for `list({ tag })` and `killAll({ tag })`: a safe identifier (`[A-Za-z0-9._-]`, 1-128 characters, not `.` or `..`). */
  readonly tag?: string
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
  readonly user?: string
  readonly shell?: string
  readonly login?: boolean
  /** `'open'` keeps stdin open for `handle.writeStdin()`; default `'closed'`. */
  readonly stdin?: StdinMode
  /** Server-side deadline in seconds; the sandbox kills the process tree when it expires. Omitted or `0` means no deadline. */
  readonly executionTimeoutSeconds?: number
  /** How long the sandbox keeps the process record and log (default `'on_exit_ttl'`). */
  readonly keepLogs?: KeepLogs
}

export interface RunOptions extends Omit<ProcessStartOptions, 'keepLogs' | 'stdin'> {
  /** Only `'closed'` (default): `run()` returns no handle to write to; use `start()` for open stdin. */
  readonly stdin?: 'closed'
  /** How long the sandbox keeps the process log (default `'on_exit_ttl'`). */
  readonly keepLogs?: RunKeepLogs
  /** Caller wait budget in seconds; omitted or `0` waits until the process ends. Expiry does not kill the process. */
  readonly timeoutSeconds?: number
}

/** Options of `pty.start()` / `pty.attach()`. */
export interface PtyStartOptions {
  /** Terminal size; 80 columns by 24 rows when omitted (see {@link TerminalSize} for the limits). */
  readonly terminal?: TerminalSize
  readonly cwd?: string
  /** Extra environment; `TERM` defaults to `xterm-256color` unless set here. */
  readonly env?: Readonly<Record<string, string>>
  readonly name?: string
  readonly user?: string
  readonly shell?: string
  readonly login?: boolean
  /** Default `'open'`. */
  readonly stdin?: StdinMode
  /** Server-side deadline in seconds; omitted or `0` means no deadline. */
  readonly executionTimeoutSeconds?: number
  /** Default `'until_cleanup'`. */
  readonly keepLogs?: KeepLogs
  /** Groups the PTY for `list({ tag })` and `killAll({ tag })` (a safe identifier, as for `tag` on start). */
  readonly tag?: string
}

export type StdinMode = 'closed' | 'open'

/** `process.run()` result: how the process ended plus its collected output. */
export interface ProcessResult extends WaitResult {
  readonly processId: string
  readonly output: string
  readonly stdout: string
  readonly stderr: string
  /** The log frames the output was collected from. */
  readonly logs: readonly LogFrame[]
}

/** One replayable process log frame. */
export interface LogFrame {
  readonly cursor: string
  readonly channel: 'stdout' | 'stderr' | 'pty' | 'setup' | (string & {})
  readonly data: string
  /** When the frame was written, in Unix milliseconds. */
  readonly timestampMs: number
}

/** One page of a process log (`logs()`). */
export interface LogPage {
  /** The frames in this page. */
  readonly items: readonly LogFrame[]
  /** The cursor to pass to the next `logs()` call to continue after this page. */
  readonly nextCursor: string
  /** Whether retention removed frames older than this page. */
  readonly truncatedHead: boolean
  /** Whether the process has ended and no frame follows this page. */
  readonly eof: boolean
  /** The cursor of the oldest frame still kept, when older ones were removed. */
  readonly firstAvailableCursor?: string
}

/** `streamLogs()`: one log frame. */
export interface StreamLogFrameEvent {
  readonly type: 'log'
  readonly cursor: string
  readonly frame: LogFrame
}

/** `streamLogs()`: the process became terminal. */
export interface StreamLogStateEvent {
  readonly type: 'state'
  readonly cursor: string
  readonly result: WaitResult
}

/** `streamLogs()`: frames before `firstAvailableCursor` were evicted. */
export interface StreamLogWarningEvent {
  readonly type: 'warning'
  readonly cursor: string
  readonly message: string
  readonly firstAvailableCursor: string
}

/** `streamLogs()`: the log is complete; the stream ends after this event. */
export interface StreamLogEofEvent {
  readonly type: 'eof'
  readonly cursor: string
}

export type StreamLogEvent = StreamLogFrameEvent | StreamLogStateEvent | StreamLogWarningEvent | StreamLogEofEvent

export interface LogsOptions {
  readonly cursor?: string
  readonly limit?: number
  /**
   * `'text'` (default) or `'base64'`: each frame's `data` is then standard
   * padded base64 (RFC 4648 section 4, `+/` alphabet with `=` padding), which
   * `Buffer.from(data, 'base64')` (Node.js) or `atob(data)` decodes.
   */
  readonly encoding?: 'text' | 'base64'
}

export interface StreamLogsOptions extends LogsOptions {
  readonly signal?: AbortSignal
}

/** Options of `process.killAll()`. */
export interface KillAllOptions {
  /** The tag whose processes to kill: a safe identifier (`[A-Za-z0-9._-]`, 1-128 characters, not `.` or `..`). */
  readonly tag: string
  /** How long to wait for them to exit, in seconds: more than 0 and at most 60 (default 10). */
  readonly timeoutSeconds?: number
}

export interface WaitProcessOptions {
  /** Caller wait budget in seconds; omitted or `0` waits until the process ends. */
  readonly timeoutSeconds?: number
}

export interface KillOptions {
  /** Default `'TERM'`. */
  readonly signal?: ProcessSignal
  /** Send `escalateTo` if the process is still running this many seconds later. */
  readonly escalateAfterSeconds?: number
  /** Default `'KILL'`. */
  readonly escalateTo?: ProcessSignal
}

export interface ProcessListOptions {
  /** Only processes created with this tag (a safe identifier, as for `tag` on start). */
  readonly tag?: string
  readonly state?: 'running' | 'terminal' | 'all'
  readonly kind?: ProcessKind
  readonly sessionId?: string
  readonly name?: string
  readonly pid?: number
}

/** One filesystem entry (`fs.list()` / `fs.stat()`). */
export interface FileInfo {
  readonly name: string
  readonly path: string
  readonly size: number
  readonly isDir: boolean
  /** Symbolic file mode, e.g. `-rw-r--r--`. */
  readonly mode: string
  /** Octal permission bits, e.g. `0644`. */
  readonly permissions: string
  readonly owner: string
  readonly group: string
  /** RFC 3339 modification time. */
  readonly modifiedAt: string
  /** The modification time as the sandbox formats it; prefer `modifiedAt`. */
  readonly modTime: string
}

/** One matching line from `fs.grep()`. */
export interface FileMatch {
  readonly file: string
  /** One-based line number. */
  readonly line: number
  readonly content: string
}

/** One file's outcome from `fs.replace()`. */
export interface ReplaceResult {
  readonly file: string
  readonly success: boolean
  readonly error?: string
}

export interface FilePermissionsOptions {
  readonly mode?: string
  readonly owner?: string
  readonly group?: string
}

export interface BulkWriteFile {
  readonly path: string
  readonly bytes: number
}

export interface BulkWriteResult {
  readonly files: readonly BulkWriteFile[]
  readonly errors: readonly string[]
}

export interface BulkReadError {
  readonly path: string
  readonly code?: string
  readonly message: string
  readonly source?: string
}

export interface BulkReadResult {
  readonly files: Readonly<Record<string, Uint8Array>>
  readonly errors: readonly BulkReadError[]
}

/** One entry of {@link GitStatus.fileStatus}. */
export interface GitFileStatus {
  readonly name: string
  readonly staging: string
  readonly worktree: string
  /** For a rename, the original path; otherwise empty. */
  readonly extra: string
}

export interface GitStatus {
  readonly currentBranch: string
  readonly ahead: number
  readonly behind: number
  readonly branchPublished: boolean
  readonly fileStatus: readonly GitFileStatus[]
}

export interface GitCommitResponse {
  readonly hash: string
}

/** One commit from `git.history()`. */
export interface GitCommit {
  readonly hash: string
  readonly message: string
  readonly author: string
  readonly email: string
  /** RFC 3339 author timestamp. */
  readonly timestamp: string
}

export type GitConfigScope = 'global' | 'local'

export interface GitRemote {
  readonly name: string
  readonly url: string
}

/**
 * Options of `interpreter.run()` / `sandbox.runCode()`. Without `context` (or
 * `isolated`) runs share kernel state per `language` (default `'python'`).
 * `context` selects an interpreter context by handle, `toJSON()` identity or
 * id; an id needs `language`. `env` applies to context-free and isolated
 * runs; a context takes its environment when it is created.
 */
export interface InterpreterRunOptions {
  /**
   * `'python'`, `'typescript'` or `'javascript'` (case-sensitive). For a
   * context-free or isolated run, any other value throws
   * `UnsupportedOperationError` (400) before a kernel is provisioned, and
   * `KernelUnavailableError` (503) means a supported language's kernel failed
   * to install or start. With a context handle or `toJSON()` identity, a
   * language that differs from the context's language throws
   * `BadRequestError` before any request; a bare context id takes `language`
   * as the context's language.
   */
  readonly language?: 'python' | 'typescript' | 'javascript' | (string & {})
  readonly context?: string | InterpreterContextReference | InterpreterContextJSON
  readonly isolated?: boolean
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
  /** Caller wait budget in seconds; omitted or `0` waits unbounded. */
  readonly timeoutSeconds?: number
}

/** Options of `context.run()`: the context fixes the language and environment. */
export interface InterpreterContextRunOptions {
  readonly cwd?: string
  /** Caller wait budget in seconds; omitted or `0` waits unbounded. */
  readonly timeoutSeconds?: number
}

/** An interpreter context's serialized identity (`context.toJSON()`), the same shape the Python SDK writes. */
export interface InterpreterContextJSON {
  readonly sandboxId: string
  readonly contextId: string
  readonly language: string
}

/** Anything that names an interpreter context (an `InterpreterContext` handle is one). */
export interface InterpreterContextReference {
  readonly sandboxId: string
  readonly contextId: string
  readonly language: string
}

export interface InterpreterContextOptions {
  /**
   * `'python'`, `'typescript'` or `'javascript'` (case-sensitive). Any other
   * value throws `UnsupportedOperationError` (400) before a kernel is
   * provisioned; `KernelUnavailableError` (503) means a supported language's
   * kernel failed to install or start.
   */
  readonly language: 'python' | 'typescript' | 'javascript' | (string & {})
  readonly contextId?: string
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
}

export interface InterpreterProcess extends WaitResult {
  /**
   * The code execution's process; absent when the run timed out while queued
   * behind earlier runs of its context and was never submitted.
   */
  readonly processId?: string
}

export interface InterpreterLogs {
  readonly stdout: string
  readonly stderr: string
}

export interface InterpreterError {
  readonly name: string
  readonly value: string
  readonly traceback: readonly string[]
}

export interface InterpreterOutput {
  readonly data: Readonly<Record<string, unknown>>
  readonly isMainResult: boolean
  readonly text?: string
  readonly html?: string
  readonly png?: string
  readonly svg?: string
  readonly json?: unknown
}

export interface InterpreterResult {
  readonly process: InterpreterProcess
  readonly results: readonly InterpreterOutput[]
  readonly logs: InterpreterLogs
  readonly error?: InterpreterError
}

/**
 * Where an image came from. `unknown` is a source kind this SDK release does
 * not know (a later API added it): `rawType` is the wire `source.type`, and the
 * source's other fields are not read. Handle it as "some other source" rather
 * than as an error.
 */
export type ImageSource =
  | { readonly type: 'registry'; readonly ref: string }
  | { readonly type: 'dockerfile'; readonly dockerfile: string }
  | { readonly type: 'named'; readonly name: string }
  | { readonly type: 'manifest'; readonly name: string }
  | { readonly type: 'unknown'; readonly rawType: string }


export type VolumeState = (typeof API_VOLUME_STATUSES)[number] | 'unknown'
export type VolumeType = (typeof API_VOLUME_TYPES)[number] | 'unknown'

export type {
  MacResources, MacModel, MacPrice, MacCatalogModel, MacCatalogRegion, MacCatalog,
  MacReservation, MacReservationStatus, MacReservationEndReason, MacReservationOrigin,
  MacReservationBatch, ListReservationsOptions, CreateReservationOptions,
  CreateReservationsBulkOptions, RenewReservationOptions, UpdateReservationOptions,
} from './macTypes.js'
