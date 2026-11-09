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
import { badRequest } from './options.js'

const SNAPSHOT_LONGPOLL_MAX_MS = 60_000

export class SnapshotsService {
  constructor(private readonly http: AxiosInstance) {
    credentialSafeDiagnostics(this, () => ({ type: 'SnapshotsService' }))
  }

  public async list(options: { cursor?: string; limit?: number; name?: string; sandboxId?: string } = {}): Promise<ListResult<Snapshot>> {
    const { data } = await this.http.get<api.ListSnapshotsResponse>('/snapshots', {
      params: { cursor: options.cursor, limit: options.limit, name: options.name, sandbox_id: options.sandboxId },
    })
    return { items: data.snapshots.map((snapshot) => new Snapshot(snapshot)), nextCursor: data.next_cursor ?? undefined }
  }

  public async *iter(options: { cursor?: string; limit?: number; name?: string; sandboxId?: string } = {}): AsyncIterable<Snapshot> {
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

  public async get(snapshotId: string, options: { wait?: 'ready'; timeout?: number } = {}): Promise<Snapshot> {
    const params: Record<string, string | number> = {}
    let requestTimeout: number | undefined
    if (options.wait !== undefined) {
      params.wait = options.wait
      if (options.timeout !== undefined && (!Number.isFinite(options.timeout) || options.timeout < 0)) {
        throw badRequest('timeout must be a non-negative finite number')
      }
      if (options.timeout) {
        params.timeout_ms = Math.min(Math.ceil(options.timeout * 1000), SNAPSHOT_LONGPOLL_MAX_MS)
        requestTimeout = Number(params.timeout_ms) + 5_000
      } else {
        requestTimeout = 0
      }
    }
    const { data } = await this.http.get<api.Snapshot>(`/snapshots/${strictUriEncode(snapshotId)}`, {
      params,
      ...(requestTimeout !== undefined ? { timeout: requestTimeout } : {}),
    })
    return new Snapshot(data)
  }

  public async delete(snapshotId: string): Promise<void> {
    await this.http.delete(`/snapshots/${strictUriEncode(snapshotId)}`)
  }
}
