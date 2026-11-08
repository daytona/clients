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
import { getArtifact } from './artifactOptions.js'
import type { GetArtifactOptions } from './artifactOptions.js'
import { INTERNAL, assertInternal } from './internal.js'
import type { Internal } from './internal.js'

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
  constructor(token: Internal, http: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly http: AxiosInstance) {
    assertInternal(token, 'SnapshotsService')
    credentialSafeDiagnostics(this, () => ({ type: 'SnapshotsService' }))
  }

  public async list(options: ListSnapshotsOptions = {}): Promise<ListResult<Snapshot>> {
    assertKnownOptions('ListSnapshotsOptions', options, LIST_SNAPSHOTS_OPTION_KEYS)
    const { data } = await this.http.get<api.ListSnapshotsResponse>('/snapshots', {
      params: { cursor: options.cursor, limit: options.limit, name: options.name, sandbox_id: options.sandboxId },
    })
    return { items: data.snapshots.map((snapshot) => new Snapshot(INTERNAL, snapshot)), nextCursor: data.next_cursor ?? undefined }
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
    const data = await getArtifact<api.Snapshot>(this.http, `/snapshots/${strictUriEncode(snapshotId)}`, options)
    return new Snapshot(INTERNAL, data)
  }

  public async delete(snapshotId: string): Promise<void> {
    await this.http.delete(`/snapshots/${strictUriEncode(snapshotId)}`)
  }
}
