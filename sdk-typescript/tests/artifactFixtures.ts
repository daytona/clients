/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios, { AxiosHeaders } from 'axios'
import type * as api from '../src/v2/gen/api-types.js'

export function imageWire(overrides: Partial<api.ImageListItem> = {}): api.ImageListItem {
  return {
    id: 'img-one', name: 'base', anonymous: false, class: 'linux', cursor: 'image-cursor',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
    region_id: 'region-a', region_ids: ['region-a', 'region-b'], retention: 'permanent',
    origin: 'registry', status: 'ready', format_version: 2, format_outdated: false,
    platform: false, default: false, deprecated: false,
    ...overrides,
  }
}

export function snapshotWire(overrides: Partial<api.Snapshot> = {}): api.Snapshot {
  return {
    id: 'snap-one', name: 'saved', class: 'linux', kind: 'full', status: 'ready',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
    region_id: 'region-a', region_ids: ['region-a', 'region-b'],
    ...overrides,
  }
}

interface ArtifactRequest {
  readonly path?: string
  readonly params: unknown
  readonly timeout?: number
  readonly body: unknown
}

export function artifactApi(payload: api.ImageListItem | api.Snapshot | api.ImagePage | api.ListSnapshotsResponse) {
  const requests: ArtifactRequest[] = []
  const http = axios.create({ baseURL: 'https://api.example', timeout: 4321, adapter: async (config) => {
    requests.push({ path: config.url, params: config.params, timeout: config.timeout, body: config.data })
    return { data: payload, status: 200, statusText: 'OK', headers: new AxiosHeaders(), config }
  } })
  return { http, requests }
}
