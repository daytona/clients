/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { Snapshot } from './Image.js'
import type * as api from './gen/api-types.js'
import { strictUriEncode } from './http.js'
import type { ListResult } from './types.js'
import { assertKnownOptions, optionKeys } from './options.js'
import { artifactRequestOptions } from './artifactOptions.js'
import type { GetArtifactOptions } from './artifactOptions.js'

export type { GetArtifactOptions } from './artifactOptions.js'

export interface ListSnapshotsOptions {
  readonly cursor?: string
  readonly limit?: number
  readonly name?: string
  readonly sandboxId?: string
}

const LIST_SNAPSHOTS_OPTION_KEYS = optionKeys<ListSnapshotsOptions>()({ cursor: true, limit: true, name: true, sandboxId: true })

export class SnapshotsService {
  /** @internal */
  public static _create(http: AxiosInstance): SnapshotsService {
    return new SnapshotsService(http)
  }

  private constructor(private readonly http: AxiosInstance) {
    credentialSafeDiagnostics(this, () => ({ type: 'SnapshotsService' }))
  }

  public async list(options: ListSnapshotsOptions = {}): Promise<ListResult<Snapshot>> {
    assertKnownOptions('ListSnapshotsOptions', options, LIST_SNAPSHOTS_OPTION_KEYS)
    const { data } = await this.http.get<api.ListSnapshotsResponse>('/snapshots', {
      params: { cursor: options.cursor, limit: options.limit, name: options.name, sandbox_id: options.sandboxId },
    })
    return { items: data.snapshots.map((snapshot) => Snapshot._create(snapshot)), nextCursor: data.next_cursor ?? undefined }
  }

  public async *iter(options: ListSnapshotsOptions = {}): AsyncIterable<Snapshot> {
    assertKnownOptions('ListSnapshotsOptions', options, LIST_SNAPSHOTS_OPTION_KEYS)
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.list({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }

  public async get(snapshotId: string, options: GetArtifactOptions = {}): Promise<Snapshot> {
    const { data } = await this.http.get<api.Snapshot>(`/snapshots/${strictUriEncode(snapshotId)}`, artifactRequestOptions(options))
    return Snapshot._create(data)
  }

  public async delete(snapshotId: string): Promise<void> {
    await this.http.delete(`/snapshots/${strictUriEncode(snapshotId)}`)
  }
}
