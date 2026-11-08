/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertKnownOptions, optionKeys } from './options.js'
import type {
  CreateRequestBaseOptions, CreateSandboxOptions, CreateSandboxFromSnapshotOptions,
  CreateMacosSandboxOptions, CreateFromBuildOptions, FindByNameOptions, GetLimitsOptions,
  ListSandboxesOptions, SearchSandboxesOptions, SandboxConnectOptions, Resources,
  MacosResources, PortMapping, VolumeMount, LifecyclePolicyOptions,
} from './types.js'

const BASE_KEYS = optionKeys<CreateRequestBaseOptions>()({
  name: true, labels: true, ports: true, lifecyclePolicy: true, region: true,
  waitSeconds: true, createTimeoutSeconds: true, idempotencyKey: true, autoDelete: true,
})
export const CREATE_KEYS = optionKeys<CreateSandboxOptions>()({
  ...BASE_KEYS, volumes: true, env: true, resources: true, secrets: true, spot: true,
  user: true, networkPolicy: true, cpuArch: true, cpuType: true,
})
export const SNAPSHOT_CREATE_KEYS = optionKeys<CreateSandboxFromSnapshotOptions>()(BASE_KEYS)
export const MACOS_CREATE_KEYS = optionKeys<CreateMacosSandboxOptions>()({
  ...BASE_KEYS, volumes: true, env: true, secrets: true, resources: true, persistent: true,
  reservation: true, autoRenew: true, networkPolicy: true,
})
export const BUILD_CREATE_KEYS = optionKeys<CreateFromBuildOptions>()({
  ...CREATE_KEYS, idleExpirySeconds: true, buildTimeoutSeconds: true,
})
export const FIND_KEYS = optionKeys<FindByNameOptions>()({ region: true })
export const LIMITS_KEYS = optionKeys<GetLimitsOptions>()({ organizationId: true })
export const LIST_KEYS = optionKeys<ListSandboxesOptions>()({ cursor: true, limit: true })
export const SEARCH_KEYS = optionKeys<SearchSandboxesOptions>()({
  q: true, namePrefix: true, idPrefix: true, states: true, sandboxClass: true, region: true,
  imageRef: true, imageRefPrefix: true, labels: true, spot: true, forkedFrom: true,
  minCores: true, maxCores: true, minMemoryMib: true, maxMemoryMib: true,
  minDiskMib: true, maxDiskMib: true, createdStartAt: true, createdEndAt: true,
  updatedStartAt: true, updatedEndAt: true, lastActivityStartAt: true,
  lastActivityEndAt: true, sort: true, order: true, cursor: true, limit: true,
})
export const CONNECT_KEYS = optionKeys<SandboxConnectOptions>()({ toolboxUrl: true, accessToken: true, autoDelete: true })
export const RESOURCES_KEYS = optionKeys<Resources>()({ cpu: true, cpuMax: true, memory: true, memoryMax: true, disk: true, diskMax: true })
export const MACOS_RESOURCES_KEYS = optionKeys<MacosResources>()({ cpu: true, memory: true, disk: true })
const PORT_KEYS = optionKeys<PortMapping>()({ port: true, proto: true })
const VOLUME_KEYS = optionKeys<VolumeMount>()({ volumeId: true, mountPath: true, subpath: true, readOnly: true })
export const LIFECYCLE_POLICY_KEYS = optionKeys<LifecyclePolicyOptions>()({ timeoutSeconds: true, onTimeout: true, idleExpirySeconds: true, onIdleTimeout: true })

export function assertCreateNestedOptions(options: CreateRequestBaseOptions & { readonly volumes?: readonly VolumeMount[] }): void {
  // `null` means no policy, and a non-object value gets the lifecycle
  // validator's own message; only an options object has keys to check.
  const policy = options.lifecyclePolicy
  if (policy !== null && typeof policy === 'object' && !Array.isArray(policy)) assertKnownOptions('LifecyclePolicyOptions', policy, LIFECYCLE_POLICY_KEYS)
  for (const port of options.ports ?? []) assertKnownOptions('PortMapping', port, PORT_KEYS)
  for (const volume of options.volumes ?? []) assertKnownOptions('VolumeMount', volume, VOLUME_KEYS)
}
