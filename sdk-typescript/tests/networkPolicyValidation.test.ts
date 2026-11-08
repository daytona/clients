/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { serializeNetworkPolicyInput } from '../src/v2/NetworkPolicy'
import {
  BadRequestError,
  PreconditionFailedError,
  PreconditionRequiredError,
  ServiceUnavailableError,
} from '../src/v2/index'

import { type PlannedResponse, startFakeApi } from './networkPolicyTestServer'
import { lazySandbox } from './lazySandbox'

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

describe('network policy validation parity', () => {
  it('rejects versioned read-side payloads and unknown fields with typed bad requests', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const versionedPolicy = JSON.parse('{"version":2}')
    const extraFieldPolicy = JSON.parse('{"extra":true}')
    await expect(client.create('python:3.12', { networkPolicy: versionedPolicy })).rejects.toBeInstanceOf(BadRequestError)
    await expect(client.create('python:3.12', { networkPolicy: versionedPolicy })).rejects.toThrow('networkPolicy contains unknown fields: version')
    await expect(client.create('python:3.12', { networkPolicy: extraFieldPolicy })).rejects.toBeInstanceOf(BadRequestError)
    await expect(client.create('python:3.12', { networkPolicy: extraFieldPolicy })).rejects.toThrow('networkPolicy contains unknown fields: extra')
  })

  it('domains default blockAll to true by omitting the field on create', async () => {
    expect(serializeNetworkPolicyInput({ allowDomains: ['api.example.com'] })).toEqual({ allow_domains: ['api.example.com'] })
  })

  it('blockAll null is treated as omitted', () => {
    expect(serializeNetworkPolicyInput({ allowDomains: ['api.example.com'], blockAll: null })).toEqual({
      allow_domains: ['api.example.com'],
    })
  })

  it('domains with blockAll false are rejected locally', async () => {
    let caught: unknown
    try {
      serializeNetworkPolicyInput({ allowDomains: ['api.example.com'], blockAll: false })
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(BadRequestError)
    expect((caught as BadRequestError).code).toBe('NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL')
    expect((caught as BadRequestError).statusCode).toBe(400)
  })

  it.each([
    [412, { code: 'STALE_NETWORK_POLICY_REVISION', message: 'revision mismatch', trace_id: 'trace-stale' }, PreconditionFailedError],
    [428, { code: 'IF_MATCH_REQUIRED', message: 'precondition required', trace_id: 'trace-precondition' }, PreconditionRequiredError],
    [503, { code: 'SERVICE_UNAVAILABLE', message: 'service unavailable', trace_id: 'trace-unavailable' }, ServiceUnavailableError],
  ])('preserves trace ids and never auto-retries on HTTP %i', async (status, body, expectedError) => {
    const api = await startFakeApi([{ status, body } as PlannedResponse])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      let thrown: unknown
      try {
        await client.updateNetworkPolicy('sbx', { allowDomains: null }, { ifMatch: '"np-7"' })
      } catch (error) {
        thrown = error
      }
      expect(thrown).toBeInstanceOf(expectedError as typeof Error)
      expect(thrown).toMatchObject({ traceId: (body as { trace_id: string }).trace_id })
    } finally {
      await api.close()
    }
    expect(api.requests).toHaveLength(1)
    expect(api.requests[0]?.headers['if-match']).toBe('"np-7"')
  })

  it.each(['linux', 'macos'] as const)('reads network policy for a %s sandbox', async (sandboxClass) => {
    // Given
    const api = await startFakeApi([{ status: 200, headers: { ETag: '"np-1"' }, body: { desired_revision: 1 } }])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = sandboxClass
    try {
      // When
      const policy = await sandbox.getNetworkPolicy()
      // Then
      expect(policy.desiredRevision).toBe(1)
      expect(api.requests.map(({ method, path }) => [method, path])).toEqual([['GET', '/sandboxes/sbx/network-policy']])
    } finally {
      await api.close()
    }
  })
})

describe('network policy input serialization', () => {
  it('treats an explicitly undefined optional field as omitted while keeping null and [] distinct', () => {
    const computed: string[] | undefined = undefined
    expect(serializeNetworkPolicyInput({ blockAll: true, allowCidrs: computed, allowDomains: undefined })).toEqual({ block_all: true })
    expect(serializeNetworkPolicyInput({ allowDomains: null })).toEqual({ allow_domains: null })
    expect(serializeNetworkPolicyInput({ allowDomains: [] })).toEqual({ allow_domains: [] })
    expect(serializeNetworkPolicyInput({ allowCidrs: [] })).toEqual({ allow_cidrs: [] })
    expect(() => serializeNetworkPolicyInput({ blockAll: 'yes' })).toThrow(BadRequestError)
  })
})
