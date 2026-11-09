/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ClientTimeoutError, ConnectionError, DaytonaError, ImageBuildFailedError, RequestTimeoutError, SandboxCreateConnectionError, SandboxCreateRequestTimeoutError, TransferCancelledError } from './errors/DaytonaError.js'
import { sandboxState, sleep } from './http.js'
import { eventDestinationFromHeaders } from './events.js'
import { scopedProxyToolboxUrl } from './Sandbox.js'
import type { Sandbox, SandboxConnectOptionsFromCreate } from './Sandbox.js'
import { imageOptionsBody, isImageBuilder } from './Image.js'
import type { Image, ImageBuilder } from './Image.js'
import type { ImagesService } from './Images.js'
import { assertKnownOptions, badRequest, optionalSeconds, snapshotOptions, waitDeadline } from './options.js'
import { assertCreateNestedOptions, BUILD_CREATE_KEYS, RESOURCES_KEYS, withoutNullOptions } from './clientOptionKeys.js'
import { createRegion, createWaitSeconds, MAX_WIRE_WAIT_SECONDS, rejectV0Target } from './clientCreateOptions.js'
import { linuxCreateRequest } from './clientCreateRequests.js'
import { createIdempotencyKey } from './clientCreateNames.js'
import type { PreparedSandboxCreate } from './clientCreateRequests.js'
import type { SandboxClientContext } from './clientSandboxes.js'
import type { CreateFromBuildOptions, CreateSandboxOptions, CreateSandboxResponse } from './types.js'
import { WAIT_READ_SLACK_MS } from './ProcessWire.js'

/** @internal */
export async function createSandbox(context: SandboxClientContext, request: PreparedSandboxCreate): Promise<Sandbox> {
  const { path, body, metadata } = request
  const wait = metadata.waitSeconds
  const region = typeof body.region_id === 'string' ? body.region_id : undefined
  // Only name-derived keys await hashing; explicit/random keys post immediately.
  const key = createIdempotencyKey(body)
  const idempotencyKey = typeof key === 'string' ? key : await key
  if (body.idempotency_key === undefined) body.idempotency_key = idempotencyKey
  // The create response first associates the sandbox id with its regional
  // API endpoint for the events hub.
  let createdEventDestination: string | undefined
  // A create that asks the server to hold is a long poll: the hold plus read
  // slack is never cut by a shorter request timeout. No limit (0) stays no
  // limit; a create with waitSeconds 0 is an ordinary request.
  const rootMs = context.api.defaults.timeout
  // A wait above the timer maximum is no limit: no client deadline on the POST either.
  const unbounded = wait > MAX_WIRE_WAIT_SECONDS
  const holdTimeout = wait <= 0 || typeof rootMs !== 'number' || rootMs <= 0 ? undefined
    : unbounded ? 0 : Math.max(rootMs, wait * 1000 + WAIT_READ_SLACK_MS)
  const created = await context.api.post<CreateSandboxResponse>(path, body, {
    ...(holdTimeout === undefined ? {} : { timeout: holdTimeout }),
    headers: { 'Idempotency-Key': idempotencyKey, ...(region ? { 'x-daytona-region': region } : {}) },
  }).then((response) => {
    createdEventDestination = eventDestinationFromHeaders(response.headers) || undefined
    return response.data
  }).catch((error: unknown) => {
    if (error instanceof ConnectionError) throw new SandboxCreateConnectionError(idempotencyKey, error.message)
    if (error instanceof ClientTimeoutError) throw new SandboxCreateRequestTimeoutError(idempotencyKey)
    throw error
  })
  if (typeof created !== 'object' || created === null || typeof created.id !== 'string' || created.id === '') {
    // The contract requires the new sandbox's id; nothing can follow without it.
    throw badRequest('sandbox create response is missing the sandbox id')
  }
  // The sandbox exists once its id came back: a missing or non-string status
  // reads as `unknown`, never an error (as `wait_cap_seconds`).
  const rawStatus: unknown = Reflect.get(created, 'status')
  const status = typeof rawStatus === 'string' ? rawStatus : undefined
  // A create token is refreshable, unlike a caller-supplied one.
  const connectOptions: SandboxConnectOptionsFromCreate = {
    accessToken: created.access_token ?? undefined,
    accessTokenFromCreate: true,
    accessTokenExpiresAt: created.access_token_expires_at ?? undefined,
    autoDelete: metadata.autoDelete,
  }
  // The handle holds only what the response carries, plus an `img_` / `snap_`
  // id the caller passed (a name is never an id), and the class only when the
  // call fixes it (createMacos). A partial DTO would mark absent fields known.
  const booted = created.image
  const sandbox = context.buildSandbox({
    sandboxId: created.id,
    toolboxProxyUrl: scopedProxyToolboxUrl(created.toolbox_proxy_url, created.id),
    name: created.name,
    rawState: status,
    state: sandboxState(status),
    region,
    image: typeof booted?.id === 'string' && booted.id !== ''
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
  // No limit stays no limit (0) after the server's hold.
  const remainingWait = unbounded ? 0 : Math.max(0, wait - waitCap)
  if (remainingWait <= 0 && !unbounded) return sandbox
  try {
    return await sandbox.wait({ until: 'started', timeoutSeconds: remainingWait })
  } catch (error) {
    if (error instanceof DaytonaError && !(error instanceof TransferCancelledError)) return sandbox
    throw error
  }
}

/** @internal */
export async function buildSandboxImage(images: ImagesService, builder: ImageBuilder, options: CreateFromBuildOptions): Promise<{ readonly image: Image; readonly createOptions: CreateSandboxOptions }> {
  if (!isImageBuilder(builder)) throw badRequest('createFromBuild expects an ImageBuilder')
  rejectV0Target(options)
  assertKnownOptions('CreateFromBuildOptions', options, BUILD_CREATE_KEYS, { timeout: 'buildTimeoutSeconds' })
  // An own-key copy read through prototypes: the rest spread below keeps inherited options.
  options = withoutNullOptions(snapshotOptions('CreateFromBuildOptions', options, BUILD_CREATE_KEYS) ?? options)
  assertKnownOptions('Resources', options.resources, RESOURCES_KEYS)
  assertCreateNestedOptions(options)
  const region = createRegion(options)
  createWaitSeconds(options.waitSeconds)
  const { buildTimeoutSeconds, idleExpirySeconds, ...createOptions } = options
  // `null` is Python's `None`: no build bound.
  const budget = optionalSeconds('buildTimeoutSeconds', buildTimeoutSeconds === null ? undefined : buildTimeoutSeconds)
  // Every create option is validated before the (billed) build: a caller
  // error must not cost a build first. The image id is not known yet.
  linuxCreateRequest('img_pending', createOptions)
  // The name-derived key too (it can need Web Crypto), as Python resolves it before the build.
  await createIdempotencyKey({ name: createOptions.name, idempotency_key: createOptions.idempotencyKey })
  // The options the build takes are checked before the (billed) build too.
  imageOptionsBody({ region, idleExpirySeconds, cpuArch: options.cpuArch })
  const image = await images.build(builder, { region, idleExpirySeconds, cpuArch: options.cpuArch })
  // Omitted, 0, or above the timer maximum (about 24.8 days): no deadline.
  const deadline = waitDeadline(budget)
  const readyImage = await waitForBuiltImage(images, image, deadline)
  return { image: readyImage, createOptions }
}

async function waitForBuiltImage(images: ImagesService, initial: Image, deadline: number | undefined): Promise<Image> {
  let image = initial
  for (;;) {
    if (image.status === 'ready') return image
    if (image.status === 'failed' || image.status === 'error') throw new ImageBuildFailedError(image.error ?? `image ${image.id} build failed`, { statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: image.id, buildError: image.error ?? undefined })
    const remainingTimeout = deadline === undefined ? 0 : (deadline - Date.now()) / 1000
    if (deadline !== undefined && remainingTimeout <= 0) {
      throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    }
    try {
      const started = Date.now()
      image = await images.get(image.id, { wait: 'ready', timeoutSeconds: remainingTimeout })
      if (image.status === 'ready' || image.status === 'failed' || image.status === 'error') continue
      const pause = Math.min(1000 - (Date.now() - started), deadline === undefined ? Infinity : deadline - Date.now())
      if (pause > 0) await sleep(pause)
    } catch (error) {
      // Only the client's own deadline is the build timeout; a server 408 keeps its source, as in Python.
      // The artifact wait's own timeout (not a client read timer) is always this
      // deadline running out: the wait shares it.
      const waitRanOut = !(error instanceof ClientTimeoutError) || (deadline !== undefined && Date.now() >= deadline)
      if (error instanceof DaytonaError && error.source === 'SDK' && error.code === 'REQUEST_TIMEOUT' && deadline !== undefined && waitRanOut) {
        throw new RequestTimeoutError('image build did not finish before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      }
      throw error
    }
  }
}
