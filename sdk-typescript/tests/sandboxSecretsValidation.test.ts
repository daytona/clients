/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { BadRequestError, PreconditionFailedError } from '../src/v2/index'
import { lazySandbox } from './lazySandbox'

import { type PlannedResponse, startFakeApi } from './networkPolicyTestServer'

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

describe('sandbox secret validation parity', () => {
  it.each([
    [412, { code: 'STALE_SECRETS_REVISION', message: 'revision mismatch', trace_id: 'trace-secret-stale' }, PreconditionFailedError],
  ])('preserves trace ids and never auto-retries on HTTP %i', async (status, body, expectedError) => {
    const api = await startFakeApi([{ status, body } as PlannedResponse])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      let thrown: unknown
      try {
        await client.replaceSandboxSecrets('sbx', [], { ifMatch: '"sv-9"' })
      } catch (error) {
        thrown = error
      }
      expect(thrown).toBeInstanceOf(expectedError as typeof Error)
      expect(thrown).toMatchObject({ traceId: (body as { trace_id: string }).trace_id })
    } finally {
      await api.close()
    }
    expect(api.requests).toHaveLength(1)
    expect(api.requests[0]?.headers['if-match']).toBe('"sv-9"')
  })

  it.each(['linux', 'macos'] as const)('reads secrets for a %s sandbox', async (sandboxClass) => {
    // Given
    const api = await startFakeApi([{ status: 200, headers: { ETag: '"sv-1"' }, body: { secrets: ['TOKEN'], desired_revision: 1 } }])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = sandboxClass
    try {
      // When
      const secrets = await sandbox.getSecrets()
      // Then
      expect(secrets.secrets).toEqual(['TOKEN'])
      expect(api.requests.map(({ method, path }) => [method, path])).toEqual([['GET', '/sandboxes/sbx/secrets']])
    } finally {
      await api.close()
    }
  })

  it('rejects plain-object secret maps before any request leaves the SDK', async () => {
    const api = await startFakeApi([])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const plainObject = JSON.parse('{"NAME":"value"}')
    try {
      await expect(client.replaceSandboxSecrets('sbx', plainObject, { ifMatch: '"sv-1"' })).rejects.toBeInstanceOf(BadRequestError)
      await expect(client.replaceSandboxSecrets('sbx', plainObject, { ifMatch: '"sv-1"' })).rejects.toThrow('sandbox secrets must be an array of strings')
    } finally {
      await api.close()
    }
    expect(api.requests).toHaveLength(0)
  })
})
