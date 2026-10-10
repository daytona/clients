// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Configuration } from '@daytona/api-client'
import { createApiResponse } from './helpers'
import { SnapshotService } from '../Snapshot'
import { Image } from '../Image'
import { DaytonaForbiddenError, DaytonaNotFoundError } from '../errors/DaytonaError'

const mockProcessStreamingResponse = jest.fn()
const mockDynamicImport = jest.fn()

jest.mock(
  '@daytona/api-client',
  () => ({
    SnapshotState: {
      ACTIVE: 'active',
      ERROR: 'error',
      BUILD_FAILED: 'build_failed',
      PENDING: 'pending',
    },
  }),
  { virtual: true },
)

jest.mock('../utils/Stream', () => ({
  processStreamingResponse: (...args: unknown[]) => mockProcessStreamingResponse(...args),
}))

jest.mock('../utils/Import', () => ({
  dynamicImport: (...args: unknown[]) => mockDynamicImport(...args),
}))

describe('SnapshotService', () => {
  const cfg: Configuration = {
    basePath: 'http://api',
    baseOptions: { headers: { Authorization: 'Bearer token' } },
  } as unknown as Configuration

  const snapshotsApi = {
    getAllSnapshots: jest.fn(),
    getSnapshot: jest.fn(),
    removeSnapshot: jest.fn(),
    createSnapshot: jest.fn(),
    getSnapshotBuildLogsUrl: jest.fn(),
    activateSnapshot: jest.fn(),
  }
  const objectStorageApi = {
    getPushAccess: jest.fn(),
  }

  const service = new SnapshotService(cfg, snapshotsApi as unknown as never, objectStorageApi as unknown as never, 'eu')
  const access = {
    storageUrl: 'https://s3.us-east-1.amazonaws.com',
    bucket: 'customer-contexts',
    region: 'us-east-1',
    organizationId: 'org-1',
    accessKey: 'key',
    secret: 'secret',
    sessionToken: 'session',
  }
  const contextImage = () => {
    const image = Image.base('node:24-bookworm-slim')
    image.contextList.push({ sourcePath: '/tmp/file.txt', archivePath: 'file.txt' })
    return image
  }

  beforeEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
    mockDynamicImport.mockReset()
  })

  it('lists/gets/deletes snapshots', async () => {
    snapshotsApi.getAllSnapshots.mockResolvedValue(
      createApiResponse({ items: [{ id: 's1', name: 'snap1' }], total: 1, page: 1, totalPages: 1 }),
    )
    snapshotsApi.getSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snap1' }))
    snapshotsApi.removeSnapshot.mockResolvedValue(createApiResponse(undefined))

    await expect(service.list(1, 10)).resolves.toEqual({
      items: [{ id: 's1', name: 'snap1' }],
      total: 1,
      page: 1,
      totalPages: 1,
    })
    expect(snapshotsApi.getAllSnapshots).toHaveBeenCalledWith(undefined, 1, 10, undefined, undefined)
    await expect(service.get('snap1')).resolves.toEqual({ id: 's1', name: 'snap1' })
    await service.delete({ id: 's1' } as never)
  })

  it('lists snapshots with a query object including sourceSandboxId', async () => {
    snapshotsApi.getAllSnapshots.mockResolvedValue(
      createApiResponse({ items: [{ id: 's1', name: 'snap1' }], total: 1, page: 1, totalPages: 1 }),
    )

    await expect(service.list({ page: 2, limit: 5, sourceSandboxId: 'sandbox-1' })).resolves.toEqual({
      items: [{ id: 's1', name: 'snap1' }],
      total: 1,
      page: 1,
      totalPages: 1,
    })
    expect(snapshotsApi.getAllSnapshots).toHaveBeenCalledWith(undefined, 2, 5, undefined, 'sandbox-1')
  })

  it('deletes snapshot by name with a single resolution call', async () => {
    snapshotsApi.getSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snap1' }))
    snapshotsApi.removeSnapshot.mockResolvedValue(createApiResponse(undefined))

    await service.delete('snap1')

    expect(snapshotsApi.getSnapshot).toHaveBeenCalledTimes(1)
    expect(snapshotsApi.getSnapshot).toHaveBeenCalledWith('snap1')
    expect(snapshotsApi.removeSnapshot).toHaveBeenCalledTimes(1)
    expect(snapshotsApi.removeSnapshot).toHaveBeenCalledWith('s1')
  })

  it('deletes snapshot by UUID id without resolution', async () => {
    const id = '9f0a2b52-6a5f-4bd6-9c1e-1c9a1cf7d3aa'
    snapshotsApi.removeSnapshot.mockResolvedValue(createApiResponse(undefined))

    await service.delete(id)

    expect(snapshotsApi.getSnapshot).not.toHaveBeenCalled()
    expect(snapshotsApi.removeSnapshot).toHaveBeenCalledTimes(1)
    expect(snapshotsApi.removeSnapshot).toHaveBeenCalledWith(id)
  })

  it('falls back to name resolution when UUID-formatted name is not an id', async () => {
    const uuidName = '9f0a2b52-6a5f-4bd6-9c1e-1c9a1cf7d3aa'
    snapshotsApi.removeSnapshot
      .mockRejectedValueOnce(new DaytonaNotFoundError('not found'))
      .mockResolvedValueOnce(createApiResponse(undefined))
    snapshotsApi.getSnapshot.mockResolvedValue(createApiResponse({ id: 'real-id', name: uuidName }))

    await service.delete(uuidName)

    expect(snapshotsApi.removeSnapshot).toHaveBeenNthCalledWith(1, uuidName)
    expect(snapshotsApi.getSnapshot).toHaveBeenCalledWith(uuidName)
    expect(snapshotsApi.removeSnapshot).toHaveBeenNthCalledWith(2, 'real-id')
  })

  it('propagates non-404 errors from delete by UUID without resolution', async () => {
    const id = '9f0a2b52-6a5f-4bd6-9c1e-1c9a1cf7d3aa'
    snapshotsApi.removeSnapshot.mockRejectedValue(new DaytonaForbiddenError('forbidden'))

    await expect(service.delete(id)).rejects.toThrow('forbidden')
    expect(snapshotsApi.getSnapshot).not.toHaveBeenCalled()
    expect(snapshotsApi.removeSnapshot).toHaveBeenCalledTimes(1)
  })

  it('creates snapshot from image name with resources and region', async () => {
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse({ id: 's2', name: 'snap2', state: 'active' }))

    const snapshot = await service.create({
      name: 'snap2',
      image: 'python:3.12',
      resources: { cpu: 4, memory: 8 },
      entrypoint: ['python', 'main.py'],
    })

    expect(snapshot).toEqual({ id: 's2', name: 'snap2', state: 'active' })
    expect(snapshotsApi.createSnapshot).toHaveBeenCalled()
  })

  it('passes timeout values in milliseconds to snapshot creation', async () => {
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse({ id: 's2', name: 'snap2', state: 'active' }))

    await service.create({ name: 'snap2', image: 'python:3.12' }, { timeout: 12 })

    expect(snapshotsApi.createSnapshot).toHaveBeenCalledWith(expect.any(Object), undefined, { timeout: 12000 })
  })

  it('creates snapshot from declarative image using processImageContext', async () => {
    const contextSpy = jest.spyOn(SnapshotService, 'processImageContext').mockResolvedValue(['hash1'])
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse({ id: 's3', name: 'snap3', state: 'active' }))

    const image = Image.base('python:3.12').runCommands('echo hi')
    const snapshot = await service.create({ name: 'snap3', image })

    expect(snapshot.id).toBe('s3')
    expect(contextSpy).toHaveBeenCalled()
  })

  it('throws when the api returns no created snapshot', async () => {
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse(undefined))

    await expect(service.create({ name: 'snap-missing', image: 'python:3.12' })).rejects.toThrow(
      "Failed to create snapshot. Didn't receive a snapshot from the server API.",
    )
  })

  it('throws when terminal snapshot states indicate failure', async () => {
    snapshotsApi.createSnapshot.mockResolvedValue(
      createApiResponse({ id: 's4', name: 'snap4', state: 'error', errorReason: 'build failed' }),
    )

    await expect(service.create({ name: 'snap4', image: 'python:3.12' })).rejects.toThrow(
      'Failed to create snapshot. Name: snap4 Reason: build failed',
    )
  })

  it('returns empty context hashes when an image has no context files', async () => {
    await expect(
      SnapshotService.processImageContext(objectStorageApi as never, Image.base('python:3.12')),
    ).resolves.toEqual([])
  })

  it('uploads image contexts through object storage push credentials', async () => {
    const upload = jest.fn().mockResolvedValue('ctx-hash')
    const ObjectStorage = jest.fn().mockImplementation(() => ({ upload }))
    objectStorageApi.getPushAccess.mockResolvedValue(
      createApiResponse({
        storageUrl: 'https://s3.us-east-1.amazonaws.com',
        accessKey: 'key',
        secret: 'secret',
        sessionToken: 'session',
        bucket: 'bucket',
        organizationId: 'org-1',
        region: 'us-east-2',
      }),
    )
    mockDynamicImport.mockResolvedValue({ ObjectStorage })

    const image = Image.base('python:3.12')
    ;(image as unknown as { _contextList: Array<{ sourcePath: string; archivePath: string }> })._contextList = [
      { sourcePath: '/tmp/context', archivePath: '.' },
    ]

    await expect(SnapshotService.processImageContext(objectStorageApi as never, image)).resolves.toEqual(['ctx-hash'])
    expect(objectStorageApi.getPushAccess).toHaveBeenCalledTimes(1)
    expect(ObjectStorage).toHaveBeenCalledWith(expect.objectContaining({ region: 'us-east-2' }))
    expect(upload).toHaveBeenCalledWith('/tmp/context', 'org-1', '.')
  })

  it('skips custom and hosted upload access for an image without context files', async () => {
    const getAccess = jest.fn()
    await expect(
      SnapshotService.processImageContext(objectStorageApi as never, Image.base('python:3.12'), getAccess),
    ).resolves.toEqual([])
    expect(getAccess).not.toHaveBeenCalled()
    expect(objectStorageApi.getPushAccess).not.toHaveBeenCalled()
  })

  it('uses custom upload access for the snapshot region without changing its metadata contract', async () => {
    const upload = jest.fn().mockResolvedValue('ctx-hash')
    const ObjectStorage = jest.fn(() => ({ upload }))
    mockDynamicImport.mockResolvedValue({ ObjectStorage })
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snapshot', state: 'active' }))
    const params = { name: 'snapshot', image: contextImage(), regionId: 'customer-region' }
    const getAccess = jest.fn(async () => {
      params.regionId = 'changed-during-upload'
      return access
    })
    const customer = new SnapshotService(cfg, snapshotsApi as never, objectStorageApi as never, 'eu', getAccess)

    await customer.create(params)

    expect(getAccess).toHaveBeenCalledWith('customer-region')
    expect(objectStorageApi.getPushAccess).not.toHaveBeenCalled()
    expect(ObjectStorage).toHaveBeenCalledWith({
      endpointUrl: access.storageUrl,
      bucketName: access.bucket,
      region: access.region,
      accessKeyId: access.accessKey,
      secretAccessKey: access.secret,
      sessionToken: access.sessionToken,
    })
    expect(upload).toHaveBeenCalledWith('/tmp/file.txt', access.organizationId, 'file.txt')
    expect(snapshotsApi.createSnapshot).toHaveBeenCalledWith(
      {
        name: 'snapshot',
        regionId: 'customer-region',
        sandboxClass: undefined,
        buildInfo: { dockerfileContent: params.image.dockerfile, contextHashes: ['ctx-hash'] },
      },
      undefined,
      { timeout: 0 },
    )
  })

  it.each(['access', 'upload'])('never falls back or creates metadata after custom %s fails', async (failure) => {
    const upload = jest.fn().mockRejectedValue(new Error('Upload denied'))
    mockDynamicImport.mockResolvedValue({ ObjectStorage: jest.fn(() => ({ upload })) })
    const getAccess = jest.fn().mockResolvedValue(access)
    if (failure === 'access') getAccess.mockRejectedValue(new Error('Access denied'))
    const customer = new SnapshotService(
      cfg,
      snapshotsApi as never,
      objectStorageApi as never,
      'customer-region',
      getAccess,
    )

    await expect(customer.create({ name: 'snapshot', image: contextImage() })).rejects.toThrow(/denied/)

    expect(objectStorageApi.getPushAccess).not.toHaveBeenCalled()
    expect(snapshotsApi.createSnapshot).not.toHaveBeenCalled()
  })

  it('requires an explicit region and bucket for custom upload access', async () => {
    const getAccess = jest.fn().mockResolvedValue({ ...access, bucket: '' })
    const ObjectStorage = jest.fn()
    mockDynamicImport.mockResolvedValue({ ObjectStorage })
    const image = contextImage()

    await expect(SnapshotService.processImageContext(objectStorageApi as never, image, getAccess)).rejects.toThrow(
      'An explicit target region is required',
    )
    expect(getAccess).not.toHaveBeenCalled()
    await expect(
      SnapshotService.processImageContext(objectStorageApi as never, image, getAccess, 'customer-region'),
    ).rejects.toThrow('must specify a bucket')
    expect(ObjectStorage).not.toHaveBeenCalled()
    expect(objectStorageApi.getPushAccess).not.toHaveBeenCalled()
  })

  it('streams build logs when onLogs is provided for build snapshots', async () => {
    const fetchSpy = jest.spyOn(global, 'fetch' as never).mockResolvedValue({ ok: true } as never)
    snapshotsApi.createSnapshot.mockResolvedValue(createApiResponse({ id: 's5', name: 'snap5', state: 'building' }))
    snapshotsApi.getSnapshotBuildLogsUrl.mockResolvedValue(createApiResponse({ url: 'https://logs.daytona/snap5' }))
    snapshotsApi.getSnapshot.mockResolvedValue(createApiResponse({ id: 's5', name: 'snap5', state: 'active' }))
    mockProcessStreamingResponse.mockImplementation(async (_fetchLogs, onChunk: (chunk: string) => void) => {
      onChunk('log line')
    })

    const onLogs = jest.fn()
    await service.create({ name: 'snap5', image: Image.base('python:3.12').runCommands('echo hi') }, { onLogs })

    expect(snapshotsApi.getSnapshotBuildLogsUrl).toHaveBeenCalledWith('s5')
    expect(mockProcessStreamingResponse).toHaveBeenCalled()
    expect(onLogs).toHaveBeenCalledWith(expect.stringContaining('Creating snapshot snap5'))
    expect(onLogs).toHaveBeenCalledWith('log line')

    fetchSpy.mockRestore()
  })

  it('activates snapshots', async () => {
    snapshotsApi.activateSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snap1', state: 'active' }))

    await expect(service.activate({ id: 's1' } as never)).resolves.toEqual({ id: 's1', name: 'snap1', state: 'active' })
  })

  it('activates snapshots by name', async () => {
    snapshotsApi.getSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snap1' }))
    snapshotsApi.activateSnapshot.mockResolvedValue(createApiResponse({ id: 's1', name: 'snap1', state: 'active' }))

    await expect(service.activate('snap1')).resolves.toEqual({ id: 's1', name: 'snap1', state: 'active' })
    expect(snapshotsApi.getSnapshot).toHaveBeenCalledWith('snap1')
    expect(snapshotsApi.activateSnapshot).toHaveBeenCalledWith('s1')
  })
})
