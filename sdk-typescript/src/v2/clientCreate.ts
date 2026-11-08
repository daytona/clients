/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ClientTimeoutError, ConnectionError, DaytonaError, ImageBuildFailedError, RequestTimeoutError, SandboxCreateConnectionError, SandboxCreateRequestTimeoutError, SandboxTerminalStateError } from './errors/DaytonaError.js'
import { sandboxState } from './http.js'
import { eventDestinationFromHeaders } from './events.js'
import { scopedProxyToolboxUrl } from './Sandbox.js'
import type { Sandbox, SandboxConnectOptionsFromCreate } from './Sandbox.js'
import { isImageBuilder } from './Image.js'
import type { Image, ImageBuilder } from './Image.js'
import type { ImagesService } from './Images.js'
import { assertKnownOptions, optionalSeconds } from './options.js'
import { assertCreateNestedOptions, BUILD_CREATE_KEYS, RESOURCES_KEYS } from './clientOptionKeys.js'
import { createRegion, createWaitSeconds, rejectV0Target } from './clientCreateOptions.js'
import { createIdempotencyKey } from './clientCreateNames.js'
import type { PreparedSandboxCreate } from './clientCreateRequests.js'
import type { SandboxClientContext } from './clientSandboxes.js'
import type { CreateFromBuildOptions, CreateSandboxOptions, CreateSandboxResponse } from './types.js'

/** @internal */
export async function createSandbox(context: SandboxClientContext, request: PreparedSandboxCreate): Promise<Sandbox> {
  const { path, body, metadata } = request
  const wait = createWaitSeconds(body.wait_seconds ?? undefined)
  const region = typeof body.region_id === 'string' ? body.region_id : undefined
  // Only name-derived keys await hashing; explicit/random keys post immediately.
  const key = createIdempotencyKey(body)
  const idempotencyKey = typeof key === 'string' ? key : await key
  if (body.idempotency_key === undefined) body.idempotency_key = idempotencyKey
  // The create response first associates the sandbox id with its regional
  // API endpoint for the events hub.
  let createdEventDestination: string | undefined
  const created = await context.api.post<CreateSandboxResponse>(path, body, {
    headers: { 'Idempotency-Key': idempotencyKey, ...(region ? { 'x-daytona-region': region } : {}) },
  }).then((response) => {
    createdEventDestination = eventDestinationFromHeaders(response.headers) || undefined
    return response.data
  }).catch((error: unknown) => {
    if (error instanceof ConnectionError) throw new SandboxCreateConnectionError(idempotencyKey, error.message)
    if (error instanceof ClientTimeoutError) throw new SandboxCreateRequestTimeoutError(idempotencyKey)
    throw error
  })
  // A create token is refreshable, unlike a caller-supplied one.
  const connectOptions: SandboxConnectOptionsFromCreate = {
    accessToken: created.access_token ?? undefined,
    accessTokenFromCreate: true,
    accessTokenExpiresAt: created.access_token_expires_at ?? undefined,
    autoDelete: metadata.autoDelete,
  }
  // A handle info carries only known fields; a partial DTO would mark absent
  // fields known and persist their defaults in toJSON().
  const booted = created.image
  const sandbox = context.buildSandbox({
    sandboxId: created.id,
    toolboxProxyUrl: scopedProxyToolboxUrl(created.toolbox_proxy_url, created.id),
    name: created.name,
    rawState: created.status ?? undefined,
    state: sandboxState(created.status),
    region,
    image: typeof booted?.id === 'string' && booted.id.startsWith('img_')
      ? { id: booted.id, name: booted.name ?? null }
      : metadata.image?.startsWith('img_') ? { id: metadata.image, name: null } : undefined,
    snapshot: metadata.snapshot?.startsWith('snap_') ? { id: metadata.snapshot, name: null } : undefined,
    sandboxClass: metadata.sandboxClass,
    rawSandboxClass: metadata.sandboxClass,
    reservationId: created.reservation_id ?? undefined,
  }, connectOptions)
  sandbox.learnEventDestination(createdEventDestination)
  const waitCap = typeof created.wait_cap_seconds === 'number' && Number.isFinite(created.wait_cap_seconds) ? Math.max(0, created.wait_cap_seconds) : 0
  if (wait === 0 || sandbox.state === 'started') return sandbox
  const remainingWait = Math.max(0, wait - waitCap)
  if (remainingWait <= 0) return sandbox
  try {
    return await sandbox.wait({ until: 'started', timeoutSeconds: remainingWait })
  } catch (error) {
    if (error instanceof SandboxTerminalStateError || (error instanceof RequestTimeoutError && error.source === 'SDK')) return sandbox
    throw error
  }
}

/** @internal */
export async function buildSandboxImage(images: ImagesService, builder: ImageBuilder, options: CreateFromBuildOptions): Promise<{ readonly image: Image; readonly createOptions: CreateSandboxOptions }> {
  if (!isImageBuilder(builder)) throw new TypeError('createFromBuild expects an ImageBuilder')
  rejectV0Target(options)
  assertKnownOptions('CreateFromBuildOptions', options, BUILD_CREATE_KEYS, { timeout: 'buildTimeoutSeconds' })
  assertKnownOptions('Resources', options.resources, RESOURCES_KEYS)
  assertCreateNestedOptions(options)
  const region = createRegion(options)
  createWaitSeconds(options.waitSeconds)
  const { buildTimeoutSeconds, idleExpirySeconds, ...createOptions } = options
  const budget = optionalSeconds('buildTimeoutSeconds', buildTimeoutSeconds)
  const deadline = budget === undefined || budget === 0 ? undefined : Date.now() + Math.max(0, Math.ceil(budget * 1000))
  const image = await images.build(builder, { region, idleExpirySeconds, cpuArch: options.cpuArch })
  const readyImage = await waitForBuiltImage(images, image.id, deadline)
  return { image: readyImage, createOptions }
}

async function waitForBuiltImage(images: ImagesService, imageId: string, deadline: number | undefined): Promise<Image> {
  for (;;) {
    const remainingTimeout = deadline === undefined ? 0 : (deadline - Date.now()) / 1000
    if (deadline !== undefined && remainingTimeout <= 0) {
      throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    }
    try {
      const image = await images.get(imageId, { wait: 'ready', timeoutSeconds: remainingTimeout })
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
