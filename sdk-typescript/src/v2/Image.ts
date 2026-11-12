/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { selectRegion } from './region.js'
import { deepFrozen } from './frozen.js'
import type * as api from './gen/api-types.js'
import { ImageRetention as IMAGE_RETENTIONS, SnapshotClass as SNAPSHOT_CLASSES, SnapshotKind as SNAPSHOT_KINDS } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { badRequest } from './options.js'
import { isBlank } from './pyText.js'
import type { ImageBuilder } from './ImageBuilder.js'
export { ImageBuilder, isImageBuilder } from './ImageBuilder.js'
import type { ArtifactRetention, CpuArch, ImageOrigin, ImageSource, ImageStatus, SnapshotClass, SnapshotKind } from './types.js'
import type { ImageModel } from './imageModel.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { arrayOf, checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

// The contract fields an Image / Snapshot maps; a field the contract requires
// must be present, as in Python.
const IMAGE: ResponseFields = {
  id: 'string', anonymous: 'boolean', class: 'string', created_at: 'timestamp', updated_at: 'timestamp', origin: 'string',
  region_id: 'string', retention: 'string', status: 'string', format_version: 'integer', format_outdated: 'boolean',
  region_ids: arrayOf('string'), platform: 'boolean', default: 'boolean', deprecated: 'boolean', name: 'string?',
  bytes: 'integer?', disk_mib: 'integer?', error: 'string?', expires_at: 'timestamp?', layer_count: 'integer?', source: 'object?',
}
const SNAPSHOT: ResponseFields = {
  id: 'string', name: 'string', class: 'string', kind: 'string', status: 'string', region_id: 'string',
  region_ids: arrayOf('string'), created_at: 'timestamp', updated_at: 'timestamp', bytes: 'integer?', memory_bytes: 'integer?',
  error: 'string?', sandbox_id: 'string?',
}

export class Image implements ImageModel {
  public readonly id: string
  public readonly name?: string
  public readonly anonymous: boolean
  public readonly availableIn: readonly string[]
  public readonly bytes?: number
  public readonly sandboxClass: string
  public readonly createdAt: string
  public readonly updatedAt: string
  public readonly error?: string
  public readonly expiresAt?: string
  public readonly layerCount?: number
  public readonly origin: ImageOrigin
  public readonly region: string
  public readonly retention: ArtifactRetention
  public readonly rawRetention: string
  public readonly source?: ImageSource
  public readonly status: ImageStatus
  public readonly formatVersion?: number
  public readonly formatOutdated: boolean
  public readonly platform: boolean
  public readonly default: boolean
  public readonly deprecated: boolean
  public readonly diskMib?: number

  /** @internal */
  constructor(token: Internal, image: api.ImageListItem)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, image: api.ImageListItem) {
    assertInternal(token, 'Image')
    checkResponse<api.ImageListItem>(image, IMAGE, 'image')
    this.id = image.id
    this.name = image.name ?? undefined
    this.anonymous = image.anonymous
    this.availableIn = Object.freeze([...image.region_ids])
    this.bytes = image.bytes ?? undefined
    this.sandboxClass = image.class
    this.createdAt = image.created_at
    this.updatedAt = image.updated_at
    this.error = image.error ?? undefined
    this.expiresAt = image.expires_at ?? undefined
    this.layerCount = image.layer_count ?? undefined
    this.origin = image.origin
    this.region = image.region_id
    this.rawRetention = image.retention
    this.retention = knownOrUnknown(image.retention, IMAGE_RETENTIONS)
    this.source = image.source === undefined || image.source === null ? undefined : deepFrozen(imageSource(image.source))
    this.status = image.status
    this.formatVersion = image.format_version
    this.formatOutdated = image.format_outdated
    this.platform = image.platform
    this.default = image.default
    this.deprecated = image.deprecated
    this.diskMib = image.disk_mib ?? undefined
    // An SDK-owned data model with no live state: frozen at run time.
    Object.freeze(this)
  }
}

export class Snapshot {
  public readonly id: string
  public readonly name: string
  public readonly sandboxClass: SnapshotClass
  public readonly rawSandboxClass: string
  public readonly kind: SnapshotKind
  public readonly rawKind: string
  public readonly availableIn: readonly string[]
  public readonly region: string
  public readonly status: string
  public readonly error?: string
  public readonly createdAt: string
  public readonly updatedAt: string
  public readonly bytes?: number
  public readonly memoryBytes?: number
  public readonly sandboxId?: string

  /** @internal */
  constructor(token: Internal, snapshot: api.Snapshot)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, snapshot: api.Snapshot) {
    assertInternal(token, 'Snapshot')
    checkResponse<api.Snapshot>(snapshot, SNAPSHOT, 'snapshot')
    this.id = snapshot.id
    this.name = snapshot.name
    this.rawSandboxClass = snapshot.class
    this.sandboxClass = knownOrUnknown(snapshot.class, SNAPSHOT_CLASSES)
    this.rawKind = snapshot.kind
    this.kind = knownOrUnknown(snapshot.kind, SNAPSHOT_KINDS)
    this.availableIn = Object.freeze([...snapshot.region_ids])
    this.region = snapshot.region_id
    this.status = snapshot.status
    this.error = snapshot.error ?? undefined
    this.createdAt = snapshot.created_at
    this.updatedAt = snapshot.updated_at
    this.bytes = snapshot.bytes ?? undefined
    this.memoryBytes = snapshot.memory_bytes ?? undefined
    this.sandboxId = snapshot.sandbox_id ?? undefined
    Object.freeze(this)
  }
}

// The source is a discriminated union: each known variant's fields are
// required. Any other non-empty string type is a kind a later API added, not a
// malformed response: it maps to `unknown` with the wire type (as Python).
const SOURCE_FIELDS: Readonly<Record<string, ResponseFields>> = {
  registry: { ref: 'string' },
  dockerfile: { context_object: 'string', dockerfile: 'string' },
  named: { name: 'string' },
  manifest: { name: 'string' },
}

function imageSource(source: NonNullable<api.ImageListItem['source']>): ImageSource {
  const type: unknown = Reflect.get(source, 'type')
  if (typeof type !== 'string' || type === '') throw badRequest('image response has a source.type that is not a non-empty string')
  if (!Object.prototype.hasOwnProperty.call(SOURCE_FIELDS, type)) return { type: 'unknown', rawType: type }
  checkResponse(source, SOURCE_FIELDS[type], 'image source')
  switch (source.type) {
    case 'registry':
      return { type: source.type, ref: source.ref }
    case 'dockerfile':
      return { type: source.type, dockerfile: source.dockerfile }
    case 'named':
    case 'manifest':
      return { type: source.type, name: source.name }
  }
}

/** @internal */
export function artifactRefOf(ref: api.ImageReference | api.SnapshotReference | null | undefined): { readonly id: string; readonly name?: string } | undefined {
  if (!ref) return undefined
  return Object.freeze(ref.name === undefined || ref.name === null ? { id: ref.id } : { id: ref.id, name: ref.name })
}

/**
 * The image options of a build, pull or capture, each checked before any
 * request, as in Python; `null` / `undefined` are omitted.
 * @internal
 */
export function imageOptionsBody(options: { readonly name?: unknown; readonly idleExpirySeconds?: unknown; readonly region?: unknown; readonly cpuArch?: unknown }): { name?: string; idle_expiry_seconds?: number; region_id?: string; cpu_arch?: string } {
  const body: { name?: string; idle_expiry_seconds?: number; region_id?: string; cpu_arch?: string } = {}
  for (const [key, wire] of [['name', 'name'], ['region', 'region_id'], ['cpuArch', 'cpu_arch']] as const) {
    const value: unknown = options[key]
    if (key === 'region') {
      // The region rule of create: blank or local selects the default region
      // (no region_id); any other value follows the header rule and is sent as given.
      const selected = selectRegion(value)
      if (selected !== undefined) body.region_id = selected
      continue
    }
    if (value === undefined || value === null) continue
    if (typeof value !== 'string') throw badRequest(`${key} must be a string`)
    // A blank name names nothing: refused, as promote() refuses it.
    if (key === 'name' && isBlank(value)) throw badRequest('name must be a non-empty string')
    // The contract's architectures only: SDK inputs are closed.
    if (key === 'cpuArch' && value !== 'amd64' && value !== 'arm64') throw badRequest('cpuArch must be "amd64" or "arm64"')
    body[wire] = value
  }
  const idle: unknown = options.idleExpirySeconds
  if (idle !== undefined && idle !== null) {
    // The contract: -1 (the maximum) or 1 and up, an int64 both SDKs carry
    // exactly; 0 and anything below -1 are refused, and the server holds the maximum.
    if (typeof idle !== 'number' || !Number.isSafeInteger(idle) || !(idle === -1 || idle >= 1)) {
      throw badRequest('idleExpirySeconds must be -1 (the maximum) or a positive integer number of seconds')
    }
    body.idle_expiry_seconds = idle
  }
  return body
}

/** @internal */
export function buildImageRequest(builder: ImageBuilder, options: {
  readonly name?: string
  readonly idleExpirySeconds?: number
  readonly region?: string
  readonly cpuArch?: CpuArch
} = {}): api.CreateImageRequest {
  return {
    ...imageOptionsBody(options),
    ...builder.toRequestSource(),
  }
}
