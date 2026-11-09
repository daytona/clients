/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { lazySandbox } from './lazySandbox'
import { BadRequestError } from '../src/v2/index'

import { jsonBody, sentRequest, startFakeApi } from './networkPolicyTestServer'

function withApiUrl<T>(apiUrl: string, callback: () => T): T {
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = apiUrl
  try {
    return callback()
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}

// A detail the create wait reads: it must keep the contract, or the create throws.
const runningDetail = (id: string) => ({ id, class: 'linux', name: id, status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: 'region-a', port_map: [] })

describe('network policy wire contract', () => {
  it('preserves missing, null, and empty create shapes', async () => {
    const api = await startFakeApi([
      { status: 200, body: { id: 'sbx-a' } },
      { status: 200, body: runningDetail('sbx-a') },
      { status: 200, body: { id: 'sbx-b' } },
      { status: 200, body: runningDetail('sbx-b') },
      { status: 200, body: { id: 'sbx-c' } },
      { status: 200, body: runningDetail('sbx-c') },
      { status: 200, body: { id: 'sbx-d' } },
      { status: 200, body: runningDetail('sbx-d') },
      { status: 200, body: { id: 'sbx-e' } },
      { status: 200, body: runningDetail('sbx-e') },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      await client.create('python:3.12')
      await client.create('python:3.12', { networkPolicy: null })
      await client.create('python:3.12', { networkPolicy: {} })
      await client.create('python:3.12', { networkPolicy: { allowDomains: null } })
      await client.create('python:3.12', { networkPolicy: { allowDomains: [] } })
    } finally {
      await api.close()
    }

    const postBodies = api.requests.filter((request) => request.method === 'POST').map(jsonBody) as Array<Record<string, unknown>>
    expect(postBodies).toHaveLength(5)
    expect(sentRequest(api, 10, 0).path).toBe('/sandboxes')
    expect(postBodies[0]).not.toHaveProperty('network_policy')
    expect(postBodies[1]?.network_policy).toBeNull()
    expect(postBodies[2]?.network_policy).toEqual({})
    expect(postBodies[3]?.network_policy).toEqual({ allow_domains: null })
    expect(postBodies[4]?.network_policy).toEqual({ allow_domains: [] })
  })

  it('never sends network_policy on any restore shape', async () => {
    const api = await startFakeApi([
      { status: 202, body: { id: 'sbx-a', status: 'pending', wait_cap_seconds: 3 } },
      { status: 202, body: { id: 'sbx-b', status: 'pending', wait_cap_seconds: 3 } },
      { status: 202, body: { id: 'sbx-c', status: 'pending', wait_cap_seconds: 3 } },
      { status: 202, body: { id: 'sbx-d', status: 'pending', wait_cap_seconds: 3 } },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      await client.createFromSnapshot('snap_123', { waitSeconds: 0 })
      await client.createFromSnapshot('snap_123', { createTimeoutSeconds: 30, waitSeconds: 0 })
      await client.createFromSnapshot('snap_123', { labels: { env: 'dev' }, waitSeconds: 0 })
      await client.createFromSnapshot('snap_123', { ports: [{ port: 80 }], waitSeconds: 0 })
    } finally {
      await api.close()
    }

    const requests = api.requests.filter((request) => request.method === 'POST')
    const postBodies = requests.map(jsonBody) as Array<Record<string, unknown>>
    expect(requests).toHaveLength(4)
    expect(requests.every((request) => request.path === '/sandboxes/from-snapshot')).toBe(true)
    for (const body of postBodies) {
      expect(body).not.toHaveProperty('network_policy')
    }
  })

  it('treats networkPolicy undefined like omission on linux creates', async () => {
    const api = await startFakeApi([
      { status: 200, body: { id: 'sbx-u' } },
      { status: 200, body: runningDetail('sbx-u') },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      await client.create('python:3.12', { networkPolicy: undefined })
    } finally {
      await api.close()
    }

    // The create, then its wait read.
    const request = sentRequest(api, 2, 0)
    expect(request.path).toBe('/sandboxes')
    expect(jsonBody(request)).not.toHaveProperty('network_policy')
  })

  it('returns etag, revisions, and read-side versioned policy state', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"np-7"' },
        body: {
          desired: { version: 2, block_all: true, allow_cidrs: ['1.2.3.4/32'], allow_domains: ['api.example.com'] },
          desired_revision: 7,
          applied: { version: 2, block_all: false, allow_cidrs: [], allow_domains: null },
          applied_revision: 6,
          applied_start_id: 'start-1',
          status: 'pending',
          error: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const policy = await lazySandbox(client, 'sbx').getNetworkPolicy()
      expect(sentRequest(api, 1).path).toBe('/sandboxes/sbx/network-policy')
      expect(policy).toMatchObject({ etag: '"np-7"', httpStatus: 200, desiredRevision: 7, appliedRevision: 6, appliedStartId: 'start-1', status: 'pending' })
      expect(policy.desired).toMatchObject({ version: 2, blockAll: true, allowCidrs: ['1.2.3.4/32'], allowDomains: ['api.example.com'] })
      expect(policy.applied).toMatchObject({ version: 2, blockAll: false, allowCidrs: [], allowDomains: null })
    } finally {
      await api.close()
    }
  })

  it('sends exact update and reset wires and preserves actual 202 metadata', async () => {
    const api = await startFakeApi([
      {
        status: 202,
        headers: { ETag: '"np-2"' },
        body: {
          desired: { version: 2, block_all: true, allow_cidrs: [], allow_domains: [] },
          desired_revision: 2,
          applied: null,
          applied_revision: null,
          applied_start_id: null,
          status: 'pending',
          error: null,
        },
      },
      {
        status: 202,
        headers: { ETag: '"np-3"' },
        body: {
          desired: null,
          desired_revision: 3,
          applied: null,
          applied_revision: null,
          applied_start_id: null,
          status: 'applied',
          error: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const updated = await lazySandbox(client, 'sbx').updateNetworkPolicy({ blockAll: true, allowCidrs: [], allowDomains: [] }, { ifMatch: '"np-1"' })
      const reset = await lazySandbox(client, 'sbx').resetNetworkPolicy({ ifMatch: '"np-2"' })
      expect(updated).toMatchObject({ etag: '"np-2"', httpStatus: 202, desiredRevision: 2, status: 'pending' })
      expect(reset).toMatchObject({ etag: '"np-3"', httpStatus: 202, desiredRevision: 3, status: 'applied' })
    } finally {
      await api.close()
    }

    const update = sentRequest(api, 2, 0)
    const reset = sentRequest(api, 2, 1)
    expect(update.path).toBe('/sandboxes/sbx/network-policy')
    expect(update.headers['if-match']).toBe('"np-1"')
    expect(jsonBody(update)).toEqual({ block_all: true, allow_cidrs: [], allow_domains: [] })
    expect(reset.headers['if-match']).toBe('"np-2"')
    expect(jsonBody(reset)).toBeNull()
  })

  it('keeps 200 success parsing only as a compatibility parser case', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"np-4"' },
        body: {
          desired: null,
          desired_revision: 4,
          applied: null,
          applied_revision: null,
          applied_start_id: null,
          status: 'applied',
          error: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      await expect(lazySandbox(client, 'sbx').getNetworkPolicy()).resolves.toMatchObject({ etag: '"np-4"', httpStatus: 200, desiredRevision: 4 })
    } finally {
      await api.close()
    }
  })

  it('classifies malformed server response arrays as contract errors (BadRequestError, source SDK)', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"np-7"' },
        body: {
          desired: { version: 2, block_all: true, allow_cidrs: [1], allow_domains: null },
          desired_revision: 7,
          applied: null,
          applied_revision: null,
          applied_start_id: null,
          status: 'pending',
          error: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      let thrown: unknown
      try {
      await lazySandbox(client, 'sbx').getNetworkPolicy()
      } catch (error) {
        thrown = error
      }
      // A response that breaks the contract is the SDK's BadRequestError.
      expect(thrown).toBeInstanceOf(BadRequestError)
      expect(thrown).toMatchObject({ message: 'desired.allow_cidrs must be an array of strings', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    } finally {
      await api.close()
    }
  })
})
