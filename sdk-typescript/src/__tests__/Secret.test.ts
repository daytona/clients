// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { createApiResponse } from './helpers'
import { SecretService } from '../Secret'

jest.mock('@daytona/api-client', () => ({}), { virtual: true })

describe('SecretService', () => {
  const secretApi = {
    listSecretsPaginated: jest.fn(),
    getSecret: jest.fn(),
    createSecret: jest.fn(),
    updateSecret: jest.fn(),
    deleteSecret: jest.fn(),
  }
  const service = new SecretService(secretApi as unknown as never)

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('lists secrets and forwards all query params', async () => {
    secretApi.listSecretsPaginated.mockResolvedValue(
      createApiResponse({ items: [{ id: 's1', name: 'secret1' }], total: 42, nextCursor: 'cursor-2' }),
    )

    await expect(
      service.list({ cursor: 'cursor-1', limit: 50, name: 'secret', sort: 'name', order: 'asc' }),
    ).resolves.toEqual({ items: [{ id: 's1', name: 'secret1' }], total: 42, nextCursor: 'cursor-2' })

    expect(secretApi.listSecretsPaginated).toHaveBeenCalledWith(undefined, 'cursor-1', 50, 'secret', 'name', 'asc')
  })

  it('lists secrets without a query', async () => {
    secretApi.listSecretsPaginated.mockResolvedValue(createApiResponse({ items: [], total: 0, nextCursor: null }))

    await expect(service.list()).resolves.toEqual({ items: [], total: 0, nextCursor: null })

    expect(secretApi.listSecretsPaginated).toHaveBeenCalledWith(
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
    )
  })

  it('gets a secret by id', async () => {
    secretApi.getSecret.mockResolvedValue(createApiResponse({ id: 's1', name: 'secret1' }))

    await expect(service.get('s1')).resolves.toEqual({ id: 's1', name: 'secret1' })
    expect(secretApi.getSecret).toHaveBeenCalledWith('s1')
  })

  it('creates a secret and forwards all params', async () => {
    secretApi.createSecret.mockResolvedValue(createApiResponse({ id: 's2', name: 'secret2' }))

    await expect(
      service.create({
        name: 'secret2',
        value: 'super-secret',
        description: 'a description',
        hosts: ['api.example.com', '*.example.com'],
      }),
    ).resolves.toEqual({ id: 's2', name: 'secret2' })

    expect(secretApi.createSecret).toHaveBeenCalledWith({
      name: 'secret2',
      value: 'super-secret',
      description: 'a description',
      hosts: ['api.example.com', '*.example.com'],
    })
  })

  it('updates a secret and forwards the partial params', async () => {
    secretApi.updateSecret.mockResolvedValue(createApiResponse({ id: 's3', name: 'secret3' }))

    await expect(service.update('s3', { value: 'new-value' })).resolves.toEqual({ id: 's3', name: 'secret3' })

    expect(secretApi.updateSecret).toHaveBeenCalledWith('s3', {
      value: 'new-value',
      description: undefined,
      hosts: undefined,
    })
  })

  it('deletes a secret by id', async () => {
    secretApi.deleteSecret.mockResolvedValue(createApiResponse(undefined))

    await service.delete('s4')
    expect(secretApi.deleteSecret).toHaveBeenCalledWith('s4')
  })

  it('propagates errors from the API', async () => {
    const error = new Error('boom')
    secretApi.getSecret.mockRejectedValue(error)

    await expect(service.get('missing')).rejects.toBe(error)
  })
})
