/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertKnownOptions, assertLabels, assertOptionsObject, badRequest, isPlainStringRecord, optionKeys } from './options.js'
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

/**
 * The create and fork options a JavaScript `null` leaves out, exactly where
 * Python's `None` does for that parameter. `waitSeconds` is not here (Python
 * refuses `None`), nor `networkPolicy` (Python's `None` sends an explicit
 * null policy), nor the TypeScript-only `autoDelete` (a boolean).
 * @internal
 */
export const NULL_MEANS_OMITTED: ReadonlySet<string> = new Set([
  'name', 'labels', 'ports', 'lifecyclePolicy', 'region', 'createTimeoutSeconds', 'idempotencyKey',
  'volumes', 'env', 'resources', 'secrets', 'spot', 'user', 'cpuArch', 'cpuType', 'persistent',
  'reservation', 'autoRenew', 'idleExpirySeconds', 'buildTimeoutSeconds', 'memory',
])

/**
 * The options with every `null` that Python reads as an omitted `None`
 * removed (a copy; read through prototypes, as the key check is).
 * @internal
 */
export function withoutNullOptions<T extends object>(options: T): T {
  if (typeof options !== 'object' || options === null) return options
  let copy: Record<string, unknown> | undefined
  for (const key of NULL_MEANS_OMITTED) {
    if (Reflect.get(options, key) !== null) continue
    // SAFETY: an options object is a string-keyed record.
    copy ??= Object.fromEntries(enumerableEntries(options as Readonly<Record<string, unknown>>))
    copy[key] = undefined
  }
  // SAFETY: the same keys and values, with Python's omitted `None`s left out.
  return (copy ?? options) as T
}

function enumerableEntries(options: Readonly<Record<string, unknown>>): [string, unknown][] {
  const entries: [string, unknown][] = []
  for (const key in options) entries.push([key, Reflect.get(options, key)])
  return entries
}

/** The create options whose values are checked before any request (shared by every create route). */
interface CreateValueOptions extends CreateRequestBaseOptions {
  readonly volumes?: readonly VolumeMount[]
  readonly env?: Readonly<Record<string, string>>
  readonly secrets?: readonly string[]
  readonly spot?: boolean
  readonly user?: string
  readonly cpuArch?: string
  readonly cpuType?: string
  readonly persistent?: boolean
  readonly reservation?: string
  readonly autoRenew?: boolean
}

function optionalTyped(options: CreateValueOptions, key: keyof CreateValueOptions, kind: 'string' | 'boolean'): void {
  const value: unknown = Reflect.get(options, key)
  if (value !== undefined && typeof value !== kind) throw badRequest(`${key} must be a ${kind}`)
}

function arrayOf(value: unknown, message: string): readonly unknown[] | undefined {
  if (value === undefined) return undefined
  if (!Array.isArray(value)) throw badRequest(message)
  return value
}

/**
 * Every create route's option values, checked before any request (and, for
 * createFromBuild, before the build): a mistyped value is refused, never
 * forwarded. Nested entries must be options objects with known keys.
 */
export function assertCreateNestedOptions(options: CreateValueOptions): void {
  // `null` means no policy, and a non-object value gets the lifecycle
  // validator's own message; only an options object has keys to check.
  const policy = options.lifecyclePolicy
  if (policy !== null && typeof policy === 'object' && !Array.isArray(policy)) assertKnownOptions('LifecyclePolicyOptions', policy, LIFECYCLE_POLICY_KEYS)
  for (const port of arrayOf(options.ports, 'ports must be an array of { port } objects') ?? []) {
    assertOptionsObject('PortMapping', port)
    assertKnownOptions('PortMapping', port, PORT_KEYS)
    const { port: number, proto } = port as PortMapping
    if (typeof number !== 'number' || !Number.isInteger(number) || number < 1 || number > 65535) throw badRequest('ports[].port must be an integer in 1..65535')
    // The contract's PortProtocol values only: SDK inputs are closed.
    if (proto !== undefined && proto !== 'tcp' && proto !== 'udp') throw badRequest("ports[].proto must be 'tcp' or 'udp'")
  }
  for (const volume of arrayOf(options.volumes, 'volumes must be an array of { volumeId, mountPath } objects') ?? []) {
    assertOptionsObject('VolumeMount', volume)
    assertKnownOptions('VolumeMount', volume, VOLUME_KEYS)
    const { volumeId, mountPath, subpath, readOnly } = volume as VolumeMount
    if (typeof volumeId !== 'string' || volumeId.trim() === '') throw badRequest('volumes[].volumeId must be a non-empty string')
    if (typeof mountPath !== 'string' || mountPath.trim() === '') throw badRequest('volumes[].mountPath must be a non-empty string')
    if (subpath !== undefined && typeof subpath !== 'string') throw badRequest('volumes[].subpath must be a string')
    if (readOnly !== undefined && typeof readOnly !== 'boolean') throw badRequest('volumes[].readOnly must be a boolean')
  }
  for (const secret of arrayOf(options.secrets, 'secrets must be an array of secret names') ?? []) {
    if (typeof secret !== 'string' || secret === '') throw badRequest('secrets must be an array of secret names')
  }
  if (options.env !== undefined && !isPlainStringRecord(options.env)) throw badRequest('env must be a plain object of string to string')
  if (options.labels !== undefined) assertLabels(options.labels)
  if (options.name !== undefined && typeof options.name !== 'string') throw badRequest('name must be a string')
  for (const key of ['user', 'cpuArch', 'cpuType', 'reservation'] as const) optionalTyped(options, key, 'string')
  for (const key of ['spot', 'persistent', 'autoRenew', 'autoDelete'] as const) optionalTyped(options, key, 'boolean')
}
