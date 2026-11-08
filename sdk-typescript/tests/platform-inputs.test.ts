/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import { createSandbox } from './helpers/sandbox'
import { createHttpClient } from '../src/v2/http'

describe('platform input boundaries', () => {
  it.each(([
    { name: 'secret domains', values: [undefined, 'example.com', [3], null, {}] },
    { name: 'secret options', values: [undefined, null, true] },
    { name: 'volume size', values: [true, NaN, Infinity, -1, 0, 1.5, '4'] },
    { name: 'create timeout', values: [-2, NaN, Infinity, true, 1.5] },
    { name: 'create labels', values: ['team', ['team'], null, { team: undefined }, { team: 1 }, new Map([['team', 'a']]), Object.create({ team: 'a' }), new (class Labels { team = 'a' })()] },
    { name: 'search labels', values: ['team', ['team'], { team: undefined }, { team: 1 }, new Map([['team', 'a']]), Object.create({ team: 'a' })] },
    { name: 'setLabels', values: [new Map([['team', 'a']]), Object.create({ team: 'a' }), { team: 1 }] },
    { name: 'resources', values: [true, NaN, Infinity, 1n, Symbol('size'), '4'] },
    { name: 'volume create options', values: [undefined, null, true, {}, { name: '' }, { name: '  ' }, { name: 3 }] },
    { name: 'volume get options', values: [true, false, null, 3, 'create', []] },
  ] as const).flatMap(({ name, values }) => values.map((value: unknown) => ({ name, value }))))('refuses invalid $name=$value before HTTP', async ({ name, value }) => {
    // Given runtime inputs that cannot satisfy the public types.
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      const calls = {
        // SAFETY: Deliberately invalid JavaScript inputs must reach the runtime guards.
        'secret domains': () => client.v2.secrets.set('KEY', 'value', { allowedDomains: value as never }),
        'secret options': () => client.v2.secrets.set('KEY', 'value', value as never),
        'volume size': () => client.v2.volumes.create({ name: 'data', sizeGib: value as never }),
        'create timeout': () => client.v2.create({ createTimeoutSeconds: value as never }),
        'create labels': () => client.v2.create({ labels: value as never }),
        'search labels': () => client.v2.search({ labels: value as never }),
        setLabels: () => createSandbox('sbx', createHttpClient(api.url)).setLabels(value as never),
        resources: () => client.v2.create({ resources: { memory: value as never } }),
        'volume get options': () => client.v2.volumes.get('data', value as never),
        'volume create options': () => client.v2.volumes.create(value as never),
      }
      // When a caller bypasses compile-time checking.
      const pending: Promise<unknown> = calls[name]()
      // Then the boundary emits the typed SDK error without making a request.
      await expect(pending).rejects.toBeInstanceOf(BadRequestError)
      await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      expect(api.requests).toHaveLength(0)
    } finally {
      await client.close()
      await api.close()
    }
  })

  describe.each(['updateNetworkPolicy', 'resetNetworkPolicy', 'replaceSecrets'] as const)('%s If-Match options', (method) => {
    it.each([undefined, {}, { ifMatch: '' }, { ifMatch: 3 }, { ifMatch: 'tag', typo: true }])('refuses invalid options %j before HTTP', async (options) => {
      // Given missing, empty, non-string, and unknown concurrency options.
      const api = await startFakeApi([])
      const sandbox = createSandbox('sbx', createHttpClient(api.url))
      try {
        const calls = {
          // SAFETY: Invalid concurrency options exercise the JavaScript boundary.
          updateNetworkPolicy: () => sandbox.updateNetworkPolicy({ blockAll: true }, options as never),
          resetNetworkPolicy: () => sandbox.resetNetworkPolicy(options as never),
          replaceSecrets: () => sandbox.replaceSecrets([], options as never),
        }
        // When an invalid concurrency option is supplied.
        const pending: Promise<unknown> = calls[method]()
        // Then no unconditional update can be sent.
        await expect(pending).rejects.toBeInstanceOf(BadRequestError)
        expect(api.requests).toHaveLength(0)
      } finally {
        await api.close()
      }
    })
  })

  it.each([undefined, ImageBuilder.fromRegistry('base')])('names createMacos when an image source is invalid (%s)', async (image) => {
    // Given a missing source or a builder that cannot produce macOS images.
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      // When the macOS entrypoint parses its image source.
      // SAFETY: Neither input is accepted by the typed image argument.
      const pending = client.v2.createMacos(image as never)
      // Then the error identifies the actual entrypoint, never the Linux builder route.
      await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('createMacos') })
      await expect(pending).rejects.not.toThrow('createFromBuild')
      expect(api.requests).toHaveLength(0)
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('reports a missing network-policy ETag at the response boundary', async () => {
    // Given a policy response without its required concurrency tag.
    const api = await startFakeApi([{ status: 200, body: { desired_revision: 1 } }])
    const sandbox = createSandbox('sbx', createHttpClient(api.url))
    try {
      // When reading the policy.
      const pending = sandbox.getNetworkPolicy()
      // Then malformed metadata is a field-naming SDK BadRequestError.
      await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'network_policy response is missing the ETag header' })
    } finally {
      await api.close()
    }
  })
})
