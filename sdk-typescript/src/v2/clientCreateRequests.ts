/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import type { CreateMacosSandboxOptions, CreateSandboxFromSnapshotOptions, CreateSandboxOptions } from './types.js'
import { assertLifecycleClassSupport, lifecycleBody } from './http.js'
import { assertKnownOptions, badRequest } from './options.js'
import { assertCreateNestedOptions, CREATE_KEYS, SNAPSHOT_CREATE_KEYS, MACOS_CREATE_KEYS, RESOURCES_KEYS, MACOS_RESOURCES_KEYS } from './clientOptionKeys.js'
import { createRegion, rejectV0Target, createWaitSeconds, createTimeoutSeconds, gibToMib, imageSourceString, linuxCreateArguments, macosCores, macosMib, resolveIdempotencyKey, snapshotSourceString } from './clientCreateOptions.js'
import { labelsWithName } from './clientCreateNames.js'
import { createNetworkPolicyCreateField } from './NetworkPolicy.js'

// `image` is optional: an imageless Linux create boots the platform default.
type LinuxCreateRequest = Omit<api.CreateSandboxRequest, 'image'> & {
  readonly image?: string
  readonly network_policy?: Record<string, unknown> | null
}

/** @internal */
export interface PreparedSandboxCreate {
  readonly path: string
  readonly body: {
    readonly name?: string | null
    readonly wait_seconds?: number | null
    readonly region_id?: string | null
    // Submission fills the generated key when the caller omitted it.
    idempotency_key?: string | null
  }
  readonly metadata: {
    readonly autoDelete: boolean
    readonly image?: string
    readonly snapshot?: string
    readonly sandboxClass?: 'macos'
  }
}

/** @internal */
export function linuxCreateRequest(imageOrOptions: string | CreateSandboxOptions | undefined, maybeOptions: CreateSandboxOptions | undefined): PreparedSandboxCreate {
  const { source, options } = linuxCreateArguments(imageOrOptions, maybeOptions)
  rejectV0Target(options)
  assertKnownOptions('CreateSandboxOptions', options, CREATE_KEYS)
  assertKnownOptions('Resources', options.resources, RESOURCES_KEYS)
  assertCreateNestedOptions(options)
  for (const axis of ['cpu', 'cpuMax'] as const) {
    const cores: unknown = options.resources?.[axis]
    if (cores !== undefined && (typeof cores !== 'number' || !Number.isFinite(cores) || cores <= 0)) {
      throw badRequest(`resources.${axis} must be a positive number of cores`)
    }
  }
  const route = 'linux'
  const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateSandboxRequest['lifecycle_policy']
  assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: route, persistent: false })
  const body: LinuxCreateRequest = {
    name: options.name,
    ...(source !== undefined ? { image: source } : {}),
    env: options.env,
    region_id: createRegion(options),
    cpu: options.resources?.cpu,
    cores_max: options.resources?.cpuMax,
    memory_mib: options.resources?.memory !== undefined ? gibToMib(options.resources.memory, 'memory') : undefined,
    memory_max_mib: options.resources?.memoryMax !== undefined ? gibToMib(options.resources.memoryMax, 'memoryMax') : undefined,
    disk_mib: options.resources?.disk !== undefined ? gibToMib(options.resources.disk, 'disk') : undefined,
    disk_max_mib: options.resources?.diskMax !== undefined ? gibToMib(options.resources.diskMax, 'diskMax') : undefined,
    ports: options.ports?.map((port) => ({ port: port.port, proto: port.proto ?? 'tcp' })),
    volumes: options.volumes?.map((volume) => ({ volume: volume.volumeId, mount_path: volume.mountPath, subpath: volume.subpath, read_only: volume.readOnly === true ? true : undefined })),
    secrets: options.secrets === undefined ? undefined : [...options.secrets],
    labels: labelsWithName(options.name, options.labels),
    spot: options.spot,
    lifecycle_policy: lifecyclePolicy,
    network_policy: createNetworkPolicyCreateField(options),
    cpu_arch: options.cpuArch,
    cpu_type: options.cpuType,
    user: options.user,
    wait_seconds: createWaitSeconds(options.waitSeconds),
    create_timeout_seconds: createTimeoutSeconds(options.createTimeoutSeconds),
    idempotency_key: resolveIdempotencyKey(options.idempotencyKey),
  }
  return { path: '/sandboxes', body, metadata: { autoDelete: options.autoDelete ?? false, image: source } }
}

/** @internal */
export function snapshotCreateRequest(snapshot: string, options: CreateSandboxFromSnapshotOptions): PreparedSandboxCreate {
  rejectV0Target(options)
  assertKnownOptions('CreateSandboxFromSnapshotOptions', options, SNAPSHOT_CREATE_KEYS)
  assertCreateNestedOptions(options)
  const source = snapshotSourceString(snapshot)
  const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateSandboxFromSnapshotRequest['lifecycle_policy']
  assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: 'linux', persistent: false })
  const body: api.CreateSandboxFromSnapshotRequest = {
    name: options.name,
    snapshot: source,
    labels: labelsWithName(options.name, options.labels),
    lifecycle_policy: lifecyclePolicy,
    ports: options.ports?.map((port) => ({ port: port.port, proto: port.proto ?? 'tcp' })),
    region_id: createRegion(options),
    wait_seconds: createWaitSeconds(options.waitSeconds),
    create_timeout_seconds: createTimeoutSeconds(options.createTimeoutSeconds),
    idempotency_key: resolveIdempotencyKey(options.idempotencyKey),
  }
  return { path: '/sandboxes/from-snapshot', body, metadata: { autoDelete: options.autoDelete ?? false, snapshot: source } }
}

/** @internal */
export function macosCreateRequest(image: string, options: CreateMacosSandboxOptions): PreparedSandboxCreate {
  rejectV0Target(options)
  assertKnownOptions('CreateMacosSandboxOptions', options, MACOS_CREATE_KEYS)
  assertKnownOptions('MacosResources', options.resources, MACOS_RESOURCES_KEYS)
  assertCreateNestedOptions(options)
  const source = imageSourceString(image, 'createMacos')
  if (options.autoRenew !== undefined && options.reservation !== 'new') {
    throw badRequest('macos create takes autoRenew only with reservation: "new"')
  }
  const cores = macosCores(options.resources?.cpu)
  const memoryMib = macosMib(options.resources?.memory, 'memory', 4096)
  const diskMib = macosMib(options.resources?.disk, 'disk', 81920)
  // SAFETY: lifecycleBody validates and serializes the shared lifecycle fields.
  const lifecyclePolicy = lifecycleBody(options.lifecyclePolicy, { creating: true }) as api.CreateMacosSandboxRequest['lifecycle_policy']
  assertLifecycleClassSupport(options.lifecyclePolicy ?? undefined, { sandboxClass: 'macos', persistent: options.persistent === true })
  const body: api.CreateMacosSandboxRequest = {
    name: options.name,
    image: source,
    env: options.env,
    secrets: options.secrets === undefined ? undefined : [...options.secrets],
    region_id: createRegion(options),
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
    // The helper checks property presence and validates before serializing.
    network_policy: createNetworkPolicyCreateField(options),
  }
  return { path: '/sandboxes/macos', body, metadata: { autoDelete: options.autoDelete ?? false, image: source, sandboxClass: 'macos' } }
}
