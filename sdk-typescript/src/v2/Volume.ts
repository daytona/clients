/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as api from './gen/api-types.js'
import type { JsonValue, ListResult, VolumeState, VolumeType } from './types.js'
import { VolumeStatus as VOLUME_STATUSES, VolumeType as VOLUME_TYPES } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { NotFoundError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import { assertKnownOptions, assertOptionsObject, badRequest, optionKeys } from './options.js'

/** A volume that preserves files between sandboxes. */
export interface Volume {
  readonly id: string
  readonly name: string
  readonly type: VolumeType
  readonly rawType: string
  readonly sizeGib?: number
  readonly status: VolumeState
  readonly rawStatus: string
  readonly error?: string
  readonly createdAt: string
  readonly updatedAt: string
  readonly lastUsedAt?: string
  readonly options?: Readonly<Record<string, JsonValue>>
  readonly usedBytes?: number
  readonly usedBytesAt?: string
}

export interface GetVolumeOptions {
  readonly create?: boolean
  readonly idOnly?: boolean
}

export interface CreateVolumeOptions {
  readonly name: string
  readonly sizeGib?: number
}

export interface DeleteVolumeOptions {
  readonly idOnly?: boolean
}

export interface ListVolumesOptions {
  readonly cursor?: string
  readonly limit?: number
}

const GET_VOLUME_KEYS = optionKeys<GetVolumeOptions>()({ create: true, idOnly: true })
const CREATE_VOLUME_KEYS = optionKeys<CreateVolumeOptions>()({ name: true, sizeGib: true })
const DELETE_VOLUME_KEYS = optionKeys<DeleteVolumeOptions>()({ idOnly: true })
const LIST_VOLUMES_KEYS = optionKeys<ListVolumesOptions>()({ cursor: true, limit: true })

function refConfig(options?: DeleteVolumeOptions): { readonly params: { readonly id_only: true } } | undefined {
  return options?.idOnly ? { params: { id_only: true } } : undefined
}

function mapVolume(volume: api.Volume): Volume {
  return {
    id: volume.id,
    name: volume.name,
    type: knownOrUnknown(volume.type, VOLUME_TYPES),
    rawType: volume.type,
    status: knownOrUnknown(volume.status, VOLUME_STATUSES),
    rawStatus: volume.status,
    createdAt: volume.created_at,
    updatedAt: volume.updated_at,
    sizeGib: volume.size_gib ?? undefined,
    error: volume.error ?? undefined,
    lastUsedAt: volume.last_used_at ?? undefined,
    options: volume.options ?? undefined,
    usedBytes: volume.used_bytes ?? undefined,
    usedBytesAt: volume.used_bytes_at ?? undefined,
  }
}

/** Manage named volumes and attach them with `create({ volumes })`. */
export class VolumesService {
  /** @internal */
  constructor(token: Internal, api: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly api: AxiosInstance) {
    assertInternal(token, 'VolumesService')
    credentialSafeDiagnostics(this, () => ({ type: 'VolumesService' }))
  }

  private async listPage(options: ListVolumesOptions): Promise<ListResult<Volume>> {
    const params = {
      cursor: options.cursor,
      limit: options.limit,
    }
    const { data } = await this.api.get<api.ListVolumesResponse>('/volumes', { params })
    const page = listPage<api.Volume>(data, 'volumes', 'volume list')
    return { items: page.items.map(mapVolume), nextCursor: page.nextCursor }
  }

  public async list(options: ListVolumesOptions = {}): Promise<ListResult<Volume>> {
    assertKnownOptions('ListVolumesOptions', options, LIST_VOLUMES_KEYS)
    return this.listPage(options)
  }

  public async *iter(options: ListVolumesOptions = {}): AsyncIterable<Volume> {
    assertKnownOptions('ListVolumesOptions', options, LIST_VOLUMES_KEYS)
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.listPage({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }

  /** Get a volume by id or name; optionally create it when absent. */
  public async get(nameOrId: string, options: GetVolumeOptions = {}): Promise<Volume> {
    if (typeof options !== 'object' || options === null || Array.isArray(options)) {
      throw badRequest('volumes.get takes options; pass { create: true }')
    }
    assertKnownOptions('GetVolumeOptions', options, GET_VOLUME_KEYS)
    try {
      const { data } = await this.api.get<api.Volume>(
        `/volumes/${strictUriEncode(nameOrId)}`,
        refConfig(options),
      )
      return mapVolume(data)
    } catch (e) {
      if (options.create && !options.idOnly && e instanceof NotFoundError) {
        return this.create({ name: nameOrId })
      }
      throw e
    }
  }

  /** Creates a disk volume (ready immediately). */
  public async create(options: CreateVolumeOptions): Promise<Volume> {
    // The options are required: an omitted value is refused like any non-object.
    assertOptionsObject('CreateVolumeOptions', options)
    assertKnownOptions('CreateVolumeOptions', options, CREATE_VOLUME_KEYS)
    if (typeof options.name !== 'string' || options.name.trim() === '') throw badRequest('name must be a non-empty string')
    if (options.sizeGib !== undefined && (!Number.isInteger(options.sizeGib) || options.sizeGib <= 0)) {
      throw badRequest('sizeGib must be a positive integer')
    }
    const body: api.CreateVolumeRequest = { name: options.name, type: 'disk', size_gib: options.sizeGib }
    const { data } = await this.api.post<api.Volume>('/volumes', body)
    return mapVolume(data)
  }

  /** Requests deletion and returns once the API accepts it. */
  public async delete(nameOrId: string, options: DeleteVolumeOptions = {}): Promise<void> {
    assertKnownOptions('DeleteVolumeOptions', options, DELETE_VOLUME_KEYS)
    await this.api.delete(`/volumes/${strictUriEncode(nameOrId)}`, refConfig(options))
  }
}
