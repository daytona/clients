/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { optionalQueryText, pageParams } from './pageParams.js'
import { pathSegment } from './pathSegment.js'
import { isBlank } from './pyText.js'
import { frozenPage } from './frozen.js'
import { listPage } from './listPage.js'
import { searchOrList } from './searchFirst.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { followBuildLogs } from './BuildLogs.js'
import type { BuildLogFollowOptions, BuildLogFollowResult } from './BuildLogs.js'
import { Image, ImageBuilder, Snapshot, buildImageRequest, imageOptionsBody, isImageBuilder } from './Image.js'
import { contextUploadRuntime, uploadBuildContext } from './contextUpload.js'
import { archiveBuilder, archiveDirectory, isContextDirectory } from './contextArchive.js'
import { localSourcesOf } from './ImageBuilder.js'
import { expandHome } from './builderLocalSource.js'
import type { BuildContext } from './contextUpload.js'
import { HttpTransport, boundedRequestTimeout, isRequestBudgetTimeout, sleep, strictUriEncode, transportFromAxiosInstance } from './http.js'
import { acknowledgedId } from './responseFields.js'
import type { HttpTransportConfig } from './http.js'
import type * as api from './gen/api-types.js'
import type { BuildImageOptions, ListResult } from './types.js'
import { ImageBuildFailedError, RequestTimeoutError } from './errors/DaytonaError.js'
import { MAX_TIMER_DELAY_MS, assertKnownOptions, badRequest, optionalSeconds, optionKeys, snapshotOptions, waitDeadline } from './options.js'
import { getArtifact } from './artifactOptions.js'
import type { GetArtifactOptions } from './artifactOptions.js'
import { INTERNAL, assertInternal } from './internal.js'
import type { Internal } from './internal.js'

export type { GetArtifactOptions } from './artifactOptions.js'

export interface ListImagesOptions {
  readonly cursor?: string
  readonly limit?: number
  readonly name?: string
  readonly anonymous?: boolean
  readonly sandboxId?: string
  readonly platform?: 'true' | 'false' | 'any'
}

export interface ImagePullOptions extends BuildImageOptions {
  readonly wait?: boolean
  /** Total pull budget in seconds; omitted, zero or a value above about 24.8 days is unbounded. */
  readonly timeoutSeconds?: number
}

const BUILD_IMAGE_OPTION_KEYS = optionKeys<BuildImageOptions>()({ name: true, idleExpirySeconds: true, region: true, cpuArch: true })
export type BuildFromContextOptions = BuildImageOptions & {
  /** The Dockerfile's path inside the archive (default `Dockerfile`). */
  readonly dockerfile?: string
  /**
   * Bounds the build request that follows the upload, in seconds, replacing
   * the client's request timeout for it; `0`, or a value above about 24.8
   * days, is no limit. The upload keeps its own bounds.
   */
  readonly timeoutSeconds?: number
}

const BUILD_FROM_CONTEXT_OPTION_KEYS = optionKeys<BuildFromContextOptions>()({ ...BUILD_IMAGE_OPTION_KEYS, dockerfile: true, timeoutSeconds: true })
const IMAGE_PULL_OPTION_KEYS = optionKeys<ImagePullOptions>()({ ...BUILD_IMAGE_OPTION_KEYS, wait: true, timeoutSeconds: true })
const LIST_IMAGES_OPTION_KEYS = optionKeys<ListImagesOptions>()({ cursor: true, limit: true, name: true, anonymous: true, sandboxId: true, platform: true })

// A per-call timeout in seconds as axios milliseconds: `0`, or a value above
// the longest timer, is no limit, as for every 2.x timeout.
function axiosTimeoutMs(timeoutSeconds: number): number {
  const timeoutMs = Math.ceil(timeoutSeconds * 1000)
  return timeoutMs > MAX_TIMER_DELAY_MS ? 0 : timeoutMs
}

const IMAGE_LONGPOLL_MAX_MS = 60_000
const IMAGE_POLL_MIN_INTERVAL_MS = 1_000
const IMAGE_HOLD_READ_SLACK_MS = 5_000

// Python's `anonymous` defaults to `False` (the contract's default) and refuses
// `None`, so `null` is refused here too; only an omitted value takes the default.
function listImagesAnonymous(value: unknown): boolean | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'boolean') throw badRequest('anonymous must be a boolean')
  return value
}

// The contract's values only: SDK inputs are closed.
function listImagesPlatform(value: unknown): 'true' | 'false' | 'any' | undefined {
  if (value === undefined || value === null) return undefined
  if (value !== 'true' && value !== 'false' && value !== 'any') throw badRequest('platform must be "true", "false" or "any"')
  return value
}

export class ImagesService {
  private resolvedApiTransport?: HttpTransport

  /** @internal */
  constructor(
    token: Internal,
    http: AxiosInstance,
    apiTransport?: HttpTransport | HttpTransportConfig,
  )
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(
    token: Internal,
    private readonly http: AxiosInstance,
    private readonly apiTransport?: HttpTransport | HttpTransportConfig,
  ) {
    assertInternal(token, 'ImagesService')
    credentialSafeDiagnostics(this, () => ({ type: 'ImagesService' }))
  }

  private getApiTransport(): HttpTransport {
    if (this.apiTransport instanceof HttpTransport) return this.apiTransport
    this.resolvedApiTransport ??= transportFromAxiosInstance(this.http, this.apiTransport)
    return this.resolvedApiTransport
  }

  public async followBuildLogs(imageId: string, options: BuildLogFollowOptions): Promise<BuildLogFollowResult> {
    return followBuildLogs(this.getApiTransport(), `/images/${pathSegment(imageId, 'imageId')}/build-logs-url`, options)
  }

  /**
   * Submit a builder without waiting for the image to become ready.
   * Local files are archived and uploaded first, with the same timeouts as
   * `buildFromContext`. With local files, `name` and `idleExpirySeconds` cannot
   * be combined: idle expiry applies only to anonymous images.
   */
  public async build(builder: ImageBuilder, options: BuildImageOptions = {}): Promise<Image> {
    if (!isImageBuilder(builder)) throw badRequest('images.build() takes an ImageBuilder (ImageBuilder.fromDockerfile(...), .fromRegistry(...))')
    assertKnownOptions('BuildImageOptions', options, BUILD_IMAGE_OPTION_KEYS)
    // Validate every image option before inspecting or reading any local file.
    const request = buildImageRequest(builder, options)
    if (localSourcesOf(builder).length !== 0) {
      if (request.name !== undefined && request.idle_expiry_seconds !== undefined) {
        throw badRequest('idleExpirySeconds applies only to an anonymous image; omit name or idleExpirySeconds')
      }
      const archive = await archiveBuilder(builder)
      try { return await this.buildFromContext(archive.path, options) } finally { await archive.cleanup() }
    }
    const { data } = await this.http.post<api.CreateImageResponse>('/images', request)
    // Checked before the read: no request ever goes to /images/undefined.
    return this.get(acknowledgedId(data, 'image create'))
  }

  /**
   * Build an image from a Dockerfile inside a build-context tar archive: its
   * bytes (an `ArrayBuffer` or any view), a `Blob`, or (Node.js) the path of a
   * `.tar` file or directory, up to 1 GiB.
   * Directories honour the Dockerfile-specific ignore file, else `.dockerignore`.
   * `dockerfile` is the Dockerfile's path inside it (default `Dockerfile`).
   * The archive is uploaded straight to storage in parts and verified by the
   * API before the build starts; an identical archive the project already
   * uploaded is reused without uploading it again. `name` and
   * `idleExpirySeconds` cannot be combined: idle expiry applies only to
   * anonymous images. `timeoutSeconds` bounds the build request after the
   * upload (see {@link BuildFromContextOptions}). Resolves once the build is
   * submitted; wait with `get(id, { wait: 'ready' })`.
   */
  public async buildFromContext(context: BuildContext, options: BuildFromContextOptions = {}): Promise<Image> {
    assertKnownOptions('BuildFromContextOptions', options, BUILD_FROM_CONTEXT_OPTION_KEYS)
    // Checked before filesystem access or upload, as build() checks before its request.
    const imageOptions = imageOptionsBody(options)
    // Checked before the upload: the API refuses the pair only at the build.
    if (imageOptions.name !== undefined && imageOptions.idle_expiry_seconds !== undefined) {
      throw badRequest('idleExpirySeconds applies only to an anonymous image; omit name or idleExpirySeconds')
    }
    // Checked before the upload, as build() checks them before its request.
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
    // images.build() archives builders; here a context is bytes, a Blob or a
    // path. Checked where Python checks it, so both report the same error first.
    if (isImageBuilder(context)) {
      throw badRequest('buildFromContext() takes the bytes, Blob or path of a build context; build an ImageBuilder with images.build()')
    }
    const dockerfile = options.dockerfile ?? 'Dockerfile'
    if (typeof dockerfile !== 'string' || isBlank(dockerfile)) throw badRequest('dockerfile must be a non-empty path')
    const expanded = typeof context === 'string' && contextUploadRuntime.isNode() ? expandHome(context) : context
    if (typeof expanded === 'string' && await isContextDirectory(expanded)) {
      const archive = await archiveDirectory(expanded, dockerfile)
      try { return await this.buildFromContext(archive.path, options) } finally { await archive.cleanup() }
    }
    const upload = await uploadBuildContext(this.http, expanded, { dockerfile, region: imageOptions.region_id })
    const request: api.CreateImageRequest = {
      ...imageOptions,
      // The build must name the region the upload was verified in.
      context_upload_id: upload.id,
      region_id: upload.regionId,
    }
    const { data } = await this.http.post<api.CreateImageResponse>('/images', request, timeoutSeconds === undefined ? undefined : { timeout: axiosTimeoutMs(timeoutSeconds) })
    // Checked before the read: no request ever goes to /images/undefined.
    return this.get(acknowledgedId(data, 'image create'))
  }

  /**
   * List images, one page. Reads `GET /images/search` (every region,
   * eventually consistent: an image you just created may be missing for a few
   * seconds; `get()` reads it at once) and, on a deployment without search,
   * `GET /images`.
   */
  public async list(options: ListImagesOptions = {}): Promise<ListResult<Image>> {
    options = snapshotOptions('ListImagesOptions', options, LIST_IMAGES_OPTION_KEYS) ?? {}
    const params = {
      ...pageParams(options.cursor, options.limit),
      name: optionalQueryText(options.name, 'name'),
      anonymous: listImagesAnonymous(options.anonymous),
      sandbox_id: optionalQueryText(options.sandboxId, 'sandboxId'),
      platform: listImagesPlatform(options.platform),
    }
    const data = await searchOrList(
      () => this.http.get<api.ImagePage>('/images/search', { params }).then((response) => response.data),
      () => this.http.get<api.ImagePage>('/images', { params }).then((response) => response.data),
    )
    const page = listPage<api.ImageListItem>(data, 'images', 'image list')
    return frozenPage({ items: page.items.map((image) => new Image(INTERNAL, image)), nextCursor: page.nextCursor })
  }

  public async *iter(options: ListImagesOptions = {}): AsyncIterable<Image> {
    options = snapshotOptions('ListImagesOptions', options, LIST_IMAGES_OPTION_KEYS) ?? {}
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

  public async get(imageId: string, options: GetArtifactOptions = {}): Promise<Image> {
    return getArtifact(this.http, `/images/${pathSegment(imageId, 'imageId')}`, options,
      // SAFETY: the Image constructor checks the response against the contract.
      (data) => new Image(INTERNAL, data as api.ImageListItem))
  }

  /** Import a registry image and wait until ready by default. Omitted or zero timeoutSeconds, or one above about 24.8 days, is unbounded. */
  public async pull(ref: string, options: ImagePullOptions = {}): Promise<Image> {
    assertKnownOptions('ImagePullOptions', options, IMAGE_PULL_OPTION_KEYS)
    const wait: unknown = options.wait
    if (wait !== undefined && typeof wait !== 'boolean') throw badRequest('wait must be a boolean')
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
    // Omitted, 0, or above the timer maximum (about 24.8 days): no deadline.
    const deadline = waitDeadline(timeoutSeconds)
    const timeoutError = () => new RequestTimeoutError('image pull did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeRequestUsesDeadline = false
    const armDeadline = (): void => {
      if (deadline === undefined) return
      const remaining = deadline - Date.now()
      if (remaining <= 0) controller.abort()
      // A deadline is never beyond the timer maximum; a timer that fires early re-checks.
      else timer = setTimeout(armDeadline, Math.ceil(remaining))
    }
    const requestOptions = (requestTimeout = this.http.defaults.timeout) => {
      const timeout = deadline === undefined ? requestTimeout : boundedRequestTimeout(deadline, requestTimeout)
      if (timeout === undefined || controller.signal.aborted) throw timeoutError()
      // Axios's timer can fire before the final clock tick, including rounded/equal budgets.
      activeRequestUsesDeadline = deadline !== undefined && timeout >= deadline - Date.now()
      return { timeout: timeout > MAX_TIMER_DELAY_MS ? 0 : timeout, signal: controller.signal }
    }
    const checkDeadline = (): void => {
      if (deadline !== undefined && Date.now() >= deadline) throw timeoutError()
    }
    armDeadline()
    try {
      const { data: response } = await this.http.post<api.CreateImageResponse>('/images', buildImageRequest(ImageBuilder.fromRegistry(ref), options), requestOptions())
      const submittedId = acknowledgedId(response, 'image create')
      const { data: initial } = await this.http.get<api.ImageListItem>(`/images/${pathSegment(submittedId, 'image id')}`, requestOptions())
      checkDeadline()
      let image = new Image(INTERNAL, initial)
      if (options.wait === false) return image
      let lastPollStarted: number | undefined
      while (image.status !== 'ready' && image.status !== 'failed' && image.status !== 'error') {
        // An early non-terminal answer pauses up to a second before the next
        // hold, as `get(id, { wait: 'ready' })` does: no hot loop on a fast server.
        if (lastPollStarted !== undefined) {
          const pause = Math.min(IMAGE_POLL_MIN_INTERVAL_MS - (Date.now() - lastPollStarted), deadline === undefined ? Infinity : deadline - Date.now())
          if (pause > 0) await sleep(pause)
        }
        checkDeadline()
        lastPollStarted = Date.now()
        // A long poll is governed by the pull budget, not the request timeout:
        // the hold is the smaller of the remaining budget and the server's cap,
        // in whole milliseconds, and the read allows the hold plus slack.
        const hold = deadline === undefined ? undefined : Math.max(1, Math.min(Math.ceil(deadline - Date.now()), IMAGE_LONGPOLL_MAX_MS))
        const config = requestOptions(hold === undefined ? 0 : hold + IMAGE_HOLD_READ_SLACK_MS)
        const { data } = await this.http.get<api.ImageListItem>(`/images/${strictUriEncode(image.id)}`, {
          ...config,
          params: { wait: 'ready', timeout_ms: hold },
        })
        checkDeadline()
        image = new Image(INTERNAL, data)
      }
      if (image.status === 'failed' || image.status === 'error') {
        throw new ImageBuildFailedError(image.error ?? `image pull ${image.id} failed`, { statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: image.id, buildError: image.error ?? undefined })
      }
      return image
    } catch (error) {
      // The pull's deadline caused this: its own signal fired, or the request
      // whose budget was the deadline's remainder ran out of it (axios' timer
      // can fire before the clock reads the deadline). A timeout an auth
      // provider raised on its own keeps its error.
      if (controller.signal.aborted || (isRequestBudgetTimeout(error) && (
        activeRequestUsesDeadline
        || (deadline !== undefined && Date.now() >= deadline)
      ))) throw timeoutError()
      throw error
    } finally {
      if (timer !== undefined) clearTimeout(timer)
    }
  }

  /**
   * Name an ANONYMOUS image, making it permanent (`PATCH /images/{id}`).
   * Promotion only: an image that already has a name answers 409 — renaming a
   * named image is not offered (sandboxes reference images by id, so a rename
   * would silently relabel every sandbox created from it). A name that already
   * exists throws `ImageNameTakenError`; a reserved prefix (`img_`, `snap_`,
   * `sbx_`, `vol_`, `fn_`) throws `ImageNameReservedError`.
   */
  public async promote(imageId: string, name: string): Promise<Image> {
    const path = `/images/${pathSegment(imageId, 'imageId')}`
    const promoted: unknown = name
    if (typeof promoted !== 'string' || isBlank(promoted)) throw badRequest('name must be a non-empty string')
    const { data } = await this.http.patch<api.ImageListItem>(path, { name } satisfies api.PromoteImageRequest)
    return new Image(INTERNAL, data)
  }

  public async delete(imageId: string): Promise<void> {
    await this.http.delete(`/images/${pathSegment(imageId, 'imageId')}`)
  }
}

export { Snapshot }
