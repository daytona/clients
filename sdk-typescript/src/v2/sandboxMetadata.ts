/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { artifactRefOf } from './Image.js'
import { regionOf } from './events.js'
import { SandboxClass as SANDBOX_CLASSES } from './gen/api-enums.js'
import { parseSandboxLifecycle, sandboxState } from './http.js'
import { mibToGib } from './resources.js'
import { badRequest } from './options.js'
import type { ImageRef, MacBootMode, SandboxClass, SandboxDto, SandboxHandleJSON, SandboxInfo, SandboxLifecycle } from './types.js'

/** The handle's current observation; only the handle updates this storage. */
export type SandboxMetadata = {
  -readonly [K in keyof Omit<SandboxHandleJSON, 'sandboxId'>]: SandboxHandleJSON[K]
} & { macBootMode: MacBootMode | null }

export function isSandboxInfo(value: SandboxDto | SandboxInfo): value is SandboxInfo {
  return 'sandboxId' in value
}

function sandboxClass(value: string | undefined): SandboxClass | 'unknown' | undefined {
  if (value === undefined) return undefined
  return SANDBOX_CLASSES.find((known) => known === value) ?? 'unknown'
}

function region(id: string, explicit: string | undefined): string | undefined {
  const inferred = regionOf(id)
  return explicit || (inferred === '_' ? undefined : inferred)
}

export function parseLabels(value: unknown): Readonly<Record<string, string>> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return Object.freeze({})
  return Object.freeze(Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string')))
}

/**
 * Refuse a sandbox object from the API that breaks the contract: every one
 * carries its `class`. A `BadRequestError` (400, `BAD_REQUEST`, source `SDK`).
 * @internal
 */
export function assertSandboxResponse(sandbox: unknown): asserts sandbox is SandboxDto {
  if (typeof sandbox !== 'object' || sandbox === null || Array.isArray(sandbox)) throw badRequest('expected a sandbox object')
  const wireClass: unknown = Reflect.get(sandbox, 'class')
  if (typeof wireClass !== 'string' || wireClass === '') throw badRequest('sandbox response is missing its class')
}

export function metadataFromSandbox(sandbox: SandboxDto): SandboxMetadata {
  return {
    name: sandbox.name,
    image: artifactRefOf(sandbox.image) ?? undefined,
    snapshot: artifactRefOf(sandbox.snapshot) ?? undefined,
    labels: parseLabels(sandbox.labels),
    cpu: sandbox.cpu,
    cpuMax: sandbox.cores_max ?? undefined,
    memory: mibToGib(sandbox.memory_mib),
    memoryMax: mibToGib(sandbox.memory_max_mib),
    disk: mibToGib(sandbox.disk_mib),
    diskMax: mibToGib(sandbox.disk_max_mib),
    rawState: sandbox.status ?? undefined,
    state: sandboxState(sandbox.status),
    error: sandbox.error ?? undefined,
    macBootMode: sandbox.mac_boot_mode ?? null,
    imageBuildId: sandbox.image_build_id ?? undefined,
    // Every API sandbox object carries `class` (checked at the boundary with
    // assertSandboxResponse); partial internal data leaves it unknown.
    sandboxClass: sandboxClass(sandbox.class ?? undefined),
    rawSandboxClass: sandbox.class ?? undefined,
    region: region(sandbox.id, sandbox.region_id),
    persistent: sandbox.persistent ?? undefined,
    user: sandbox.user ?? undefined,
    spot: sandbox.spot ?? undefined,
    spotEvictedAt: sandbox.spot_evicted_at ?? undefined,
    lifecyclePolicy: sandbox.lifecycle_policy ? Object.freeze(parseSandboxLifecycle(sandbox.lifecycle_policy)) : undefined,
    forkedFrom: sandbox.forked_from ?? undefined,
    reservationId: sandbox.reservation_id ?? undefined,
  }
}

export function metadataFromInfo(info: SandboxInfo): SandboxMetadata {
  return {
    name: info.name,
    image: infoArtifact(info.image),
    snapshot: infoArtifact(info.snapshot),
    labels: parseLabels(info.labels),
    cpu: info.cpu,
    cpuMax: info.cpuMax,
    memory: info.memory,
    memoryMax: info.memoryMax,
    disk: info.disk,
    diskMax: info.diskMax,
    state: info.state,
    rawState: info.rawState,
    error: info.error,
    macBootMode: null,
    imageBuildId: info.imageBuildId,
    sandboxClass: sandboxClass(info.rawSandboxClass ?? info.sandboxClass),
    rawSandboxClass: info.rawSandboxClass ?? info.sandboxClass,
    region: region(info.sandboxId, info.region),
    persistent: info.persistent,
    user: info.user,
    spot: info.spot,
    spotEvictedAt: info.spotEvictedAt,
    lifecyclePolicy: info.lifecyclePolicy ? Object.freeze({ ...info.lifecyclePolicy }) : undefined,
    forkedFrom: info.forkedFrom,
    reservationId: info.reservationId,
  }
}

function infoArtifact(value: ImageRef | undefined): ImageRef | undefined {
  // Internal fixtures may still carry the historical string form; it is not
  // enough information to identify an artifact and never becomes a reference.
  if (value === undefined || typeof value === 'string') return undefined
  return artifactRefOf(value) ?? undefined
}

function knownFields<T extends object>(value: T): T {
  const output = { ...value }
  for (const [key, field] of Object.entries(output)) {
    if (field === undefined || field === null) Reflect.deleteProperty(output, key)
  }
  return output
}

function lifecycleJSON(policy: SandboxLifecycle | undefined): SandboxLifecycle | undefined {
  if (policy === undefined) return undefined
  return knownFields({
    timeoutSeconds: policy.timeoutSeconds,
    onTimeout: policy.onTimeout,
    // Timestamps are the server's strings, verbatim, as Python writes them.
    timeoutAt: policy.timeoutAt,
    idleExpirySeconds: policy.idleExpirySeconds,
    onIdleTimeout: policy.onIdleTimeout,
    idleTimeoutAt: policy.idleTimeoutAt,
    lastActivityAt: policy.lastActivityAt,
    lastActivitySource: policy.lastActivitySource,
  })
}

export function sandboxHandleJSON(id: string, data: SandboxMetadata): SandboxHandleJSON {
  return knownFields({
    sandboxId: id,
    name: data.name,
    state: data.state !== 'unknown' || data.rawState ? data.state : undefined,
    rawState: data.rawState || undefined,
    image: data.image ? { id: data.image.id, name: data.image.name } : undefined,
    snapshot: data.snapshot ? { id: data.snapshot.id, name: data.snapshot.name } : undefined,
    region: data.region,
    labels: data.labels && Object.keys(data.labels).length > 0 ? { ...data.labels } : undefined,
    cpu: data.cpu,
    cpuMax: data.cpuMax,
    memory: data.memory,
    memoryMax: data.memoryMax,
    disk: data.disk,
    diskMax: data.diskMax,
    sandboxClass: data.sandboxClass,
    rawSandboxClass: data.rawSandboxClass,
    persistent: data.persistent,
    spot: data.spot,
    spotEvictedAt: data.spotEvictedAt,
    lifecyclePolicy: lifecycleJSON(data.lifecyclePolicy),
    forkedFrom: data.forkedFrom,
    reservationId: data.reservationId,
    user: data.user,
    error: data.error,
    imageBuildId: data.imageBuildId,
  })
}
