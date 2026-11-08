/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { pathSegment } from './pathSegment.js'
import { frozenPage } from './frozen.js'
import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { Snapshot } from './Image.js'
import type * as api from './gen/api-types.js'
import type { ListResult } from './types.js'
import { assertKnownOptions, optionKeys, snapshotOptions } from './options.js'
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
    options = snapshotOptions('ListSnapshotsOptions', options, LIST_SNAPSHOTS_OPTION_KEYS) ?? {}
    const { data } = await this.http.get<api.ListSnapshotsResponse>('/snapshots', {
      params: { cursor: options.cursor, limit: options.limit, name: options.name, sandbox_id: options.sandboxId },
    })
    const page = listPage<api.Snapshot>(data, 'snapshots', 'snapshot list')
    return frozenPage({ items: page.items.map((snapshot) => new Snapshot(INTERNAL, snapshot)), nextCursor: page.nextCursor })
  }

  public async *iter(options: ListSnapshotsOptions = {}): AsyncIterable<Snapshot> {
    options = snapshotOptions('ListSnapshotsOptions', options, LIST_SNAPSHOTS_OPTION_KEYS) ?? {}
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
    return getArtifact(this.http, `/snapshots/${pathSegment(snapshotId, 'snapshotId')}`, options, 
      // SAFETY: the Snapshot constructor checks the response against the contract.
      (data) => new Snapshot(INTERNAL, data as api.Snapshot))
  }

  public async delete(snapshotId: string): Promise<void> {
    await this.http.delete(`/snapshots/${pathSegment(snapshotId, 'snapshotId')}`)
  }
}
