/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona'
import { serializeNetworkPolicyInput } from '../src/v2/NetworkPolicy'
import { BadRequestError } from '../src/v2/index'
import type { NetworkPolicyInput } from '../src/v2/index'

import { jsonBody, sentRequest, startFakeApi } from './networkPolicyTestServer'
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

// Shared with the Python SDK's test_network_policy_upstream_proxy.py.
describe('network policy upstream proxy', () => {
  it.each([
    [{ upstreamProxy: { url: 'http://proxy.example.com:3128' } }, { upstream_proxy: { url: 'http://proxy.example.com:3128' } }],
    [
      { upstreamProxy: { url: 'https://proxy.example.com', credentialsSecret: 'PROXY_AUTH' } },
      { upstream_proxy: { url: 'https://proxy.example.com', credentials_secret: 'PROXY_AUTH' } },
    ],
    [{ upstreamProxy: { url: 'http://proxy.example.com', credentialsSecret: null } }, { upstream_proxy: { url: 'http://proxy.example.com', credentials_secret: null } }],
    [{ upstreamProxy: { url: 'http://proxy.example.com', credentialsSecret: undefined } }, { upstream_proxy: { url: 'http://proxy.example.com' } }],
    [{ upstreamProxy: null }, { upstream_proxy: null }],
    [{ upstreamProxy: undefined }, {}],
    [
      { blockAll: true, allowDomains: ['pypi.org'], upstreamProxy: { url: 'http://proxy.example.com' } },
      { block_all: true, allow_domains: ['pypi.org'], upstream_proxy: { url: 'http://proxy.example.com' } },
    ],
  ] as const)('serializes %j', (policy, body) => {
    expect(serializeNetworkPolicyInput(policy)).toEqual(body)
  })

  it.each([
    [{ upstreamProxy: 'http://proxy.example.com' }, 'networkPolicy.upstreamProxy must be an object'],
    [{ upstreamProxy: [] }, 'networkPolicy.upstreamProxy must be an object'],
    [{ upstreamProxy: {} }, 'networkPolicy.upstreamProxy.url must be a string'],
    [{ upstreamProxy: { url: 3128 } }, 'networkPolicy.upstreamProxy.url must be a string'],
    [{ upstreamProxy: { url: 'http://p.example', credentialsSecret: 1 } }, 'networkPolicy.upstreamProxy.credentialsSecret must be a string'],
    [{ upstreamProxy: { url: 'http://p.example', credentials_secret: 'S' } }, 'networkPolicy.upstreamProxy contains unknown fields: credentials_secret'],
  ])('refuses %j locally', (policy, message) => {
    expect(() => serializeNetworkPolicyInput(policy)).toThrow(expect.objectContaining({
      name: 'BadRequestError', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message,
    }))
  })

  it('sends upstream_proxy on create and on update', async () => {
    const policyResponse = {
      status: 202,
      headers: { ETag: '"np-2"' },
      body: {
        desired: { version: 3, block_all: false, allow_cidrs: [], allow_domains: null, upstream_proxy: { url: 'https://proxy.example.com:443', credentials_secret: 'PROXY_AUTH' } },
        desired_revision: 2,
        applied: null,
        status: 'pending',
      },
    }
    const api = await startFakeApi([
      { status: 200, body: { id: 'sbx-a' } },
      { status: 200, body: { id: 'sbx-a', status: 'running' } },
      policyResponse,
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const networkPolicy: NetworkPolicyInput = { upstreamProxy: { url: 'https://proxy.example.com', credentialsSecret: 'PROXY_AUTH' } }
    try {
      await client.create('python:3.12', { networkPolicy })
      const updated = await lazySandbox(client, 'sbx').updateNetworkPolicy(networkPolicy, { ifMatch: '"np-1"' })
      expect(updated.desired).toEqual({
        version: 3,
        blockAll: false,
        allowCidrs: [],
        allowDomains: undefined,
        upstreamProxy: { url: 'https://proxy.example.com:443', credentialsSecret: 'PROXY_AUTH' },
      })
    } finally {
      await api.close()
    }
    const wire = { upstream_proxy: { url: 'https://proxy.example.com', credentials_secret: 'PROXY_AUTH' } }
    expect(jsonBody(sentRequest(api, 3, 0))).toMatchObject({ network_policy: wire })
    const update = sentRequest(api, 3, 2)
    expect(update.path).toBe('/sandboxes/sbx/network-policy')
    expect(jsonBody(update)).toEqual(wire)
  })

  it('reads a proxy without credentials and a policy without a proxy', async () => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"np-5"' },
        body: {
          desired: { version: 3, block_all: false, allow_cidrs: [], upstream_proxy: { url: 'http://proxy.example.com:80', credentials_secret: null } },
          desired_revision: 5,
          applied: { version: 2, block_all: false, allow_cidrs: [], upstream_proxy: null },
          applied_revision: 4,
        },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const policy = await lazySandbox(client, 'sbx').getNetworkPolicy()
      expect(policy.desired?.upstreamProxy).toEqual({ url: 'http://proxy.example.com:80', credentialsSecret: undefined })
      expect(policy.desired?.upstreamProxy?.credentialsSecret).toBeUndefined()
      expect(policy.applied?.upstreamProxy).toBeUndefined()
    } finally {
      await api.close()
    }
  })

  it.each([
    [{ url: 1 }, 'desired.upstream_proxy.url must be a string'],
    [{ url: 'http://p.example:80', credentials_secret: 7 }, 'desired.upstream_proxy.credentials_secret must be a string'],
    ['http://p.example:80', 'desired.upstream_proxy must be an object'],
  ])('refuses the malformed response proxy %j', async (upstreamProxy, message) => {
    const api = await startFakeApi([
      {
        status: 200,
        headers: { ETag: '"np-6"' },
        body: { desired: { version: 3, block_all: false, allow_cidrs: [], upstream_proxy: upstreamProxy }, desired_revision: 6 },
      },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    try {
      const outcome = lazySandbox(client, 'sbx').getNetworkPolicy()
      await expect(outcome).rejects.toBeInstanceOf(BadRequestError)
      await expect(outcome).rejects.toMatchObject({ message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    } finally {
      await api.close()
    }
  })
})
