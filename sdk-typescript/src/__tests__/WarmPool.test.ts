import { createApiResponse } from './helpers'
import { WarmPoolService } from '../WarmPool'

jest.mock('@daytona/api-client', () => ({}), { virtual: true })

describe('WarmPoolService', () => {
  const warmPoolsApi = {
    listWarmPools: jest.fn(),
    createWarmPool: jest.fn(),
    updateWarmPool: jest.fn(),
    deleteWarmPool: jest.fn(),
  }
  const service = new WarmPoolService(warmPoolsApi as unknown as never)

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('lists warm pools', async () => {
    warmPoolsApi.listWarmPools.mockResolvedValue(createApiResponse([{ id: 'wp1', snapshot: 'snap', pool: 3 }]))

    await expect(service.list()).resolves.toEqual([{ id: 'wp1', snapshot: 'snap', pool: 3 }])
  })

  it('creates a warm pool with the given params', async () => {
    warmPoolsApi.createWarmPool.mockResolvedValue(createApiResponse({ id: 'wp2', snapshot: 'snap', pool: 5 }))

    await expect(service.create({ snapshot: 'snap', pool: 5 })).resolves.toEqual({
      id: 'wp2',
      snapshot: 'snap',
      pool: 5,
    })
    expect(warmPoolsApi.createWarmPool).toHaveBeenCalledWith({ snapshot: 'snap', pool: 5 })
  })

  it('updates the pool size', async () => {
    warmPoolsApi.updateWarmPool.mockResolvedValue(createApiResponse({ id: 'wp3', pool: 10 }))

    await expect(service.update('wp3', { pool: 10 })).resolves.toEqual({ id: 'wp3', pool: 10 })
    expect(warmPoolsApi.updateWarmPool).toHaveBeenCalledWith('wp3', { pool: 10 })
  })

  it('deletes a warm pool', async () => {
    await service.delete('wp4')
    expect(warmPoolsApi.deleteWarmPool).toHaveBeenCalledWith('wp4')
  })

  it('propagates create failures', async () => {
    const error = new Error('conflict')
    warmPoolsApi.createWarmPool.mockRejectedValue(error)

    await expect(service.create({ snapshot: 'snap', pool: 1 })).rejects.toBe(error)
  })
})
