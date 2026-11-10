/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { pathSegment } from './pathSegment.js'
import type { AxiosInstance } from 'axios'
import { createHttpClient, HttpTransport } from './http.js'
import type { HttpClientOptions } from './http.js'
import { assertKnownOptions, badRequest, snapshotOptions } from './options.js'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { INTERNAL } from './internal.js'
import { BadRequestError, ClientClosedError } from './errors/DaytonaError.js'
import type { ImageBuilder } from './Image.js'
import { Sandbox } from './Sandbox.js'
import type { SandboxConnectOptionsFromCreate } from './Sandbox.js'
import { ImagesService } from './Images.js'
import { SecretsService } from './Secret.js'
import { ReservationsService } from './Reservations.js'
import { MacModelsService } from './MacModels.js'
import { SnapshotsService } from './Snapshots.js'
import { VolumesService } from './Volume.js'
import { auditActionList, projectApiKey, projectBudget, organizationLimits } from './accountMappers.js'
import { LIMITS_KEYS, LIST_KEYS } from './clientOptionKeys.js'
import { clientConnectionConfig } from './clientConfig.js'
import { buildSandboxImage, createSandbox } from './clientCreate.js'
import { linuxCreateRequest, snapshotCreateRequest, macosCreateRequest } from './clientCreateRequests.js'
import { getSandbox, listSandboxes, searchSandboxes, findSandboxesByName } from './clientSandboxes.js'
import type { SandboxClientContext } from './clientSandboxes.js'
import type * as api from './gen/api-types.js'
import { attachHub, detachHub, eventsHubConfig } from './events.js'
import type { EventsHubConfig, WaitHub } from './events.js'
import type { AuditActionList, ProjectApiKey, CreateFromBuildOptions, FindByNameOptions, LifecycleWaitOptions, CreateMacosSandboxOptions, CreateSandboxFromSnapshotOptions, CreateSandboxOptions, DaytonaConfig, GetLimitsOptions, ListResult, ListSandboxesOptions, OrganizationLimits, ProjectBudget, SandboxConnectOptions, SandboxInfo, SearchResult, SearchSandboxesOptions, SandboxDto } from './types.js'
import { isBlank } from './pyText.js'

export { DEFAULT_V2_API_URL } from './clientConfig.js'

export class Daytona {
  public readonly images: ImagesService
  public readonly snapshots: SnapshotsService
  public readonly volumes: VolumesService
  public readonly secrets: SecretsService
  public readonly reservations: ReservationsService
  /** The Mac model registry that `reservations.create({ model })` draws from. */
  public readonly macModels: MacModelsService
  private readonly api: AxiosInstance
  private readonly apiTransport: HttpTransport
  private readonly apiKey?: string
  private keyOrganization?: Promise<string>
  private readonly apiUrl: string
  private readonly httpOptions: HttpClientOptions
  private readonly maxResponseBytes: number
  private readonly hubShared: boolean
  private readonly hubEventsConfig: EventsHubConfig
  private readonly hubIdentityKey: string
  private hub: WaitHub | undefined
  private readonly hubApi: AxiosInstance
  private closed = false

  constructor(config: DaytonaConfig = {}) {
    const connection = clientConnectionConfig(config)
    this.apiKey = connection.apiKey
    this.apiUrl = connection.apiUrl
    this.hubIdentityKey = connection.hubIdentityKey
    this.httpOptions = { ...connection.httpOptions, ensureOpen: () => {
      if (this.closed) throw new ClientClosedError()
    } }
    this.maxResponseBytes = connection.maxResponseBytes
    this.apiTransport = new HttpTransport(this.apiUrl, this.apiKey, connection.requestTimeoutMs, this.httpOptions)
    this.api = createHttpClient(this.apiUrl, this.apiKey, connection.requestTimeoutMs, this.httpOptions)
    // A shared events hub outlives the client that created it: its instance has
    // no closed-client check, so closing this client never breaks the waits of
    // another client attached to the same hub (each wait checks its own client).
    this.hubApi = createHttpClient(this.apiUrl, this.apiKey, connection.requestTimeoutMs, connection.httpOptions)
    this.images = new ImagesService(INTERNAL, this.api, this.apiTransport)
    this.snapshots = new SnapshotsService(INTERNAL, this.api)
    this.volumes = new VolumesService(INTERNAL, this.api)
    this.secrets = new SecretsService(INTERNAL, this.api)
    this.reservations = new ReservationsService(INTERNAL, this.api)
    this.macModels = new MacModelsService(INTERNAL, this.api)
    this.hubShared = config.sharedHub ?? true
    this.hubEventsConfig = eventsHubConfig(connection.events)
    credentialSafeDiagnostics(this, () => ({ type: 'DaytonaV2' }))
  }

  private acquireHub(): WaitHub | undefined {
    if (this.closed) throw new ClientClosedError()
    if (this.hub?.closed) this.hub = undefined
    if (this.hub === undefined) {
      this.hub = attachHub({
        apiUrl: this.apiUrl,
        apiKey: this.hubIdentityKey,
        // v2 has no client-level target (region is per create): not a key part.
        target: undefined,
        shared: this.hubShared,
        config: this.hubEventsConfig,
        // The client's own instance: its maxResponseBytes caps the hub's
        // ticket mint and long-poll fallback (neither is a file transfer).
        _api: this.hubApi,
        maxResponseBytes: this.maxResponseBytes,
      })
    }
    return this.hub
  }

  /** Close this client. New requests on stored clients and their handles throw
   * `ClientClosedError`; closing the root lets `root.v2` build a fresh client. */
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
  public async listAuditActions(): Promise<AuditActionList> {
    return this.api.get<api.AuditActionList>('/audit/actions').then((response) => auditActionList(response.data))
  }

  /**
   * Describe the API key this client authenticates with: its name, masked
   * display parts, the project and organization it acts in, its permissions,
   * expiry and last use. The token itself is never returned.
   */
  public async currentApiKey(): Promise<ProjectApiKey> {
    return this.api.get<api.ProjectApiKeyWithContext>('/api-keys/current').then((response) => projectApiKey(response.data))
  }

  /**
   * Return the monthly spend budget of the API key's project. An unset budget
   * has undefined limit and period fields. The key needs `read:spending`.
   * Spend is calculated at list price, before credits, and lags usage by
   * several minutes. While the project is over its budget, creating or
   * starting a sandbox in it throws `ProjectBudgetExceededError`.
   */
  public async getProjectBudget(): Promise<ProjectBudget> {
    return this.api.get<api.ProjectBudget>('/usage/budget').then((response) => projectBudget(response.data))
  }

  /**
   * Return the organization's limits and current usage. An undefined `limit` means
   * unlimited. With an API key the organization defaults to the key's own,
   * looked up once and cached; with `jwtToken` authentication pass
   * `organizationId`. The credential needs `read:limits`. Member counts
   * (`members`) are not included: they are served to signed-in members only.
   */
  public async getLimits(options: GetLimitsOptions = {}): Promise<OrganizationLimits> {
    assertKnownOptions('GetLimitsOptions', options, LIMITS_KEYS)
    const organizationId = options.organizationId ?? await this.keyOrganizationId()
    return this.api
      .get<api.OrganizationLimits>(`/orgs/${pathSegment(organizationId, 'organizationId')}/limits`)
      .then((response) => organizationLimits(response.data))
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
      (key) => key.context.orgId,
      (error: unknown) => {
        this.keyOrganization = undefined
        throw error
      },
    )
    return this.keyOrganization
  }

  /**
   * Create a Linux sandbox from a registered image name or `img_…` id.
   * Repeating the same `name` and request is a permanent get-or-create operation.
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
    return createSandbox(this.sandboxContext, linuxCreateRequest(imageOrOptions, maybeOptions))
  }

  /**
   * Create a sandbox from a snapshot.
   *
   * When Linux sandboxes are not enabled for the organization, the API answers
   * `403` `FEATURE_NOT_ENABLED` (thrown as `FeatureNotEnabledError`).
   */
  public async createFromSnapshot(snapshot: string, options: CreateSandboxFromSnapshotOptions = {}): Promise<Sandbox> {
    return createSandbox(this.sandboxContext, snapshotCreateRequest(snapshot, options))
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
    * `resources.cpu` (cores), `resources.memory` (GiB) and `resources.disk` (GiB)
    * size the sandbox; each omitted
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
   * Find regions and their regional API endpoints with `daytona region list` (the CLI);
   * discover Mac models and prices with `macModels.catalog({ region: '<region>' })`.
   * To select a regional API endpoint, set `DAYTONA_V2_API_URL` before creating
   * the client. An API that does not serve the region answers `ConflictError`;
   * the SDK does not reroute.
   */
  public async createMacos(image: string, options: CreateMacosSandboxOptions = {}): Promise<Sandbox> {
    return createSandbox(this.sandboxContext, macosCreateRequest(image, options))
  }

  /**
   * Build an anonymous image, wait for it, then create the sandbox from it.
   * `region` selects the region for BOTH legs: a Dockerfile-built image lives
   * in one region and can only boot there, so it is built where the
   * sandbox will run. `buildTimeoutSeconds` bounds the image build separately;
   * the sandbox create always receives the caller's full `waitSeconds`.
   */
  public async createFromBuild(builder: ImageBuilder, options: CreateFromBuildOptions = {}): Promise<Sandbox> {
    const { image, createOptions } = await buildSandboxImage(this.images, builder, options)
    return this.create(image.id, createOptions)
  }

  private get sandboxContext(): SandboxClientContext {
    return { api: this.api, buildSandbox: (source, options) => this.buildSandbox(source, options) }
  }

  // Every caller passes API data (create response, get/list sandbox details)
  // or a bare id, so the input's toolbox URL is trusted here — and only here.
  private buildSandbox(sandboxOrId: SandboxDto | SandboxInfo | string, options: SandboxConnectOptions = {}): Sandbox {
    const fromApi: SandboxConnectOptionsFromCreate = { ...options, toolboxUrlFromApi: true }
    return new Sandbox(INTERNAL, { sandbox: sandboxOrId, api: this.api, apiKey: this.apiKey, options: fromApi, requestTimeout: this.api.defaults.timeout, httpOptions: this.httpOptions, hub: this.acquireHub() })
  }

  /**
   * Find every live sandbox created with `{ name }`, across all matching pages.
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
   * This all-matches lookup is the exception to the one-page list rule.
   * `region` scopes the read the way it scopes a create; omit it or pass
   * `'local'` to use the default region.
   */
  public async findByName(name: string, options: FindByNameOptions = {}): Promise<readonly Sandbox[]> {
    return findSandboxesByName(this.sandboxContext, name, options)
  }

  /** Fetch a sandbox by id. */
  public async get(id: string, options: SandboxConnectOptions = {}): Promise<Sandbox> {
    return getSandbox(this.sandboxContext, id, options)
  }

  /**
   * List sandboxes as one page `{ items, nextCursor }`.
   *
   * `cursor` and `limit` are the only options. This is the read-your-writes path:
   * one page of
   * `GET /sandboxes`, newest first. A sandbox you just created is in the result;
   * walk further pages with {@link iter} or the returned cursor.
   *
   * Use {@link search} for filters, prefixes, resource bounds, and sorting.
   */
  public async list(options: ListSandboxesOptions = {}): Promise<ListResult<Sandbox>> {
    return listSandboxes(this.sandboxContext, options)
  }

  public async *iter(options: ListSandboxesOptions = {}): AsyncIterable<Sandbox> {
    options = snapshotOptions('ListSandboxesOptions', options, LIST_KEYS) ?? {}
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
   * name and image (or an exact id); `namePrefix`/`idPrefix` select prefixes.
   * `states`, `sandboxClass`, and `region` accept a single value or a list
   * (any-of). States use public names such as `started`; `creating` matches
   * pending or assigned sandboxes, and `error` matches failed or lost sandboxes.
   * `unknown` is not searchable. `labels` is a containment match; the
   * `min*`/`max*` bounds are inclusive, the `*StartAt` timestamp bounds are
   * inclusive and the `*EndAt` bounds are exclusive, timestamps as RFC 3339
   * timestamps without a leap second (year 0001-9999, a real calendar date) or
   * `Date` objects in those years (a `Date` is serialized in UTC — a whole day is
   * `createdStartAt` that day and `createdEndAt` the next). `sort` defaults to `created_at` and
   * `order` to `desc`; `limit` must be an integer from 1 to 200, else
   * `BadRequestError` before any request. Resolves to a
   * {@link SearchResult} whose `items` are the same {@link Sandbox} handles
   * {@link list} returns.
   *
   * Consistency: search is **eventually consistent**. A create,
   * update or delete may not appear immediately, so a sandbox you just
   * created may not be in the result yet — use {@link get} or {@link list}
   * for read-your-writes. Results span every region.
   *
   * Cursor: `nextCursor` is opaque and bound to the exact filter and sort that
   * produced it — presenting it with a different filter or sort is a 400, never
   * a silent reset. Paging is best-effort under concurrent mutation: a sandbox
   * whose sort field (`name`,
   * `memory_mib`, `updated_at`, ...) changes between two page fetches can appear on
   * both pages or on neither, and creates or deletes mid-walk shift later pages.
   *
   * Errors: 400 ({@link BadRequestError}) for a malformed or mismatched cursor,
   * an unknown `states`/`sandboxClass`/`sort` value, a non-object `labels` or a `min`
   * above its `max`; 501 ({@link SearchNotConfiguredError} with
   * `code === 'SEARCH_NOT_CONFIGURED'`) when search is not configured;
   * 503 ({@link SearchUnavailableError} with `code === 'SEARCH_UNAVAILABLE'`)
   * when search is unavailable. The error body is the unified envelope
   * `{ code, message, source, trace_id }` — the class is dispatched from
   * `(source, code)`. On 501/503 callers may fall
   * back to {@link list} without search options.
   */
  public async search(options: SearchSandboxesOptions = {}): Promise<SearchResult<Sandbox>> {
    return searchSandboxes(this.sandboxContext, options)
  }

  public async start(sandbox: Sandbox | string, options: LifecycleWaitOptions = {}): Promise<void> { await this.resolveWithHub(sandbox).start(options) }
  public async stop(sandbox: Sandbox | string, options: LifecycleWaitOptions = {}): Promise<void> { await this.resolveWithHub(sandbox).stop(options) }
  public async pause(sandbox: Sandbox | string, options: LifecycleWaitOptions = {}): Promise<void> { await this.resolveWithHub(sandbox).pause(options) }
  public async resume(sandbox: Sandbox | string, options: LifecycleWaitOptions = {}): Promise<void> { await this.resolveWithHub(sandbox).resume(options) }
  /** Delete and wait for completion. Image deletion may still return 409 afterward. */
  public async delete(sandbox: Sandbox | string, options: LifecycleWaitOptions = {}): Promise<void> { await this.resolveWithHub(sandbox).delete(options) }

  private resolveWithHub(sandbox: Sandbox | string): Sandbox {
    // A bare id is a metadata-less handle for the by-id API operations
    // (no toolbox URL, no fetch); a sandbox object comes from create/get/list.
    if (typeof sandbox === 'string' && !isBlank(sandbox)) {
      // The id goes into request paths: refused like any path value.
      pathSegment(sandbox, 'sandbox')
      return this.buildSandbox(sandbox)
    }
    // A wrong type or a blank id is refused before any request, as in Python.
    if (!(sandbox instanceof Sandbox)) throw badRequest('sandbox must be a Sandbox or a non-empty sandbox id')
    const hub = this.acquireHub()
    if (hub !== undefined) sandbox.attachHub(hub)
    return sandbox
  }
}
