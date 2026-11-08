/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios from 'axios'
import { SecretsService } from '../src/v2/Secret.js'
import { INTERNAL } from '../src/v2/internal'

const wire = { name: 'TOKEN', allowed_domains: ['api.example.com'], description: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', future_field: true }
const expected = { name: 'TOKEN', allowedDomains: ['api.example.com'], description: undefined,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-02T00:00:00Z' }

describe('secret metadata', () => {
  it.each([
    ['get', (service: SecretsService) => service.get('TOKEN')],
    ['set', (service: SecretsService) => service.set('TOKEN', 'value', { allowedDomains: ['api.example.com'] })],
    ['list', async (service: SecretsService) => (await service.list()).items[0]],
  ] as const)('maps only SDK fields when %s returns a secret', async (_name, operation) => {
    // Given
    const http = axios.create({ adapter: async (config) => ({ config, status: 200, statusText: 'OK', headers: {},
      data: config.url === '/secrets' ? { secrets: [wire], next_cursor: null } : wire }) })
    // When
    const result = await operation(new SecretsService(INTERNAL, http))
    // Then: strict, so the description null -> undefined mapping is checked.
    expect(result).toStrictEqual(expected)
  })

  it('sends a readonly domain list and creation guard when setting a secret', async () => {
    // Given
    const domains = ['api.example.com'] as const
    const requests: { path?: string; body: unknown; ifNoneMatch: unknown }[] = []
    const http = axios.create({ adapter: async (config) => {
      requests.push({ path: config.url, body: JSON.parse(config.data), ifNoneMatch: config.headers.get('If-None-Match') })
      return { config, status: 200, statusText: 'OK', headers: {}, data: wire }
    } })
    // When
    await new SecretsService(INTERNAL, http).set('API/TOKEN', 'value', { allowedDomains: domains, description: 'API access', ifNotExists: true })
    // Then
    expect(requests).toEqual([{ path: '/secrets/API%2FTOKEN', ifNoneMatch: '*',
      body: { value: 'value', allowed_domains: ['api.example.com'], description: 'API access' } }])
  })
})
