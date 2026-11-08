/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { followBuildLogs } from './BuildLogs.js'
import type { BuildLogFollowOptions, BuildLogFollowResult } from './BuildLogs.js'
import { Image, ImageBuilder, Snapshot, buildImageRequest } from './Image.js'
import { HttpTransport, boundedRequestTimeout, isRequestBudgetTimeout, strictUriEncode, transportFromAxiosInstance } from './http.js'
import type { HttpTransportConfig } from './http.js'
import type * as api from './gen/api-types.js'
import type { BuildImageOptions, ListResult } from './types.js'
import { BadRequestError, ImageBuildFailedError, RequestTimeoutError } from './errors/DaytonaError.js'

const IMAGE_LONGPOLL_MAX_MS = 60_000

function requestTimeoutMs(timeout: number | undefined): number | undefined {
  if (timeout === undefined) return undefined
  if (!Number.isFinite(timeout) || timeout < 0) throw new TypeError('timeout must be a non-negative finite number')
  return timeout === 0 ? 0 : Math.min(Math.ceil(timeout * 1000), IMAGE_LONGPOLL_MAX_MS)
}

export class ImagesService {
  private resolvedApiTransport?: HttpTransport

  constructor(
    private readonly http: AxiosInstance,
    private readonly apiTransport?: HttpTransport | HttpTransportConfig,
  ) {
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
    const { data } = await this.http.post<api.CreateImageResponse>('/images', buildImageRequest(builder, options))
    return this.get(data.id)
  }

  public async list(options: { cursor?: string; limit?: number; name?: string; anonymous?: boolean; sandboxId?: string; platform?: 'true' | 'false' | 'any' } = {}): Promise<ListResult<Image>> {
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
    return { items: data.images.map((image) => new Image(image)), nextCursor: data.next_cursor ?? undefined }
  }

  public async *iter(options: { cursor?: string; limit?: number; name?: string; anonymous?: boolean; sandboxId?: string; platform?: 'true' | 'false' | 'any' } = {}): AsyncIterable<Image> {
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

  public async get(imageId: string, options: { wait?: 'ready'; timeout?: number } = {}): Promise<Image> {
    const timeoutMs = requestTimeoutMs(options.timeout)
    const { data } = await this.http.get<api.ImageListItem>(`/images/${strictUriEncode(imageId)}`, {
      params: options.wait === undefined ? undefined : {
        wait: options.wait,
        timeout_ms: timeoutMs === undefined || timeoutMs === 0 ? undefined : timeoutMs,
      },
      timeout: options.wait === undefined ? undefined : timeoutMs === 0 ? 0 : timeoutMs === undefined ? 0 : timeoutMs + 5_000,
    })
    return new Image(data)
  }

  /** Import a registry image and wait until ready by default. Timeout is in milliseconds; omitted or zero is unbounded. */
  public async pull(ref: string, options: BuildImageOptions & { readonly wait?: boolean; readonly timeout?: number } = {}): Promise<Image> {
    if (options.timeout !== undefined && (!Number.isFinite(options.timeout) || options.timeout < 0)) {
      throw new BadRequestError('timeout must be a non-negative finite number', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    const deadline = options.timeout === undefined || options.timeout === 0 ? undefined : Date.now() + options.timeout
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
      let image = new Image(initial)
      if (options.wait === false) return image
      while (image.status !== 'ready' && image.status !== 'failed' && image.status !== 'error') {
        const remaining = deadline === undefined ? undefined : Math.min(Math.ceil(deadline - Date.now()), IMAGE_LONGPOLL_MAX_MS)
        checkDeadline()
        // Match get(wait='ready'): the server wait owns the request budget, not the root timeout.
        const config = requestOptions(remaining === undefined ? 0 : remaining + 5_000)
        const { data } = await this.http.get<api.ImageListItem>(`/images/${strictUriEncode(image.id)}`, {
          ...config,
          params: { wait: 'ready', timeout_ms: remaining },
        })
        checkDeadline()
        image = new Image(data)
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
    return new Image(data)
  }

  public async delete(imageId: string): Promise<void> {
    await this.http.delete(`/images/${strictUriEncode(imageId)}`)
  }
}

export { Snapshot }
