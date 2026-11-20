/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isBlank } from './pyText.js'
import { pageParams } from './pageParams.js'
import { pathSegment } from './pathSegment.js'
import { deepFrozen, frozenPage } from './frozen.js'
import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as api from './gen/api-types.js'
import type { JsonValue, ListResult, SearchOrder, SearchResult, SearchTimestamp, VolumeState, VolumeType } from './types.js'
import { VolumeStatus as VOLUME_STATUSES, VolumeType as VOLUME_TYPES } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { NotFoundError } from './errors/DaytonaError.js'
import { MAX_INT32, assertKnownOptions, assertOptionsObject, badRequest, optionKeys, snapshotOptions } from './options.js'
import { checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'
import { searchTimestamp } from './clientSearch.js'
import { commaList, omitNullSearchOptions, resourceSearchParams, resourceSearchResult } from './resourceSearch.js'
import type { CommonResourceSearchOptions } from './resourceSearch.js'
import { SearchProbe, refuseResourceSearchName, searchOrList } from './searchFirst.js'

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
  /** Disk volumes only; omitted uses the server default. */
  readonly sizeGib?: number
  /** Omitted lets the server choose the volume's type. */
  readonly type?: Exclude<VolumeType, 'unknown'>
}

export interface DeleteVolumeOptions {
  readonly idOnly?: boolean
}

export interface ListVolumesOptions {
  readonly cursor?: string
  readonly limit?: number
}

export interface SearchVolumesOptions extends CommonResourceSearchOptions {
  readonly types?: Exclude<VolumeType, 'unknown'> | readonly Exclude<VolumeType, 'unknown'>[] | null
  readonly statuses?: 'ready' | 'deleting' | 'error' | readonly ('ready' | 'deleting' | 'error')[] | null
  readonly imported?: boolean | null
  readonly lastUsedStartAt?: SearchTimestamp | null
  readonly lastUsedEndAt?: SearchTimestamp | null
  readonly sort?: 'created_at' | 'updated_at' | 'name' | 'used_bytes' | 'last_used_at' | 'size_gib' | null
  readonly order?: SearchOrder | null
}

const GET_VOLUME_KEYS = optionKeys<GetVolumeOptions>()({ create: true, idOnly: true })
const CREATE_VOLUME_KEYS = optionKeys<CreateVolumeOptions>()({ name: true, sizeGib: true, type: true })
const DELETE_VOLUME_KEYS = optionKeys<DeleteVolumeOptions>()({ idOnly: true })
const LIST_VOLUMES_KEYS = optionKeys<ListVolumesOptions>()({ cursor: true, limit: true })
const SEARCH_VOLUMES_KEYS = optionKeys<SearchVolumesOptions>()({ q: true, name: true, namePrefix: true, types: true, statuses: true, imported: true, createdStartAt: true, createdEndAt: true, updatedStartAt: true, updatedEndAt: true, lastUsedStartAt: true, lastUsedEndAt: true, sort: true, order: true, cursor: true, limit: true })

function refConfig(options?: DeleteVolumeOptions): { readonly params: { readonly id_only: true } } | undefined {
  return options?.idOnly ? { params: { id_only: true } } : undefined
}

const VOLUME: ResponseFields = {
  id: 'string', name: 'string', type: 'string', status: 'string', created_at: 'timestamp', updated_at: 'timestamp',
  size_gib: 'integer?', error: 'string?', last_used_at: 'timestamp?', options: 'object?', used_bytes: 'integer?', used_bytes_at: 'timestamp?',
}

function mapVolume(value: unknown): Volume {
  const volume = checkResponse<api.Volume>(value, VOLUME, 'volume')
  return deepFrozen({
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
    // The parsed response's own value (nothing else holds it), deep-frozen
    // with the model by an iterative walk: no recursive copy of deep JSON.
    options: volume.options ?? undefined,
    usedBytes: volume.used_bytes ?? undefined,
    usedBytesAt: volume.used_bytes_at ?? undefined,
  })
}

/** Manage named volumes and attach them with `create({ volumes })`. */
export class VolumesService {
  private readonly searchProbe = new SearchProbe()
  /** @internal */
  constructor(token: Internal, api: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly api: AxiosInstance) {
    assertInternal(token, 'VolumesService')
    credentialSafeDiagnostics(this, () => ({ type: 'VolumesService' }))
  }

  private async listPage(options: ListVolumesOptions): Promise<ListResult<Volume>> {
    const params = pageParams(options.cursor, options.limit)
    const data = await searchOrList(
      async () => (await this.api.get<api.ListVolumesResponse>('/volumes/search', { params })).data,
      async () => (await this.api.get<api.ListVolumesResponse>('/volumes', { params })).data,
      this.searchProbe,
      'volumes',
    )
    const page = listPage<api.Volume>(data, 'volumes', 'volume list')
    return frozenPage({ items: page.items.map(mapVolume), nextCursor: page.nextCursor })
  }

  public async search(options: SearchVolumesOptions = {}): Promise<SearchResult<Volume>> {
    options = snapshotOptions('SearchVolumesOptions', options, SEARCH_VOLUMES_KEYS) ?? {}
    const normalized = omitNullSearchOptions(options)
    const params = resourceSearchParams(normalized, ['created_at', 'updated_at', 'name', 'used_bytes', 'last_used_at', 'size_gib'])
    if (normalized.types !== undefined) params.type = commaList('types', normalized.types, VOLUME_TYPES)
    if (normalized.statuses !== undefined) params.status = commaList('statuses', normalized.statuses, ['ready', 'deleting', 'error'])
    if (normalized.imported !== undefined) {
      if (typeof normalized.imported !== 'boolean') throw badRequest('imported must be a boolean')
      params.imported = normalized.imported ? 'true' : 'false'
    }
    if (normalized.lastUsedStartAt !== undefined) params.last_used_start_at = searchTimestamp(normalized.lastUsedStartAt)
    if (normalized.lastUsedEndAt !== undefined) params.last_used_end_at = searchTimestamp(normalized.lastUsedEndAt)
    const { data } = await this.api.get<api.ListVolumesResponse>('/volumes/search', { params })
    return resourceSearchResult(data, 'volumes', mapVolume)
  }

  public async list(options: ListVolumesOptions = {}): Promise<ListResult<Volume>> {
    options = snapshotOptions('ListVolumesOptions', options, LIST_VOLUMES_KEYS) ?? {}
    return this.listPage(options)
  }

  public async *iter(options: ListVolumesOptions = {}): AsyncIterable<Volume> {
    options = snapshotOptions('ListVolumesOptions', options, LIST_VOLUMES_KEYS) ?? {}
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
    refuseResourceSearchName(nameOrId, 'volume')
    // The id first, then the options, as Python checks them.
    const path = `/volumes/${pathSegment(nameOrId, 'nameOrId')}`
    if (typeof options !== 'object' || options === null || Array.isArray(options)) {
      throw badRequest('volumes.get takes options; pass { create: true }')
    }
    assertKnownOptions('GetVolumeOptions', options, GET_VOLUME_KEYS)
    // A truthy string must never act as true (it would create a volume); null is
    // refused, as Python refuses None for its False defaults.
    for (const flag of ['create', 'idOnly'] as const) {
      const value: unknown = options[flag]
      if (value !== undefined && typeof value !== 'boolean') throw badRequest(`${flag} must be a boolean`)
    }
    try {
      const { data } = await this.api.get<api.Volume>(path, refConfig(options))
      return mapVolume(data)
    } catch (e) {
      if (options.create && !options.idOnly && e instanceof NotFoundError) {
        return this.create({ name: nameOrId })
      }
      throw e
    }
  }

  /** Creates a volume; without `type` the server chooses its type. */
  public async create(options: CreateVolumeOptions): Promise<Volume> {
    // The options are required: an omitted value is refused like any non-object.
    assertOptionsObject('CreateVolumeOptions', options)
    assertKnownOptions('CreateVolumeOptions', options, CREATE_VOLUME_KEYS)
    if (typeof options.name !== 'string' || isBlank(options.name)) throw badRequest('name must be a non-empty string')
    refuseResourceSearchName(options.name, 'volume')
    // The type before the size, as Python checks them.
    if (options.type !== undefined && !(VOLUME_TYPES as readonly unknown[]).includes(options.type)) {
      throw badRequest(`type must be one of ${VOLUME_TYPES.join(', ')}`)
    }
    // The contract's int32.
    if (options.sizeGib !== undefined && (!Number.isInteger(options.sizeGib) || options.sizeGib <= 0 || options.sizeGib > MAX_INT32)) {
      throw badRequest(`sizeGib must be a positive integer up to ${MAX_INT32}`)
    }
    const body: api.CreateVolumeRequest = { name: options.name, type: options.type, size_gib: options.sizeGib }
    const { data } = await this.api.post<api.Volume>('/volumes', body)
    const created = mapVolume(data)
    // A volume without a usable id cannot be mounted or deleted, as in Python.
    if (isBlank(created.id)) throw badRequest('volume response is missing its id')
    return created
  }

  /** Requests deletion and returns once the API accepts it. */
  public async delete(nameOrId: string, options: DeleteVolumeOptions = {}): Promise<void> {
    refuseResourceSearchName(nameOrId, 'volume')
    // The id first, then the options, as Python checks them.
    const path = `/volumes/${pathSegment(nameOrId, 'nameOrId')}`
    assertKnownOptions('DeleteVolumeOptions', options, DELETE_VOLUME_KEYS)
    if (options.idOnly !== undefined && typeof options.idOnly !== 'boolean') throw badRequest('idOnly must be a boolean')
    await this.api.delete(path, refConfig(options))
  }
}
