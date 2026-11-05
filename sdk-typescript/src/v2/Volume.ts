/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import type * as api from './gen/api-types.js'
import type { ListResult, VolumeState, VolumeType } from './types.js'
import { VolumeStatus as VOLUME_STATUSES, VolumeType as VOLUME_TYPES } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { NotFoundError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'

/** A volume that preserves files between sandboxes. */
export interface Volume {
  readonly id: string
  readonly name: string
  readonly type: VolumeType
  readonly rawType: string
  readonly sizeGib?: number | null
  readonly status: VolumeState
  readonly rawStatus: string
  readonly error?: string | null
  readonly createdAt: string
  readonly updatedAt: string
  readonly lastUsedAt?: string | null
  readonly options?: Readonly<Record<string, never>>
  readonly usedBytes?: number | null
  readonly usedBytesAt?: string | null
}

export interface VolumeRefOptions {
  readonly idOnly?: boolean
}

export interface ListVolumesOptions {
  readonly cursor?: string
  readonly limit?: number
}

function refConfig(options?: VolumeRefOptions): { readonly params: { readonly id_only: true } } | undefined {
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
    ...(volume.size_gib !== undefined ? { sizeGib: volume.size_gib } : {}),
    ...(volume.error !== undefined ? { error: volume.error } : {}),
    ...(volume.last_used_at !== undefined ? { lastUsedAt: volume.last_used_at } : {}),
    ...(volume.options !== undefined ? { options: volume.options } : {}),
    ...(volume.used_bytes !== undefined ? { usedBytes: volume.used_bytes } : {}),
    ...(volume.used_bytes_at !== undefined ? { usedBytesAt: volume.used_bytes_at } : {}),
  }
}

/** Manage named volumes and attach them with `create({ volumes })`. */
export class VolumeService {
  constructor(private readonly api: AxiosInstance) {
    credentialSafeDiagnostics(this, () => ({ type: 'VolumeService' }))
  }

  private async listPage(options: ListVolumesOptions): Promise<ListResult<Volume>> {
    const params = {
      cursor: options.cursor,
      limit: options.limit,
    }
    const { data } = await this.api.get<api.ListVolumesResponse>('/volumes', { params })
    return { items: (data.volumes ?? []).map(mapVolume), nextCursor: data.next_cursor ?? undefined }
  }

  public async list(options: ListVolumesOptions = {}): Promise<ListResult<Volume>> {
    return this.listPage(options)
  }

  public async *iter(options: ListVolumesOptions = {}): AsyncIterable<Volume> {
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
  public async get(nameOrId: string, create = false, options?: VolumeRefOptions): Promise<Volume> {
    try {
    const { data } = await this.api.get<api.Volume>(
        `/volumes/${strictUriEncode(nameOrId)}`,
        refConfig(options),
      )
      return mapVolume(data)
    } catch (e) {
      if (create && !options?.idOnly && e instanceof NotFoundError) {
        return this.create(nameOrId)
      }
      throw e
    }
  }

  /** Creates a disk volume (ready immediately). */
  public async create(name: string, sizeGib?: number): Promise<Volume> {
    const body: api.CreateVolumeRequest = sizeGib !== undefined ? { name, size_gib: sizeGib } : { name }
    const { data } = await this.api.post<api.Volume>('/volumes', body)
    return mapVolume(data)
  }

  /** Requests deletion and returns once the API accepts it. */
  public async delete(volume: Volume | string, options?: VolumeRefOptions): Promise<void> {
    const id = typeof volume === 'string' ? volume : volume.id
    await this.api.delete(`/volumes/${strictUriEncode(id)}`, refConfig(options))
  }
}
