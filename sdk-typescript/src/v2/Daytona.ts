/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { assertLifecycleClassSupport, createHttpClient, createPlatformTokenProvider, DEFAULT_REQUEST_TIMEOUT_MS, gbToMib, HttpTransport, lifecycleBody, runtimeUniqueId, sandboxState, strictUriEncode } from './http.js'
import { assertKnownOptions, badRequest, boundedTimerDelay, optionalSeconds, snapshotOptions } from './options.js'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { ClientTimeoutError, ConnectionError, DaytonaError, AuthenticationError, BadRequestError, ImageBuildFailedError, RequestTimeoutError, SandboxCreateConnectionError, SandboxCreateRequestTimeoutError, SandboxTerminalStateError, createDaytonaError } from './errors/DaytonaError.js'
import { createNetworkPolicyCreateField } from './NetworkPolicy.js'
import { Image, ImageBuilder, isImageBuilder } from './Image.js'
import { Sandbox, scopedProxyToolboxUrl } from './Sandbox.js'
import type { SandboxConnectOptionsFromCreate } from './Sandbox.js'
import { ImagesService } from './Images.js'
import { SecretService } from './Secret.js'
import { ReservationsService } from './Reservations.js'
import { MacModelsService } from './MacModels.js'
import { SnapshotsService } from './Snapshots.js'
import { VolumeService } from './Volume.js'
import type * as api from './gen/api-types.js'
import { SearchTotalRelation as SEARCH_TOTAL_RELATIONS } from './gen/api-enums.js'
import { SANDBOX_EVENTS_CONFIG_KEYS, V2_CONFIG_KEYS } from './types.js'
import { knownOrUnknown } from './knownValue.js'
import { attachHub, eventDestinationFromHeaders, detachHub, eventsHubConfig } from './events.js'
import type { EventsHubConfig, WaitHub } from './events.js'
import type { BuildImageOptions, CreateMacosSandboxOptions, CreateSandboxFromSnapshotOptions, CreateSandboxOptions, CreateSandboxResponse, DaytonaConfig, GetLimitsOptions, ListResult, ListSandboxesOptions, NetworkPolicyInfo, NetworkPolicyInput, OrganizationLimits, ProjectBudget, SandboxConnectOptions, SandboxHandleJSON, SandboxSearchFilters, SandboxSecretsInfo, SearchResult, SearchSandboxesOptions, SearchTimestamp, SandboxDto } from './types.js'
import type { HttpClientOptions } from './http.js'

// `image` is optional: an imageless Linux create boots the platform default.
type LinuxCreateRequest = Omit<api.CreateSandboxRequest, 'image'> & {
  readonly image?: string
  readonly network_policy?: Record<string, unknown> | null
}


/**
 * The default Daytona Cloud v2 API, used unless `DAYTONA_V2_API_URL` (a
 * regional endpoint or a test environment) selects another one. `DAYTONA_API_URL` belongs to
 * the 0.x SDK; SDK 2.x never reads it.
 */
export const DEFAULT_V2_API_URL = 'https://api.daytona.com'

export const SEARCH_PATH = '/sandboxes/search'

const SEARCH_FILTER_KEYS: readonly (keyof SandboxSearchFilters)[] = [
  'q', 'status', 'sandboxClass', 'region', 'imageRef', 'imageRefPrefix', 'labels',
  'spot', 'forkedFrom',
  'minCores', 'maxCores', 'minMemoryMib', 'maxMemoryMib', 'minDiskMib', 'maxDiskMib',
  'createdStartAt', 'createdEndAt', 'updatedStartAt', 'updatedEndAt', 'lastActivityStartAt', 'lastActivityEndAt',
  'sort', 'order',
]

/** True when any `GET /sandboxes/search`-only filter is set (the `list()` routing rule). */
export function hasSearchFilters(options: SandboxSearchFilters): boolean {
  // `null` is "absent" like Python's `None`, so a JS caller passing it never
  // routes `list()` onto the search path with a parameter axios then drops.
  return SEARCH_FILTER_KEYS.some((key) => options[key] != null)
}

/**
 * RFC 3339 form of a search bound; `Date` values are serialized in UTC.
 *
 * An invalid `Date` (`new Date('nope')`, `new Date(NaN)`) and anything other
 * than a `Date` or a string are the SDK's own {@link BadRequestError} — never
 * the `RangeError` `toISOString()` would throw — and no request goes out.
 * Mirrors Python's `_core.search_timestamp`.
 */
export function searchTimestamp(value: SearchTimestamp): string {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) {
      throw createDaytonaError('timestamp bounds must be a valid Date or an RFC 3339 string', 400, 'BAD_REQUEST', 'SDK')
    }
    return value.toISOString()
  }
  if (typeof value === 'string') return value
  throw createDaytonaError('timestamp bounds must be a valid Date or an RFC 3339 string', 400, 'BAD_REQUEST', 'SDK')
}

// Input-boundary checks mirror Python's `_core.search_params` byte for byte:
// a bare string is one entry (the option types are `string | readonly
// string[]`, like Python's `Sequence[str]`), and — for JS callers the types
// cannot stop — non-string entries, commas and empties are the SDK's own
// BadRequestError, and no request goes out for any of them.
function commaList(name: string, values: string | readonly string[]): string {
  const items: readonly unknown[] = typeof values === 'string' ? [values] : Array.isArray(values) ? values : []
  // An empty list is not "no filter": the server reads an empty parameter as
  // absent, so `status: []` would silently become an unfiltered search.
  // Refuse it, like `['']`; a
  // non-array (JS `null`, a number) lands here too instead of a raw TypeError.
  if (items.length === 0) {
    throw createDaytonaError(`${name} must name at least one value`, 400, 'BAD_REQUEST', 'SDK')
  }
  for (const value of items) {
    if (typeof value !== 'string' || !value || value.includes(',')) {
      throw createDaytonaError(`${name} entries must be non-empty strings without commas`, 400, 'BAD_REQUEST', 'SDK')
    }
  }
  return items.join(',')
}

/** `labels` must be a plain object (Python: a `Mapping`); a string or an array is rejected before the request. */
function labelsParam(labels: unknown): string {
  if (typeof labels !== 'object' || labels === null || Array.isArray(labels)) {
    throw createDaytonaError('labels must be an object of string to string', 400, 'BAD_REQUEST', 'SDK')
  }
  if (Object.keys(labels).length === 0) {
    throw createDaytonaError('labels must name at least one pair', 400, 'BAD_REQUEST', 'SDK')
  }
  return JSON.stringify(labels)
}

function boolParam(value: boolean | undefined): string | undefined {
  return value === undefined ? undefined : value ? 'true' : 'false'
}

function timestampParam(value: SearchTimestamp | undefined): string | undefined {
  return value === undefined ? undefined : searchTimestamp(value)
}

export type SearchQueryParams = Record<string, string | number>

/** Wire query for `GET /sandboxes/search`: arrays comma-joined, labels JSON, timestamps RFC 3339, snake_case names. */
export function searchQueryParams(input: SearchSandboxesOptions): SearchQueryParams {
  // JS `null` on any option means absent, like Python's `None` (the types say
  // `undefined`; a JS caller can still pass `null`, and axios would drop the
  // parameter after the routing rule had already sent the call to search).
  const options = Object.fromEntries(
    Object.entries(input).filter(([, value]) => value != null),
  ) as SearchSandboxesOptions
  const candidates = {
    q: options.q,
    name_prefix: options.namePrefix,
    id_prefix: options.idPrefix,
    status: options.status === undefined ? undefined : commaList('status', options.status),
    class: options.sandboxClass === undefined ? undefined : commaList('sandboxClass', options.sandboxClass),
    region_ids: options.region === undefined ? undefined : commaList('region', options.region),
    image_ref: options.imageRef,
    image_ref_prefix: options.imageRefPrefix,
    labels: options.labels === undefined ? undefined : labelsParam(options.labels),
    spot: boolParam(options.spot),
    forked_from: options.forkedFrom,
    min_cores: options.minCores,
    max_cores: options.maxCores,
    min_memory_mib: options.minMemoryMib,
    max_memory_mib: options.maxMemoryMib,
    min_disk_mib: options.minDiskMib,
    max_disk_mib: options.maxDiskMib,
    created_start_at: timestampParam(options.createdStartAt),
    created_end_at: timestampParam(options.createdEndAt),
    updated_start_at: timestampParam(options.updatedStartAt),
    updated_end_at: timestampParam(options.updatedEndAt),
    last_activity_start_at: timestampParam(options.lastActivityStartAt),
    last_activity_end_at: timestampParam(options.lastActivityEndAt),
    sort: options.sort,
    order: options.order,
    limit: options.limit,
    cursor: options.cursor,
  }
  const params: SearchQueryParams = {}
  for (const [key, value] of Object.entries(candidates)) {
    if (value !== undefined) params[key] = value
  }
  return params
}

interface SearchPageDto {
  sandboxes?: unknown
  next_cursor?: string | null
  total?: number
  total_relation?: string
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * The `GET /sandboxes/search` contract is an object
 * with a `sandboxes` array: a non-object body, or a missing / non-array `sandboxes`, is the
 * SDK's own {@link BadRequestError} rather than an empty page — an `items`
 * array is NOT accepted as a stand-in. Unknown extra fields are tolerated.
 * Mirrors Python's `_core.parse_search_page`.
 */
function searchPageSandboxes(data: unknown): SandboxDto[] {
  if (!isJsonObject(data)) {
    throw createDaytonaError('expected JSON object', 400, 'BAD_REQUEST', 'SDK')
  }
  const sandboxes = (data as SearchPageDto).sandboxes
  if (!Array.isArray(sandboxes)) {
    throw createDaytonaError('search response is missing the sandboxes array', 400, 'BAD_REQUEST', 'SDK')
  }
  for (const item of sandboxes) {
    if (!isJsonObject(item)) throw createDaytonaError('expected JSON object', 400, 'BAD_REQUEST', 'SDK')
  }
  return sandboxes as SandboxDto[]
}

function env(name: string): string | undefined {
  const runtime = globalThis as {
    readonly process?: {
      readonly env?: Readonly<Record<string, string | undefined>>
    }
  }
  return runtime.process?.env?.[name]
}

function optionalNonBlankEnv(name: string): string | undefined {
  const value = env(name)
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined
}

function envFlag(name: string): boolean | undefined {
  const value = env(name)
  if (value === undefined) return undefined
  const normalized = value.trim().toLowerCase()
  return normalized === 'true' || normalized === '1'
}

function hasText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function assertPresentStringOption(name: 'apiKey' | 'jwtToken' | 'projectId', value: string | undefined): void {
  if (value !== undefined && (typeof value !== 'string' || value.length === 0)) {
    throw new BadRequestError(`${name} must be a non-empty string when provided`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

function connectionPoolMaxsize(value: number | undefined): number | undefined {
  if (value === undefined) return undefined
  if (!Number.isInteger(value) || value <= 0) {
    throw new BadRequestError('connectionPoolMaxsize must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value
}

function imageSourceString(image: unknown): string {
  if (isImageBuilder(image)) {
    throw new TypeError('daytona.create() only accepts an image name or img_… id; use daytona.createFromBuild(builder, options) for ImageBuilder sources')
  }
  if (!hasText(image)) {
    throw new TypeError('daytona.create() only accepts an image name or img_… id')
  }
  return image.trim()
}

interface LinuxCreateArguments {
  readonly source: string | undefined
  readonly options: CreateSandboxOptions
}

/**
 * Split `create`'s overloaded arguments: `()`, `(image, options?)` (an
 * `undefined` image is the platform default) or `(options)`; an options object
 * carrying `image` or `snapshot` is the v0 shape and a `TypeError`. A string goes through `imageSourceString`, so a blank one keeps
 * its error; an `ImageBuilder`, an `Image` handle or any other non-string
 * source is still a `TypeError`.
 */
function linuxCreateArguments(imageOrOptions: string | CreateSandboxOptions | null | undefined, options: CreateSandboxOptions | undefined): LinuxCreateArguments {
  if (imageOrOptions === undefined || imageOrOptions === null) return { source: undefined, options: options ?? {} }
  if (typeof imageOrOptions === 'object' && !isImageBuilder(imageOrOptions) && !(imageOrOptions instanceof Image) && !Array.isArray(imageOrOptions)) {
    if (options !== undefined) throw new TypeError('daytona.create() takes options once: create(options) or create(image, options)')
    // A v0-shaped `create({ image })` (or `{ snapshot }`) must not silently
    // drop the source and boot the platform default instead.
    if (Object.prototype.hasOwnProperty.call(imageOrOptions, 'image') || Object.prototype.hasOwnProperty.call(imageOrOptions, 'snapshot')) {
      throw new TypeError('daytona.create() takes the image as its first argument: create(image, options), not create({ image })')
    }
    return { source: undefined, options: imageOrOptions }
  }
  return { source: imageSourceString(imageOrOptions), options: options ?? {} }
}

function snapshotSourceString(snapshot: string): string {
  if (!hasText(snapshot)) throw new TypeError('snapshot must be a non-empty snapshot name or snap_… id')
  return snapshot.trim()
}

function createWaitSeconds(waitSeconds: number | undefined): number {
  const resolved = waitSeconds ?? 60
  if (!Number.isFinite(resolved) || resolved < 0 || !Number.isInteger(resolved)) {
    throw badRequest('waitSeconds must be a non-negative integer number of seconds')
  }
  return resolved
}

function createTimeoutSeconds(createTimeoutSeconds: number | undefined): number | undefined {
  if (createTimeoutSeconds === undefined) return undefined
  if (!Number.isFinite(createTimeoutSeconds) || !Number.isInteger(createTimeoutSeconds)) {
    throw badRequest('createTimeoutSeconds must be an integer number of seconds')
  }
  return createTimeoutSeconds
}

const MAX_INT32 = 2147483647

// Omitted macOS sizes use the API defaults. Supplied sizes must fit the
// supported range; GiB converts to MiB exactly, never rounded.
function macosCores(cpu: number | undefined): number | undefined {
  if (cpu === undefined) return undefined
  if (typeof cpu !== 'number' || !Number.isInteger(cpu) || cpu < 4 || cpu > MAX_INT32) {
    throw badRequest('cpu must be an integer between 4 and 2147483647')
  }
  return cpu
}

function macosMib(gib: number | undefined, axis: 'memory' | 'disk', floorMib: number): number | undefined {
  if (gib === undefined) return undefined
  const mib = typeof gib === 'number' && Number.isFinite(gib) ? gib * 1024 : Number.NaN
  if (!Number.isInteger(mib) || mib < floorMib || mib > MAX_INT32) {
    throw badRequest(`${axis} must be a finite GiB value that converts to an integer MiB value between ${floorMib} and 2147483647`)
  }
  return mib
}

function secondsToMilliseconds(timeout: number): number {
  return Math.max(0, Math.ceil(timeout * 1000))
}

/**
 * The caller's key, or `undefined` when none was given (the SDK then derives
 * one from the create's `name`, or mints a random one right before the POST —
 * minting may touch the clock on runtimes without Web Crypto, and nothing may
 * read the clock between the budget check and the request). Sent
 * verbatim: length/character rules are the server's (400).
 */
function resolveIdempotencyKey(idempotencyKey: string | undefined): string | undefined {
  if (idempotencyKey == null) return undefined
  if (typeof idempotencyKey !== 'string') throw badRequest('idempotencyKey must be a string')
  return idempotencyKey
}

/**
 * The label every `create*({ name })` stamps with the name:
 * `labels == { name, … }`. {@link Daytona.findByName} reads it back.
 */
export const NAME_LABEL = 'name'

/** Longest idempotency key the API accepts. */
const IDEMPOTENCY_KEY_MAX_LENGTH = 256
const NAME_KEY_PREFIX = 'name:'
// A 128-bit digest of the JWT for the hub identity (two independent 64-bit
// FNV-1a passes). WebCrypto's SHA-256 is async and the identity is needed
// synchronously; this only has to separate tokens within one process, and
// it retains nothing — no table of tokens outlives the clients.
const FNV_PRIME = 0x100000001b3n
const MASK_64 = 0xffffffffffffffffn
function jwtIdentity(token: string): string {
  const bytes = new TextEncoder().encode(token)
  let a = 0xcbf29ce484222325n
  let b = 0x84222325cbf29ce4n
  for (const byte of bytes) {
    a = ((a ^ BigInt(byte)) * FNV_PRIME) & MASK_64
    b = ((b ^ BigInt(byte ^ 0x5c)) * FNV_PRIME) & MASK_64
  }
  return a.toString(16).padStart(16, '0') + b.toString(16).padStart(16, '0')
}

const NAME_KEY_HASH_PREFIX = 'name:sha256:'

/**
 * The idempotency key `create*({ name })` derives when none is given.
 *
 * `'name:' + name` when that is a key the API accepts verbatim — printable
 * ASCII (`0x20`–`0x7E`), no surrounding whitespace (the API trims keys and
 * HTTP strips header padding, so a padded name would not round-trip), at most
 * 256 characters in total — else `'name:sha256:' + hex(sha256(name))` over the
 * UTF-8 bytes. The `name:` namespace keeps a derived key from ever colliding
 * with a caller's explicit key of the same spelling. Deterministic and
 * identical in the Python SDK: use it to recover or inspect the key behind
 * a named create made with either SDK.
 */
export async function idempotencyKeyForName(name: string): Promise<string> {
  if (typeof name !== 'string') throw badRequest('name must be a string')
  const literal = NAME_KEY_PREFIX + name
  // `/^[\x20-\x7e]*$/` is the API's printable-ASCII rule; `.length` counts
  // UTF-16 units, which equals the byte count for a string that passed it.
  if (name === name.trim() && literal.length <= IDEMPOTENCY_KEY_MAX_LENGTH && /^[\x20-\x7e]*$/.test(name)) return literal
  const digest = await sha256Hex(new TextEncoder().encode(name))
  return NAME_KEY_HASH_PREFIX + digest
}

// Web Crypto in both the browser and Node, without a static node:crypto import.
// The minimal structural interface is what both `subtle` objects satisfy; their full
// SubtleCrypto types are mutually incompatible (see FileSystem.ts).
interface DigestSubtle {
  digest(algorithm: 'SHA-256', data: Uint8Array): Promise<ArrayBuffer>
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  // globalThis.crypto first (browsers, Node >= 19). The Node-only fallback uses
  // a runtime specifier, like filesystemTransfers.ts: a literal
  // `import('node:crypto')` makes browser bundlers (esbuild --platform=browser)
  // fail to resolve it even though that branch never runs there.
  const cryptoSpecifier = 'node:crypto'
  const subtle: DigestSubtle = globalThis.crypto?.subtle !== undefined
    ? (globalThis.crypto.subtle as unknown as DigestSubtle)
    : (
        (await (import(/* @vite-ignore */ /* webpackIgnore: true */ cryptoSpecifier) as Promise<{
          readonly webcrypto: { readonly subtle: unknown }
        }>)).webcrypto.subtle as DigestSubtle
      )
  const digest = await subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * The create's name when it names the sandbox (non-blank), else `undefined`.
 * Mirrors the API, which generates a name for a blank one: a blank name is no
 * name, so it neither stamps the label nor derives the key.
 */
function namedCreate(name: string | undefined): string | undefined {
  return typeof name === 'string' && name.trim() !== '' ? name : undefined
}

/**
 * The create's labels with the name stamped as `labels.name`. An explicit
 * `labels.name` that disagrees with `name` is a contradiction the SDK refuses
 * rather than resolves silently: the label IS the name {@link Daytona.findByName}
 * matches on.
 */
function labelsWithName(name: string | undefined, labels: Record<string, string> | undefined): Record<string, string> | undefined {
  const named = namedCreate(name)
  if (named === undefined) return labels
  const existing = labels?.[NAME_LABEL]
  if (existing !== undefined && existing !== named) {
    throw badRequest(`labels.${NAME_LABEL} (${JSON.stringify(existing)}) disagrees with name (${JSON.stringify(named)}); the name label is stamped from name, pass one or the other`)
  }
  return { ...(labels ?? {}), [NAME_LABEL]: named }
}

const DETAIL_FALLBACK_CONCURRENCY = 8

export class Daytona {
  public readonly images: ImagesService
  public readonly snapshots: SnapshotsService
  public readonly volume: VolumeService
  public readonly secret: SecretService
  public readonly reservations: ReservationsService
  /** The Mac model registry that `reservations.create({ model })` draws from. */
  public readonly macModels: MacModelsService
  private readonly api: AxiosInstance
  private readonly apiTransport: HttpTransport
  private readonly apiKey?: string
  private keyOrganization?: Promise<string>
  private readonly apiUrl: string
  private readonly httpOptions: HttpClientOptions
  private readonly hubShared: boolean
  private readonly hubEventsConfig: EventsHubConfig
  private readonly hubIdentityKey: string
  private hub: WaitHub | undefined
  private closed = false

  constructor(config: DaytonaConfig = {}) {
    // Unsupported options must fail loudly for plain-JS callers (TypeScript callers
    // get a compile error), as Python's v2 config rejects them.
    for (const unsupported of ['apiUrl', 'target', 'toolboxUrl']) {
      if (unsupported in config) {
        throw badRequest(
          unsupported === 'apiUrl'
            ? "the v2 client does not take 'apiUrl': set DAYTONA_V2_API_URL to select an API endpoint"
            : unsupported === 'target'
              ? "the v2 client does not take 'target': pass 'region' to each create"
              : "the v2 client does not take 'toolboxUrl': each sandbox carries its own toolbox URL",
        )
      }
    }
    assertKnownOptions('DaytonaConfig', config, V2_CONFIG_KEYS)
    const events = snapshotOptions('SandboxEventsConfig', config.events, SANDBOX_EVENTS_CONFIG_KEYS)
    // Only DAYTONA_V2_API_URL selects the endpoint (DAYTONA_API_URL belongs to
    // the 0.x SDK); read through the computed-key env lookup (bundler-safe).
    // Ambient values are trimmed and a blank one is unset; a slash-only URL
    // is unset too.
    const apiUrl = optionalNonBlankEnv('DAYTONA_V2_API_URL')?.replace(/\/+$/, '') || DEFAULT_V2_API_URL
    // Explicit values are used as given; an explicitly empty one is an error,
    // never "unset". An explicit credential wins over the ambient environment:
    // config.jwtToken suppresses DAYTONA_API_KEY and config.apiKey suppresses
    // DAYTONA_JWT_TOKEN, so an exported key never collides with the credential
    // the caller chose.
    assertPresentStringOption('apiKey', config.apiKey)
    assertPresentStringOption('jwtToken', config.jwtToken)
    assertPresentStringOption('projectId', config.projectId)
    const apiKey = config.apiKey ?? (config.jwtToken !== undefined ? undefined : optionalNonBlankEnv('DAYTONA_API_KEY'))
    const jwtToken = config.jwtToken ?? (config.apiKey !== undefined ? undefined : optionalNonBlankEnv('DAYTONA_JWT_TOKEN'))
    const projectId = config.projectId ?? optionalNonBlankEnv('DAYTONA_PROJECT_ID')
    const requestTimeoutSeconds = optionalSeconds('requestTimeoutSeconds', config.requestTimeoutSeconds)
    // Above the timer maximum (about 24.8 days) means no bound, like 0.
    const requestTimeoutMs = requestTimeoutSeconds === undefined ? DEFAULT_REQUEST_TIMEOUT_MS : boundedTimerDelay(requestTimeoutSeconds * 1000)
    const otelEnabled = config.otelEnabled ?? envFlag('DAYTONA_OTEL_ENABLED') ?? false
    const poolMaxsize = connectionPoolMaxsize(config.connectionPoolMaxsize)
    if (!apiKey && !jwtToken) {
      throw new AuthenticationError('configure apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
    }
    if (apiKey && jwtToken) {
      throw new BadRequestError('configure exactly one of apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    if (jwtToken && !projectId) {
      throw new BadRequestError('projectId is required when jwtToken is set (set config.projectId or DAYTONA_PROJECT_ID)', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    this.apiKey = apiKey ?? undefined
    const base = apiUrl.replace(/\/+$/, '') || DEFAULT_V2_API_URL
    this.apiUrl = base
    // Hub registry identity: the API key, or for JWT auth the project plus an
    // opaque per-token id. The token must be part of it — two users' JWTs for
    // one project must not share a hub that mints with the first one's
    // credentials — but the registry key never contains the raw JWT.
    this.hubIdentityKey = apiKey ?? (jwtToken && projectId ? `jwt:${projectId}:${jwtIdentity(jwtToken)}` : '')
    this.httpOptions = {
      authProvider: jwtToken && projectId ? createPlatformTokenProvider({
        baseURL: base,
        jwtToken,
        projectId,
        requestTimeout: requestTimeoutMs,
        otelEnabled,
        otelApi: config.otelApi,
      }) : undefined,
      connectionPoolMaxsize: poolMaxsize,
      otelEnabled,
      otelApi: config.otelApi,
    }
    this.apiTransport = new HttpTransport(base, this.apiKey, requestTimeoutMs, this.httpOptions)
    this.api = createHttpClient(base, this.apiKey, requestTimeoutMs, this.httpOptions)
    this.images = new ImagesService(this.api, this.apiTransport)
    this.snapshots = new SnapshotsService(this.api)
    this.volume = new VolumeService(this.api)
    this.secret = new SecretService(this.api)
    this.reservations = new ReservationsService(this.api)
    this.macModels = new MacModelsService(this.api)
    this.hubShared = config.sharedHub ?? true
    this.hubEventsConfig = eventsHubConfig(events)
    credentialSafeDiagnostics(this, () => ({ type: 'DaytonaV2' }))
  }

  private acquireHub(): WaitHub | undefined {
    if (this.closed) return undefined
    if (this.hub?.closed) this.hub = undefined
    if (this.hub === undefined) {
      this.hub = attachHub({
        apiUrl: this.apiUrl,
        apiKey: this.hubIdentityKey,
        // v2 has no client-level target (region is per create): not a key part.
        target: undefined,
        shared: this.hubShared,
        config: this.hubEventsConfig,
        _api: this.api,
      })
    }
    return this.hub
  }

  public async close(): Promise<void> {
    this.closed = true
    const hub = this.hub
    if (hub !== undefined) {
      this.hub = undefined
      await detachHub(hub, {
        apiUrl: this.apiUrl,
        apiKey: this.hubIdentityKey,
        // v2 has no client-level target (region is per create): not a key part.
        target: undefined,
        shared: this.hubShared,
      })
    }
  }

  /** Return the actions the audit log can record for each target type. */
  public async listAuditActions(): Promise<api.AuditActionList> {
    return this.api.get<api.AuditActionList>('/audit/actions').then((response) => response.data)
  }

  /**
   * Describe the API key this client authenticates with: its name, masked
   * display parts, the project and organization it acts in, its permissions,
   * expiry and last use. The token itself is never returned.
   */
  public async currentApiKey(): Promise<api.ProjectApiKeyWithContext> {
    return this.api.get<api.ProjectApiKeyWithContext>('/api-keys/current').then((response) => response.data)
  }

  /**
   * Return the monthly spend budget of the API key's project. An unset budget
   * has nullable limit and period fields. The key needs `read:spending`.
   * Spend is calculated at list price, before credits, and lags usage by
   * several minutes. While the project is over its budget, creating or
   * starting a sandbox in it throws `ProjectBudgetExceededError`.
   */
  public async getProjectBudget(): Promise<ProjectBudget> {
    return this.api.get<ProjectBudget>('/usage/budget').then((response) => response.data)
  }

  /**
   * Return the organization's limits and current usage. A null `limit` means
   * unlimited. With an API key the organization defaults to the key's own,
   * looked up once and cached; with `jwtToken` authentication pass
   * `organizationId`. The credential needs `read:limits`. Member counts
   * (`members`) are not included: they are served to signed-in members only.
   */
  public async getLimits(options: GetLimitsOptions = {}): Promise<OrganizationLimits> {
    const organizationId = options.organizationId ?? await this.keyOrganizationId()
    if (organizationId.length === 0) {
      throw new BadRequestError('organizationId must be a non-empty string', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    return this.api
      .get<OrganizationLimits>(`/orgs/${strictUriEncode(organizationId)}/limits`)
      .then((response) => response.data)
  }

  // One lookup shared by concurrent first calls; a failed lookup is dropped
  // so the next call retries it.
  private keyOrganizationId(): Promise<string> {
    if (!this.apiKey) {
      return Promise.reject(
        new BadRequestError('organizationId is required with jwtToken authentication', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' }),
      )
    }
    this.keyOrganization ??= this.currentApiKey().then(
      (key) => key.context.org_id,
      (error: unknown) => {
        this.keyOrganization = undefined
        throw error
      },
    )
    return this.keyOrganization
  }

  /**
   * Create a Linux sandbox from a registered image name or `img_…` id.
   *
   * Omitting the image (`create()` or `create(options)`) boots the platform
   * default image, the Linux image Daytona marks as default
   * (`images.list({ platform: 'true' })` shows it with `default: true`). When
   * no default is configured the create fails with
   * `DEFAULT_IMAGE_NOT_CONFIGURED` (400). An empty or whitespace-only image is
   * still an error, thrown before any request.
   *
   * When Linux sandboxes are not enabled for the organization, the API answers
   * `403` `FEATURE_NOT_ENABLED` (thrown as `FeatureNotEnabledError`).
   */
  public async create(): Promise<Sandbox>
  public async create(image: string | undefined, options?: CreateSandboxOptions): Promise<Sandbox>
  public async create(options: CreateSandboxOptions): Promise<Sandbox>
  public async create(imageOrOptions?: string | CreateSandboxOptions, maybeOptions?: CreateSandboxOptions): Promise<Sandbox> {
    const { source, options } = linuxCreateArguments(imageOrOptions, maybeOptions)
    const route = 'linux'
    const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateSandboxRequest['lifecycle_policy']
    assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: route, persistent: false })
    const body: LinuxCreateRequest = {
      name: options.name,
      ...(source !== undefined ? { image: source } : {}),
      env: options.env ?? options.envVars,
      region_id: this.createRegion(options),
      cpu: options.cpu ?? options.resources?.cpu,
      cores_max: options.resources?.cpuMax,
      memory_mib: options.memory !== undefined ? gbToMib(options.memory) : options.resources?.memory !== undefined ? gbToMib(options.resources.memory) : undefined,
      memory_max_mib: options.resources?.memoryMax !== undefined ? gbToMib(options.resources.memoryMax) : undefined,
      disk_mib: options.disk !== undefined ? gbToMib(options.disk) : options.resources?.disk !== undefined ? gbToMib(options.resources.disk) : undefined,
      disk_max_mib: options.resources?.diskMax !== undefined ? gbToMib(options.resources.diskMax) : undefined,
      ports: options.ports?.map((port) => ({ port: port.port, proto: port.proto ?? 'tcp' })),
      volumes: options.volumes?.map((volume) => ({ volume: volume.volumeId, mount_path: volume.mountPath, subpath: volume.subpath, read_only: volume.readOnly === true ? true : undefined })),
      secrets: options.secrets,
      labels: labelsWithName(options.name, options.labels),
      spot: options.spot,
      lifecycle_policy: lifecyclePolicy,
      network_policy: createNetworkPolicyCreateField(options as Record<string, unknown>),
      cpu_arch: options.cpuArch,
      cpu_type: options.cpuType,
      user: options.user,
      wait_seconds: createWaitSeconds(options.waitSeconds),
      create_timeout_seconds: createTimeoutSeconds(options.createTimeoutSeconds),
      idempotency_key: resolveIdempotencyKey(options.idempotencyKey),
    }
    return this.createSandbox('/sandboxes', body, { autoDelete: true, image: source })
  }

  /**
   * Create a sandbox from a snapshot.
   *
   * When Linux sandboxes are not enabled for the organization, the API answers
   * `403` `FEATURE_NOT_ENABLED` (thrown as `FeatureNotEnabledError`).
   */
  public async createFromSnapshot(snapshot: string, options: CreateSandboxFromSnapshotOptions = {}): Promise<Sandbox> {
    const source = snapshotSourceString(snapshot)
    const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateSandboxFromSnapshotRequest['lifecycle_policy']
    assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: 'linux', persistent: false })
    const body: api.CreateSandboxFromSnapshotRequest = {
      name: options.name,
      snapshot: source,
      labels: labelsWithName(options.name, options.labels),
      lifecycle_policy: lifecyclePolicy,
      ports: options.ports?.map((port) => ({ port: port.port, proto: port.proto ?? 'tcp' })),
      region_id: this.createRegion(options),
      wait_seconds: createWaitSeconds(options.waitSeconds),
      create_timeout_seconds: createTimeoutSeconds(options.createTimeoutSeconds),
      idempotency_key: resolveIdempotencyKey(options.idempotencyKey),
    }
    return this.createSandbox('/sandboxes/from-snapshot', body, { autoDelete: true, snapshot: source })
  }

  /**
   * Create a macOS sandbox (`POST /sandboxes/macos`) on a Mac the organization
   * has reserved (`daytona.reservations`), from a macOS image name.
   * `reservation` picks the Mac reservation: omitted uses any fitting reservation of the
   * organization in the region (or lets the organization's auto-reserve buy
   * one), `'new'` buys a reservation of the cheapest model that fits, and a
   * reservation id targets that reservation at claim. With no reservation to use it fails
   * with `ConflictError` (`NO_ACTIVE_RESERVATION`, `NO_FREE_SLOT`,
   * `MAC_DISK_FULL`, `AUTO_RESERVE_LIMIT_REACHED`, `NO_MAC_AVAILABLE`).
   * `cpu` (cores), `memory` (GiB) and `disk` (GiB) size the sandbox; each omitted
   * axis uses half of the claiming Mac, rounded down, on a model that runs two sandboxes,
   * or all of it on a model that runs one. The first successful
   * claim fixes the size, which the sandbox reports from then on. `name`,
   * `idempotencyKey`, `waitSeconds` and `createTimeoutSeconds` behave as on
   * {@link create}. `networkPolicy` sets the outbound network policy as on
   * {@link create}; a sandbox with a policy runs only on a Mac that can
   * enforce it.
   *
   * Creating macOS sandboxes must be enabled for the organization;
   * otherwise the API answers `403` `FEATURE_NOT_ENABLED`
   * (thrown as `FeatureNotEnabledError`).
   *
   * Find regions and their regional API endpoints with `GET /regions`;
   * discover Mac models and prices with `macModels.catalog({ region: '<region>' })`.
   * To select a regional API endpoint, set `DAYTONA_V2_API_URL` before creating
   * the client. An API that does not serve the region answers `ConflictError`;
   * the SDK does not reroute.
   */
  public async createMacos(image: string, options: CreateMacosSandboxOptions = {}): Promise<Sandbox> {
    const source = imageSourceString(image)
    if (options.autoRenew !== undefined && options.reservation !== 'new') {
      throw badRequest('macos create takes autoRenew only with reservation: "new"')
    }
    const cores = macosCores(options.cpu)
    const memoryMib = macosMib(options.memory, 'memory', 4096)
    const diskMib = macosMib(options.disk, 'disk', 81920)
    // SAFETY: lifecycleBody validates and serializes the shared lifecycle
    // fields used by CreateMacosSandboxRequest.
    const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateMacosSandboxRequest['lifecycle_policy']
    assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: 'macos', persistent: options.persistent === true })
    const body: api.CreateMacosSandboxRequest = {
      name: options.name,
      image: source,
      env: options.env ?? options.envVars,
      secrets: options.secrets,
      region_id: this.createRegion(options),
      ports: options.ports?.map((port) => ({ port: port.port, proto: port.proto ?? 'tcp' })),
      labels: labelsWithName(options.name, options.labels),
      persistent: options.persistent,
      volumes: options.volumes?.map((volume) => ({ volume: volume.volumeId, mount_path: volume.mountPath, subpath: volume.subpath, read_only: volume.readOnly === true ? true : undefined })),
      cores,
      memory_mib: memoryMib,
      disk_mib: diskMib,
      reservation: options.reservation,
      auto_renew: options.autoRenew,
      lifecycle_policy: lifecyclePolicy,
      wait_seconds: createWaitSeconds(options.waitSeconds),
      create_timeout_seconds: createTimeoutSeconds(options.createTimeoutSeconds),
      idempotency_key: resolveIdempotencyKey(options.idempotencyKey),
      // SAFETY: options is a plain create-options object; the helper checks
      // property presence and validates networkPolicy before serializing it.
      network_policy: createNetworkPolicyCreateField(options as Record<string, unknown>),
    }
    return this.createSandbox('/sandboxes/macos', body, { autoDelete: options.persistent !== true, image: source, sandboxClass: 'macos' })
  }

  /**
   * Build an anonymous image, wait for it, then create the sandbox from it.
   * `region` selects the region for BOTH legs: a Dockerfile-built image lives
   * in one region and can only boot there, so it is built where the
   * sandbox will run. `BuildImageOptions.region` is therefore not accepted
   * here — pass `region`.
   */
  public async createFromBuild(builder: ImageBuilder, options: CreateSandboxOptions & Omit<BuildImageOptions, 'region'> = {}): Promise<Sandbox> {
    if (!isImageBuilder(builder)) throw new TypeError('createFromBuild expects an ImageBuilder')
    if ('target' in (options as Record<string, unknown>)) throw new TypeError("createFromBuild does not take 'target'; use 'region' (it selects the region for both the image build and the sandbox)")
    const region = this.createRegion(options)
    const wait = createWaitSeconds(options.waitSeconds)
    const deadline = wait === 0 ? undefined : Date.now() + secondsToMilliseconds(wait)
    const buildOptions: BuildImageOptions = { region }
    if (options.idleExpirySeconds !== undefined) buildOptions.idleExpirySeconds = options.idleExpirySeconds
    if (options.cpuArch !== undefined) buildOptions.cpuArch = options.cpuArch
    const image = await this.images.build(builder, buildOptions)
    const readyImage = await this.waitForBuiltImage(image.id, deadline)
    const createOptions: CreateSandboxOptions = { ...options }
    if (deadline !== undefined) {
      const remainingWait = Math.ceil((deadline - Date.now()) / 1000)
      if (remainingWait <= 0) throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      createOptions.waitSeconds = remainingWait
    }
    return this.create(readyImage.id, createOptions)
  }

  private async waitForBuiltImage(imageId: string, deadline: number | undefined): Promise<Image> {
    for (;;) {
      const remainingTimeout = deadline === undefined ? 0 : (deadline - Date.now()) / 1000
      if (deadline !== undefined && remainingTimeout <= 0) {
        throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      }
      try {
        const image = await this.images.get(imageId, { wait: 'ready', timeout: remainingTimeout })
        if (image.status === 'ready') return image
        if (image.status === 'failed') throw new ImageBuildFailedError(image.error ?? `image ${image.id} build failed`, { statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: image.id, buildError: image.error ?? undefined })
      } catch (error) {
        if (error instanceof DaytonaError && error.code === 'REQUEST_TIMEOUT' && deadline !== undefined && Date.now() >= deadline) {
          throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
        }
        throw error
      }
    }
  }

  private createRegion(options: { readonly region?: string }): string | undefined {
    // v2 uses `region`, not v0's `target` (TypeScript callers get a compile error);
    // plain-JS callers must not have it dropped silently, landing the sandbox
    // in the default region. Python rejects it too.
    if ('target' in options) {
      throw new TypeError("v2 does not take 'target'; pass 'region' instead")
    }
    const resolved = options.region
    return resolved && resolved.trim().toLowerCase() !== 'local' ? resolved : undefined
  }

  private async createSandbox(path: string, body: { name?: string | null; wait_seconds?: number | null; region_id?: string | null; idempotency_key?: string | null }, metadata: { autoDelete: boolean; image?: string; snapshot?: string; sandboxClass?: 'macos' }): Promise<Sandbox> {
    const wait = createWaitSeconds(body.wait_seconds ?? undefined)
    const region = typeof body.region_id === 'string' ? body.region_id : undefined
    // Explicit key, else the name-derived one: a repeated
    // `create({ name })` is a get-or-create, else a random one per call.
    const named = namedCreate(body.name ?? undefined)
    const idempotencyKey = typeof body.idempotency_key === 'string' ? body.idempotency_key : named !== undefined ? await idempotencyKeyForName(named) : runtimeUniqueId()
    if (body.idempotency_key === undefined) body.idempotency_key = idempotencyKey
    // The create response first associates the sandbox id with its regional
    // API endpoint for the events hub.
    let createdEventDestination: string | undefined
    const created = await this.api.post<CreateSandboxResponse>(path, body, {
      headers: { 'Idempotency-Key': idempotencyKey, ...(region ? { 'x-daytona-region': region } : {}) },
    }).then((response) => {
      createdEventDestination = eventDestinationFromHeaders(response.headers) || undefined
      return response.data
    }).catch((error: unknown) => {
      if (error instanceof ConnectionError) throw new SandboxCreateConnectionError(idempotencyKey, error.message)
      if (error instanceof ClientTimeoutError) throw new SandboxCreateRequestTimeoutError(idempotencyKey)
      throw error
    })
    // The create response's token is refreshable (unlike a caller-supplied
    // one); an opaque token may have no known expiry.
    const connectOptions: SandboxConnectOptionsFromCreate = {
      accessToken: created.access_token ?? undefined,
      accessTokenFromCreate: true,
      accessTokenExpiresAt: created.access_token_expires_at ?? undefined,
      autoDelete: metadata.autoDelete,
    }
    // A handle info, not a partial DTO: a DTO marks every field as known and
    // fills the absent ones (memory/disk 1, labels {}), which toJSON() would
    // then persist. The info carries only the id and the scoped toolbox URL.
    const sandbox = this.buildSandbox({
      sandboxId: created.id,
      toolboxProxyUrl: scopedProxyToolboxUrl(created.toolbox_proxy_url, created.id),
    }, connectOptions)
    sandbox.learnEventDestination(createdEventDestination)
    sandbox.name = created.name
    sandbox.rawState = created.status ?? undefined
    sandbox.state = sandboxState(created.status)
    sandbox.accessToken = created.access_token ?? undefined
    sandbox.target = region ?? 'local'
    // The response's `image` ({id, name}) is canonical for every create — a
    // named create resolves to its `img_` id, an imageless create learns the
    // default it booted. An omitted image falls back to the input-derived
    // ref (an `img_` id input; a name stays unset).
    // SAFETY: every field is optional and re-checked with typeof below, so an
    // absent or malformed `image` only takes the fallback.
    const booted = (created as { image?: { id?: unknown; name?: unknown } | null }).image
    if (typeof booted?.id === 'string' && booted.id.startsWith('img_')) {
      sandbox.image = { id: booted.id, name: typeof booted.name === 'string' ? booted.name : null }
    } else if (metadata.image?.startsWith('img_')) {
      sandbox.image = { id: metadata.image, name: null }
    }
    if (metadata.snapshot?.startsWith('snap_')) sandbox.snapshot = { id: metadata.snapshot, name: null }
    if (metadata.sandboxClass) sandbox.sandboxClass = metadata.sandboxClass
    // Preserve the reservation even when create returns an already-running sandbox.
    sandbox.reservationId = created.reservation_id ?? undefined
    const waitCap = typeof created.wait_cap_seconds === 'number' && Number.isFinite(created.wait_cap_seconds) ? Math.max(0, created.wait_cap_seconds) : 0
    if (wait === 0 || sandbox.state === 'started') return sandbox
    const remainingWait = Math.max(0, wait - waitCap)
    if (remainingWait <= 0) return sandbox
    try {
      return await sandbox.wait({ until: 'started', timeout: remainingWait })
    } catch (error) {
      if (error instanceof RequestTimeoutError || error instanceof SandboxTerminalStateError) return sandbox
      throw error
    }
  }

  // Every caller passes API data (create response, get/list sandbox details)
  // or a bare id, so the input's toolbox URL is trusted here — and only here.
  private buildSandbox(sandboxOrId: SandboxDto | SandboxHandleJSON | string, options: SandboxConnectOptions = {}): Sandbox {
    const fromApi: SandboxConnectOptionsFromCreate = { ...options, toolboxUrlFromApi: true }
    return new Sandbox(sandboxOrId, this.api, undefined, this.apiKey, undefined, fromApi, this.api.defaults.timeout, this.httpOptions, this.acquireHub())
  }

  private async resolveSandboxToolboxSource<T extends SandboxDto>(sandbox: T, timeout?: number): Promise<T | api.Sandbox> {
    if (hasText(sandbox.toolbox_proxy_url) || hasText(sandbox.toolboxProxyUrl)) return sandbox
    const config = timeout === undefined ? undefined : { timeout }
    // One unreadable item must not fail the page: the handle keeps no toolbox
    // and says so with the explicit error on first toolbox use.
    return this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(sandbox.id)}`, config).then((response) => response.data, () => sandbox)
  }

  /** Items lacking a toolbox URL each need a detail read: bounded concurrency. */
  private async sandboxesFromDtos(items: readonly SandboxDto[]): Promise<Sandbox[]> {
    const out: Sandbox[] = new Array(items.length)
    let next = 0
    const worker = async (): Promise<void> => {
      for (let i = next++; i < items.length; i = next++) out[i] = await this.sandboxFromDto(items[i])
    }
    await Promise.all(Array.from({ length: Math.min(DETAIL_FALLBACK_CONCURRENCY, items.length) }, worker))
    return out
  }

  private async sandboxFromDto(sandbox: SandboxDto, options: SandboxConnectOptions = {}, timeout?: number): Promise<Sandbox> {
    const source = await this.resolveSandboxToolboxSource(sandbox, timeout)
    return this.buildSandbox(source, options)
  }

  /**
   * Find the sandbox created with `{ name }`.
   *
   * Uses the exact `labels.name` value every `create*({ name })` stamps. Names
   * are not unique, so this is an array: a get-or-create caller takes `[0]`
   * when it is non-empty and calls `create(image, { name })` otherwise — and
   * because that create derives the same key as the first one, a lost race
   * between two such callers still yields one sandbox. Sandboxes that are
   * `deleted` or `deleting` are left out. The lookup goes through sandbox
   * search, where a new sandbox appears shortly after its create, and a
   * deployment without search answers {@link SearchNotConfiguredError} (501).
   *
   * `region` scopes the read the way it scopes a create; omit it or pass
   * `'local'` to use the default region.
   */
  public async findByName(name: string, options: { region?: string } = {}): Promise<Sandbox[]> {
    if (typeof name !== 'string' || name.trim() === '') throw badRequest('name must be a non-blank string')
    const region = this.createRegion(options)
    const found: Sandbox[] = []
    const seen = new Set<string>()
    let cursor: string | undefined
    for (;;) {
      const page = await this.api.get<SearchPageDto>(SEARCH_PATH, {
        params: { labels: JSON.stringify({ [NAME_LABEL]: name }), limit: 100, ...(cursor ? { cursor } : {}) },
        ...(region ? { headers: { 'x-daytona-region': region } } : {}),
      }).then((response) => response.data)
      const live = searchPageSandboxes(page).filter((sandbox) => sandbox.status !== 'deleted' && sandbox.status !== 'deleting')
      const sandboxes = await this.sandboxesFromDtos(live)
      if (region !== undefined) for (const s of sandboxes) s.target = region
      found.push(...sandboxes)
      const nextCursor = page.next_cursor ?? undefined
      if (!nextCursor || seen.has(nextCursor)) return found
      seen.add(nextCursor)
      cursor = nextCursor
    }
  }

  /** Fetch a sandbox by id. */
  public async get(id: string, options: SandboxConnectOptions = {}): Promise<Sandbox> {
    return this.getWithTimeout(id, options)
  }

  private async getWithTimeout(id: string, options: SandboxConnectOptionsFromCreate = {}, timeout?: number): Promise<Sandbox> {
    const config = timeout === undefined ? undefined : { timeout }
    const sandbox = await this.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(id)}`, config).then((response) => response.data)
    return this.sandboxFromDto(sandbox, options, timeout)
  }

  /**
   * List sandboxes as one page `{ items, nextCursor }`.
   *
   * With only `cursor`/`limit` this is the read-your-writes path: one page of
   * `GET /sandboxes`, newest first. A sandbox you just created is in the result;
   * walk further pages with {@link iter} or the returned cursor.
   *
   * Any other option — including `id`/`name` (sent as `idPrefix`/`namePrefix`),
   * `q`, `status`, `sandboxClass`, `region`, `labels`, the resource and timestamp
   * bounds, `sort`, `order` — switches the call to a single `GET /sandboxes/search`
   * request, the same surface as {@link search}, and fills `total` and
   * `totalRelation` on the result. That path is
   * **eventually consistent**: results may be briefly stale,
   * and search spans every region. The
   * cursor it returns is opaque, tied to the exact filter and
   * sort that produced it (reusing it with a different filter or sort is a
   * 400), and paging is best-effort under concurrent mutation: a sandbox whose
   * sort field changes between pages can be seen twice or missed. If the API
   * reports search as unconfigured (501, {@link SearchNotConfiguredError} with
   * `code === 'SEARCH_NOT_CONFIGURED'`) or search is unavailable (503,
   * {@link SearchUnavailableError} with `code === 'SEARCH_UNAVAILABLE'`), drop
   * the search options to fall back to the `GET /sandboxes` path.
   */
  public async list(options: ListSandboxesOptions = {}): Promise<ListResult<Sandbox>> {
    // `GET /sandboxes` has no id/name filters; a prefix match applied to ONE page would
    // silently miss later pages, so a prefix uses the search API too.
    if (hasSearchFilters(options) || options.id != null || options.name != null) {
      const { id, name, ...search } = options
      const page = await this.searchPage({ ...search, idPrefix: id, namePrefix: name })
      return {
        items: page.items,
        nextCursor: page.nextCursor,
        total: page.total,
        totalRelation: page.totalRelation,
        rawTotalRelation: page.rawTotalRelation,
      }
    }
    const listResponse = await this.api.get<api.ListSandboxesResponse>('/sandboxes', {
      params: { cursor: options.cursor, limit: options.limit },
    }).then((response) => response.data)
    const nextCursor = listResponse.next_cursor ?? ('nextCursor' in listResponse && typeof listResponse.nextCursor === 'string' ? listResponse.nextCursor : undefined)
    return { items: await this.sandboxesFromDtos(listResponse.sandboxes), nextCursor }
  }

  public async *iter(options: ListSandboxesOptions = {}): AsyncIterable<Sandbox> {
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.list({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }


  /**
   * Search sandboxes across every region (`GET /sandboxes/search`).
   *
   * Every option is optional and they combine as AND. `q` is free text over
   * name and image (or an exact id); `namePrefix`/`idPrefix` are the
   * server-side form of `list({ name })`/`list({ id })`; `status`, `sandboxClass`
   * and `region` accept a single value or a list (any-of; lifecycle states use
   * the API's wire names such as `running`); `labels` is a containment match; the
   * `min*`/`max*` bounds are inclusive, the `*StartAt` timestamp bounds are
   * inclusive and the `*EndAt` bounds are exclusive, timestamps as RFC 3339
   * strings or `Date` objects (a `Date` is serialized in UTC — a whole day is
   * `createdStartAt` that day and `createdEndAt` the next). `sort` defaults to `created_at` and
   * `order` to `desc`; `limit` is clamped to 1..200 by the server. Resolves to a
   * {@link SearchResult} whose `items` are the same {@link Sandbox} handles
   * {@link list} returns.
   *
   * Consistency: search is **eventually consistent**. A create,
   * update or delete may not appear immediately, so a sandbox you just
   * created may not be in the result yet — use {@link get} or {@link list}
   * without search filters for read-your-writes. Results span every region.
   *
   * Cursor: `nextCursor` is opaque and bound to the exact filter and sort that
   * produced it — presenting it with a different filter or sort is a 400, never
   * a silent reset. Paging is best-effort under concurrent mutation: a sandbox
   * whose sort field (`status`, `name`,
   * `memory_mib`, `updated_at`, ...) changes between two page fetches can appear on
   * both pages or on neither, and creates or deletes mid-walk shift later pages.
   *
   * Errors: 400 ({@link BadRequestError}) for a malformed or mismatched cursor,
   * an unknown `status`/`sandboxClass`/`sort` value, a non-object `labels` or a `min`
   * above its `max`; 501 ({@link SearchNotConfiguredError} with
   * `code === 'SEARCH_NOT_CONFIGURED'`) when search is not configured;
   * 503 ({@link SearchUnavailableError} with `code === 'SEARCH_UNAVAILABLE'`)
   * when search is unavailable. The error body is the unified envelope
   * `{ code, message, source, trace_id }` — the class is dispatched from
   * `(source, code)`. On 501/503 callers may fall
   * back to {@link list} without search options.
   */
  public async search(options: SearchSandboxesOptions = {}): Promise<SearchResult<Sandbox>> {
    return this.searchPage(options)
  }

  private async searchPage(options: SearchSandboxesOptions): Promise<SearchResult<Sandbox>> {
    const data = await this.api.get<SearchPageDto>(SEARCH_PATH, { params: searchQueryParams(options) }).then((response) => response.data)
    const items = searchPageSandboxes(data)
    const sandboxes = await this.sandboxesFromDtos(items)
    for (let i = 0; i < sandboxes.length; i++) {
      const region = items[i]?.region_id
      if (region) sandboxes[i].target = region
    }
    const rawTotalRelation = data.total_relation ?? 'eq'
    return {
      items: sandboxes,
      nextCursor: typeof data.next_cursor === 'string' ? data.next_cursor : undefined,
      total: data.total ?? 0,
      totalRelation: knownOrUnknown(rawTotalRelation, SEARCH_TOTAL_RELATIONS),
      rawTotalRelation,
    }
  }

  public async start(sandbox: Sandbox | string, options: { readonly timeout?: number } = {}): Promise<void> { await this.resolveWithHub(sandbox).start(options) }
  public async stop(sandbox: Sandbox | string): Promise<void> { await this.resolveWithHub(sandbox).stop() }
  public async pause(sandbox: Sandbox | string, options: { readonly timeout?: number } = {}): Promise<void> { await this.resolveWithHub(sandbox).pause(options) }
  public async resume(sandbox: Sandbox | string, options: { readonly timeout?: number } = {}): Promise<void> { await this.resolveWithHub(sandbox).resume(options) }
  /** Request deletion. Image deletion may still return 409 afterward, even after a sandbox 404 or `destroyed` state. */
  public async delete(sandbox: Sandbox | string): Promise<void> { await this.resolveWithHub(sandbox).delete() }
  public async getNetworkPolicy(sandbox: Sandbox | string): Promise<NetworkPolicyInfo> { return this.resolveWithHub(sandbox).getNetworkPolicy() }
  public async updateNetworkPolicy(
    sandbox: Sandbox | string,
    networkPolicy: NetworkPolicyInput,
    options: { ifMatch: string },
  ): Promise<NetworkPolicyInfo> { return this.resolveWithHub(sandbox).updateNetworkPolicy(networkPolicy, options) }
  public async resetNetworkPolicy(sandbox: Sandbox | string, options: { ifMatch: string }): Promise<NetworkPolicyInfo> {
    return this.resolveWithHub(sandbox).resetNetworkPolicy(options)
  }
  /** Read a sandbox's attached secret names and apply state; see `Sandbox.getSecrets`. */
  public async getSandboxSecrets(sandbox: Sandbox | string): Promise<SandboxSecretsInfo> {
    return this.resolveWithHub(sandbox).getSecrets()
  }
  /** Replace a sandbox's attached secret names; see `Sandbox.replaceSecrets`. */
  public async replaceSandboxSecrets(
    sandbox: Sandbox | string,
    secrets: readonly string[],
    options: { ifMatch: string },
  ): Promise<SandboxSecretsInfo> {
    return this.resolveWithHub(sandbox).replaceSecrets(secrets, options)
  }

  private resolveWithHub(sandbox: Sandbox | string): Sandbox {
    // A bare id is a metadata-less handle for the by-id API operations
    // (no toolbox URL, no fetch); a sandbox object comes from create/get/list.
    if (typeof sandbox === 'string') return this.buildSandbox(sandbox)
    const hub = this.acquireHub()
    if (hub !== undefined) sandbox.attachHub(hub)
    return sandbox
  }
}

export { gbToMib }
