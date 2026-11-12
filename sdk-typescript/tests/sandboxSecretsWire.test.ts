/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { lazySandbox } from './lazySandbox'

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

describe('sandbox secret wire contract', () => {
  it('returns etag, revisions, and runtime secret state', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"sv-7"' },
        body: {
          secrets: ['OPENAI_API_KEY', 'GITHUB_TOKEN'],
          desired_revision: 7,
          applied_revision: 6,
          status: 'pending',
          error: null,
          capture_status: null,
          capture_unresolved_count: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const info = await lazySandbox(client, 'sbx').getSecrets()
      expect(sentRequest(api, 1).path).toBe('/sandboxes/sbx/secrets')
      expect(info).toMatchObject({ etag: '"sv-7"', desiredRevision: 7, appliedRevision: 6, status: 'pending' })
      expect(info.secrets).toEqual(['OPENAI_API_KEY', 'GITHUB_TOKEN'])
    } finally {
      await api.close()
    }
  })

  it('sends exact replace wires and preserves actual 202 metadata', async () => {
    const api = await startFakeApi([
      {
        status: 202,
        headers: { ETag: '"sv-2"' },
        body: {
          secrets: ['OPENAI_API_KEY'],
          desired_revision: 2,
          applied_revision: null,
          status: 'pending',
          error: null,
          capture_status: null,
          capture_unresolved_count: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const updated = await lazySandbox(client, 'sbx').replaceSecrets(['OPENAI_API_KEY'], { ifMatch: '"sv-1"' })
      expect(updated).toMatchObject({ etag: '"sv-2"', desiredRevision: 2, status: 'pending' })
    } finally {
      await api.close()
    }

    const request = sentRequest(api, 1)
    expect(request.path).toBe('/sandboxes/sbx/secrets')
    expect(request.headers['if-match']).toBe('"sv-1"')
    expect(jsonBody(request)).toEqual({ secrets: ['OPENAI_API_KEY'] })
  })

  it('percent-encodes lazy sandbox ids on the sandbox secret path', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"sv-1"' },
        body: {
          secrets: [],
          desired_revision: 1,
          applied_revision: null,
          status: null,
          error: null,
          capture_status: null,
          capture_unresolved_count: null,
        },
      },
      {
        status: 202,
        headers: { ETag: '"sv-2"' },
        body: {
          secrets: [],
          desired_revision: 2,
          applied_revision: null,
          status: 'pending',
          error: null,
          capture_status: null,
          capture_unresolved_count: null,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const sandbox = lazySandbox(client, 'seg/ment')
    try {
      await sandbox.getSecrets()
      await sandbox.replaceSecrets([], { ifMatch: '"sv-1"' })
    } finally {
      await api.close()
    }

    expect(api.requests.map((request) => request.path)).toEqual([
      '/sandboxes/seg%2Fment/secrets',
      '/sandboxes/seg%2Fment/secrets',
    ])
  })
})
