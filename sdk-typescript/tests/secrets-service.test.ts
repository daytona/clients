/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios, { AxiosError } from 'axios'
import { createHttpClient } from '../src/v2/http.js'
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

  // Shared with the Python SDK's test_services.py: an omitted description keeps
  // the stored one (the API replaces the whole secret); '' clears it.
  it.each([
    ['omitted, stored', { description: 'kept' }, {}, ['GET', 'PUT'], 'kept'],
    ['omitted, null stored', { description: null }, {}, ['GET', 'PUT'], undefined],
    ['omitted, new secret', undefined, {}, ['GET', 'PUT'], undefined],
    ['null, stored', { description: 'kept' }, { description: null }, ['GET', 'PUT'], 'kept'],
    ['empty', { description: 'kept' }, { description: '' }, ['PUT'], ''],
    ['given', { description: 'kept' }, { description: 'new' }, ['PUT'], 'new'],
    ['omitted, ifNotExists', { description: 'kept' }, { ifNotExists: true }, ['PUT'], undefined],
  ] as const)('keeps or replaces the description: %s', async (_name, stored, options, methods, sent) => {
    // Given
    const requests: { method?: string; body: unknown }[] = []
    // The API client's own error mapping turns the 404 into NotFoundError.
    const http = createHttpClient('http://api.invalid', 'key')
    http.defaults.adapter = async (config) => {
      requests.push({ method: config.method?.toUpperCase(), body: config.data === undefined ? undefined : JSON.parse(config.data as string) })
      if (config.method === 'get' && stored === undefined) {
        const response = { config, status: 404, statusText: 'Not Found', headers: { 'content-type': 'application/json' }, data: { code: 'NOT_FOUND', message: 'not found' } }
        throw new AxiosError('Request failed with status code 404', AxiosError.ERR_BAD_REQUEST, config, undefined, response)
      }
      return { config, status: 200, statusText: 'OK', headers: { 'content-type': 'application/json' }, data: { ...wire, ...(stored ?? {}) } }
    }
    // When
    await new SecretsService(INTERNAL, http).set('TOKEN', 'value', { allowedDomains: ['*'], ...options } as never)
    // Then
    expect(requests.map((request) => request.method)).toEqual(methods)
    const put = requests[requests.length - 1]?.body as Record<string, unknown>
    if (sent === undefined) expect(put).not.toHaveProperty('description')
    else expect(put.description).toBe(sent)
  })

  it('refuses an empty value before any request', async () => {
    const adapter = jest.fn()
    const http = axios.create({ adapter })
    await expect(new SecretsService(INTERNAL, http).set('TOKEN', '', { allowedDomains: ['*'] })).rejects.toMatchObject({ message: 'value must not be empty', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect(adapter).not.toHaveBeenCalled()
  })
})
