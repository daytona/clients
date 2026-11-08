/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { followBuildLogs } from './BuildLogs.js'
import type { BuildLogFollowOptions, BuildLogFollowResult } from './BuildLogs.js'
import { Image, ImageBuilder, Snapshot, buildImageRequest, isImageBuilder } from './Image.js'
import { HttpTransport, boundedRequestTimeout, isRequestBudgetTimeout, strictUriEncode, transportFromAxiosInstance } from './http.js'
import type { HttpTransportConfig } from './http.js'
import type * as api from './gen/api-types.js'
import type { BuildImageOptions, ListResult } from './types.js'
import { ImageBuildFailedError, RequestTimeoutError } from './errors/DaytonaError.js'
import { assertKnownOptions, badRequest, optionKeys, optionalSeconds } from './options.js'
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
  /** Total pull budget in seconds; omitted or zero is unbounded. */
  readonly timeoutSeconds?: number
}

const BUILD_IMAGE_OPTION_KEYS = optionKeys<BuildImageOptions>()({ name: true, idleExpirySeconds: true, region: true, cpuArch: true })
const IMAGE_PULL_OPTION_KEYS = optionKeys<ImagePullOptions>()({ ...BUILD_IMAGE_OPTION_KEYS, wait: true, timeoutSeconds: true })
const LIST_IMAGES_OPTION_KEYS = optionKeys<ListImagesOptions>()({ cursor: true, limit: true, name: true, anonymous: true, sandboxId: true, platform: true })

const IMAGE_LONGPOLL_MAX_MS = 60_000
const IMAGE_HOLD_READ_SLACK_MS = 5_000

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
    return followBuildLogs(this.getApiTransport(), `/images/${strictUriEncode(imageId)}/build-logs-url`, options)
  }

  public async build(builder: ImageBuilder, options: BuildImageOptions = {}): Promise<Image> {
    if (!isImageBuilder(builder)) throw badRequest('images.build() takes an ImageBuilder (ImageBuilder.fromDockerfile(...), .fromRegistry(...))')
    assertKnownOptions('BuildImageOptions', options, BUILD_IMAGE_OPTION_KEYS)
    const { data } = await this.http.post<api.CreateImageResponse>('/images', buildImageRequest(builder, options))
    return this.get(data.id)
  }

  public async list(options: ListImagesOptions = {}): Promise<ListResult<Image>> {
    assertKnownOptions('ListImagesOptions', options, LIST_IMAGES_OPTION_KEYS)
    const { data } = await this.http.get<api.ImagePage>('/images', {
      params: {
        cursor: options.cursor,
        limit: options.limit,
        name: options.name,
        anonymous: options.anonymous,
        sandbox_id: options.sandboxId,
        platform: options.platform,
      },
    })
    const page = listPage<api.ImageListItem>(data, 'images', 'image list')
    return { items: page.items.map((image) => new Image(INTERNAL, image)), nextCursor: page.nextCursor }
  }

  public async *iter(options: ListImagesOptions = {}): AsyncIterable<Image> {
    assertKnownOptions('ListImagesOptions', options, LIST_IMAGES_OPTION_KEYS)
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
    const data = await getArtifact<api.ImageListItem>(this.http, `/images/${strictUriEncode(imageId)}`, options)
    return new Image(INTERNAL, data)
  }

  /** Import a registry image and wait until ready by default. Omitted or zero timeoutSeconds is unbounded. */
  public async pull(ref: string, options: ImagePullOptions = {}): Promise<Image> {
    assertKnownOptions('ImagePullOptions', options, IMAGE_PULL_OPTION_KEYS)
    const timeoutSeconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds)
    const deadline = timeoutSeconds === undefined || timeoutSeconds === 0 ? undefined : Date.now() + timeoutSeconds * 1000
    const timeoutError = () => new RequestTimeoutError('image pull did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeRequestUsesDeadline = false
    const armDeadline = (): void => {
      if (deadline === undefined) return
      const remaining = deadline - Date.now()
      if (remaining <= 0) controller.abort()
      // Re-arm very large budgets instead of overflowing the platform timer.
      else timer = setTimeout(armDeadline, Math.min(Math.ceil(remaining), 2_147_483_647))
    }
    const requestOptions = (requestTimeout = this.http.defaults.timeout) => {
      const timeout = deadline === undefined ? requestTimeout : boundedRequestTimeout(deadline, requestTimeout)
      if (timeout === undefined || controller.signal.aborted) throw timeoutError()
      // Axios's timer can fire before the final clock tick, including rounded/equal budgets.
      activeRequestUsesDeadline = deadline !== undefined && timeout >= deadline - Date.now()
      return { timeout: timeout > 2_147_483_647 ? 0 : timeout, signal: controller.signal }
    }
    const checkDeadline = (): void => {
      if (deadline !== undefined && Date.now() >= deadline) throw timeoutError()
    }
    armDeadline()
    try {
      const { data: submitted } = await this.http.post<api.CreateImageResponse>('/images', buildImageRequest(ImageBuilder.fromRegistry(ref), options), requestOptions())
      const { data: initial } = await this.http.get<api.ImageListItem>(`/images/${strictUriEncode(submitted.id)}`, requestOptions())
      checkDeadline()
      let image = new Image(INTERNAL, initial)
      if (options.wait === false) return image
      while (image.status !== 'ready' && image.status !== 'failed' && image.status !== 'error') {
        checkDeadline()
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
   * would silently relabel every sandbox created from it).
   */
  public async promote(imageId: string, name: string): Promise<Image> {
    const { data } = await this.http.patch<api.ImageListItem>(`/images/${strictUriEncode(imageId)}`, { name } satisfies api.PromoteImageRequest)
    return new Image(INTERNAL, data)
  }

  public async delete(imageId: string): Promise<void> {
    await this.http.delete(`/images/${strictUriEncode(imageId)}`)
  }
}

export { Snapshot }
