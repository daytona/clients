// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Configuration, Sandbox as SandboxDto } from '@daytona/api-client'
import { createApiResponse } from './helpers'
import { io } from 'socket.io-client'
import { EventDispatcher } from '../utils/EventDispatcher'
import { EventSubscriptionManager } from '../utils/EventSubscriptionManager'
import { DaytonaQueueTimeoutError, DaytonaSpotEvictedError, DaytonaTimeoutError } from '../errors/DaytonaError'
import { waitPollIntervalMs } from '../utils/WaitPolling'

jest.mock(
  '@daytona/api-client',
  () => ({
    SandboxState: {
      STARTING: 'starting',
      STARTED: 'started',
      STOPPING: 'stopping',
      STOPPED: 'stopped',
      SNAPSHOTTING: 'snapshotting',
      RESIZING: 'resizing',
      PAUSING: 'pausing',
      PAUSED: 'paused',
      ARCHIVED: 'archived',
      ERROR: 'error',
      BUILD_FAILED: 'build_failed',
      DESTROYED: 'destroyed',
    },
  }),
  { virtual: true },
)

jest.mock('socket.io-client', () => ({ io: jest.fn() }))

jest.mock(
  '@daytona/toolbox-api-client',
  () => ({
    FileSystemApi: jest.fn(() => ({})),
    GitApi: jest.fn(() => ({})),
    ProcessApi: jest.fn(() => ({})),
    LspApi: jest.fn(() => ({})),
    InfoApi: jest.fn(() => ({ getUserHomeDir: jest.fn(), getWorkDir: jest.fn() })),
    ServerApi: jest.fn(() => ({ updateEnv: jest.fn() })),
    SystemApi: jest.fn(() => ({ getSystemMetrics: jest.fn() })),
    ComputerUseApi: jest.fn(() => ({})),
    InterpreterApi: jest.fn(() => ({})),
  }),
  { virtual: true },
)

const baseDto: SandboxDto = {
  id: 'sb-1',
  name: 'sandbox-one',
  organizationId: 'org-1',
  user: 'daytona',
  env: {},
  labels: {},
  public: false,
  target: 'eu',
  cpu: 2,
  gpu: 0,
  memory: 4,
  disk: 10,
  state: 'stopped',
  kvm: false,
  networkBlockAll: false,
  toolboxProxyUrl: 'http://proxy',
}

type SubscriptionManagerMock = {
  subscribe: jest.Mock
  refresh: jest.Mock
  unsubscribe: jest.Mock
  isConnected: boolean
  isEventBacked: jest.Mock
  connectionEpoch: number
}

const makeSandbox = (
  overrides: Partial<SandboxDto> = {},
  subscribeReturns = 'sub-1',
  realSubscriptionManager?: EventSubscriptionManager,
): {
  sandbox: import('../Sandbox').Sandbox
  sandboxApi: Record<string, jest.Mock>
  subscriptionManager: SubscriptionManagerMock
} => {
  const { Sandbox } = require('../Sandbox') as typeof import('../Sandbox')
  const sandboxApi = {
    startSandbox: jest.fn(),
    recoverSandbox: jest.fn(),
    stopSandbox: jest.fn(),
    deleteSandbox: jest.fn(),
    getSandbox: jest.fn(),
    replaceLabels: jest.fn(),
    setAutostopInterval: jest.fn(),
    updatePublicStatus: jest.fn(),
    setAutoPauseInterval: jest.fn(),
    setAutoArchiveInterval: jest.fn(),
    setAutoDeleteInterval: jest.fn(),
    setTtl: jest.fn(),
    getPortPreviewUrl: jest.fn(),
    getSignedPortPreviewUrl: jest.fn(),
    expireSignedPortPreviewUrl: jest.fn(),
    archiveSandbox: jest.fn(),
    resizeSandbox: jest.fn(),
    updateLastActivity: jest.fn(),
    createSandboxSnapshot: jest.fn(),
    createSshAccess: jest.fn(),
    revokeSshAccess: jest.fn(),
    validateSshAccess: jest.fn(),
    updateSandboxSecrets: jest.fn(),
  }
  const subscriptionManager: SubscriptionManagerMock = {
    subscribe: jest.fn(() => subscribeReturns),
    refresh: jest.fn(() => true),
    unsubscribe: jest.fn(),
    isConnected: subscribeReturns !== '',
    // The mock serves every id it subscribes, so event-backed simply follows the connection.
    isEventBacked: jest.fn((): boolean => subscriptionManager.isConnected),
    connectionEpoch: 1,
  }

  const cfg: Configuration = {
    basePath: 'http://proxy',
    baseOptions: { headers: {} },
  } as unknown as Configuration

  const axiosInstance = {
    defaults: { baseURL: '' },
  }

  const getAnalyticsApiUrl = jest.fn().mockResolvedValue(undefined)

  const sandbox = new Sandbox(
    { ...baseDto, ...overrides },
    cfg,
    axiosInstance as unknown as never,
    sandboxApi as unknown as never,
    getAnalyticsApiUrl as unknown as never,
    realSubscriptionManager ?? (subscriptionManager as unknown as EventSubscriptionManager),
  )

  return { sandbox, sandboxApi, subscriptionManager }
}

describe('Sandbox', () => {
  it('maps dto fields in constructor', () => {
    const { sandbox } = makeSandbox({ labels: { team: 'sdk' }, autoStopInterval: 10 })
    expect(sandbox.id).toBe('sb-1')
    expect(sandbox.labels).toEqual({ team: 'sdk' })
    expect(sandbox.autoStopInterval).toBe(10)
  })

  it('hydrates kvm from dto', () => {
    const { sandbox } = makeSandbox({ kvm: true })
    expect(sandbox.kvm).toBe(true)
  })

  it('hydrates queue timeout fields from dto', () => {
    const { sandbox } = makeSandbox({ queueTimeout: 11, queueTimedOutAt: '2026-09-29T10:00:00.000Z' })
    expect(sandbox.queueTimeout).toBe(11)
    expect(sandbox.queueTimedOutAt).toBe('2026-09-29T10:00:00.000Z')
  })

  it('maps sandboxClass, warmPoolId, gpuType, desiredState, daemonVersion and otelEndpointOverride from dto', () => {
    const { sandbox } = makeSandbox({
      sandboxClass: 'linux-vm',
      warmPoolId: 'wp-1',
      gpuType: 'H100',
      desiredState: 'started',
      daemonVersion: '1.2.3',
      otelEndpointOverride: 'http://otel.example:4318',
    } as Partial<SandboxDto>)
    expect(sandbox.sandboxClass).toBe('linux-vm')
    expect(sandbox.warmPoolId).toBe('wp-1')
    expect(sandbox.gpuType).toBe('H100')
    expect(sandbox.desiredState).toBe('started')
    expect(sandbox.daemonVersion).toBe('1.2.3')
    expect(sandbox.otelEndpointOverride).toBe('http://otel.example:4318')
  })

  it('delegates start/stop/delete to sandbox api', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'stopped' })

    sandboxApi.startSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))
    await sandbox.start(5)

    sandboxApi.stopSandbox.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))
    await sandbox.stop(5)

    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroyed' }))
    await sandbox.delete(5)

    expect(sandboxApi.startSandbox).toHaveBeenCalledWith('sb-1', undefined, { timeout: 5000 })
    expect(sandboxApi.stopSandbox).toHaveBeenCalledWith('sb-1', undefined, false, { timeout: 5000 })
    expect(sandboxApi.deleteSandbox).toHaveBeenCalledWith('sb-1', undefined, { timeout: 5000 })
  })

  it('recovers sandboxes and waits for them to start', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'error' })

    sandboxApi.recoverSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'starting' }))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))

    await sandbox.recover(5)

    expect(sandboxApi.recoverSandbox).toHaveBeenCalledWith('sb-1', undefined, undefined, { timeout: 5000 })
    expect(sandbox.state).toBe('started')
  })

  test.each([['start'], ['recover'], ['stop'], ['waitUntilStarted'], ['waitUntilStopped'], ['resize']])(
    'validates negative timeout for %s',
    async (method) => {
      const { sandbox } = makeSandbox({ state: 'stopped' })

      if (method === 'resize') {
        await expect(
          (sandbox as never as { resize: (resources: { cpu: number }, timeout: number) => Promise<void> }).resize(
            { cpu: 2 },
            -1,
          ),
        ).rejects.toThrow('Timeout must be a non-negative number')
        return
      }

      const call = (sandbox as unknown as Record<string, (timeout: number) => Promise<void>>)[method].bind(sandbox)
      await expect(call(-1)).rejects.toThrow('Timeout must be a non-negative number')
    },
  )

  test.each([
    ['setAutostopInterval', -1, 'autoStopInterval must be a non-negative integer'],
    ['setAutoPauseInterval', -1, 'autoPauseInterval must be a non-negative integer'],
    ['setAutoArchiveInterval', -1, 'autoArchiveInterval must be a non-negative integer'],
    ['setTtl', -1, 'ttlMinutes must be a non-negative integer'],
  ])('validates %s', async (method, arg, message) => {
    const { sandbox } = makeSandbox()
    const call = (sandbox as unknown as Record<string, (n: number) => Promise<void>>)[method].bind(sandbox)
    await expect(call(arg)).rejects.toThrow(message)
  })

  it('updates labels and lifecycle intervals', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    sandboxApi.replaceLabels.mockResolvedValue(createApiResponse({ labels: { env: 'dev' } }))

    await expect(sandbox.setLabels({ env: 'dev' })).resolves.toEqual({ env: 'dev' })
    sandboxApi.setTtl.mockResolvedValue(createApiResponse({}))

    await sandbox.setAutostopInterval(20)
    await sandbox.setAutoArchiveInterval(30)
    await sandbox.setAutoDeleteInterval(0)
    await sandbox.setTtl(60)

    expect(sandbox.labels).toEqual({ env: 'dev' })
    expect(sandbox.autoStopInterval).toBe(20)
    expect(sandbox.autoArchiveInterval).toBe(30)
    expect(sandbox.autoDeleteInterval).toBe(0)
    expect(sandboxApi.setTtl).toHaveBeenCalledWith(sandbox.id, 60)
  })

  it('handles preview link, archive, resize, refresh calls', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'resizing' })

    sandboxApi.getPortPreviewUrl.mockResolvedValue(createApiResponse({ url: 'http://preview', token: 't' }))
    await expect(sandbox.getPreviewLink(3000)).resolves.toEqual({ url: 'http://preview', token: 't' })

    sandboxApi.archiveSandbox.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))
    await sandbox.archive()

    sandboxApi.resizeSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))
    await sandbox.resize({ cpu: 4 }, 5)

    await sandbox.refreshData()
    await sandbox.refreshActivity()

    expect(sandboxApi.archiveSandbox).toHaveBeenCalledWith('sb-1')
    expect(sandboxApi.resizeSandbox).toHaveBeenCalled()
    expect(sandboxApi.updateLastActivity).toHaveBeenCalledWith('sb-1')
  })

  it('gets and expires signed preview urls', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    sandboxApi.getSignedPortPreviewUrl.mockResolvedValue(
      createApiResponse({ url: 'http://signed', token: 'signed-token' }),
    )
    sandboxApi.expireSignedPortPreviewUrl.mockResolvedValue(createApiResponse(undefined))

    await expect(sandbox.getSignedPreviewUrl(8080, 90)).resolves.toEqual({
      url: 'http://signed',
      token: 'signed-token',
    })
    await sandbox.expireSignedPreviewUrl(8080, 'signed-token')

    expect(sandboxApi.getSignedPortPreviewUrl).toHaveBeenCalledWith('sb-1', 8080, undefined, 90)
    expect(sandboxApi.expireSignedPortPreviewUrl).toHaveBeenCalledWith('sb-1', 8080, 'signed-token')
  })

  it('creates, revokes and validates ssh access tokens', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    sandboxApi.createSshAccess.mockResolvedValue(createApiResponse({ token: 'ssh-token' }))
    sandboxApi.revokeSshAccess.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.validateSshAccess.mockResolvedValue(createApiResponse({ valid: true }))

    await expect(sandbox.createSshAccess(30)).resolves.toEqual({ token: 'ssh-token' })
    await sandbox.revokeSshAccess('ssh-token')
    await expect(sandbox.validateSshAccess('ssh-token')).resolves.toEqual({ valid: true })

    expect(sandboxApi.createSshAccess).toHaveBeenCalledWith('sb-1', undefined, 30)
    expect(sandboxApi.revokeSshAccess).toHaveBeenCalledWith('sb-1', undefined, 'ssh-token')
    expect(sandboxApi.validateSshAccess).toHaveBeenCalledWith('ssh-token')
  })

  it('aliases getUserRootDir to getUserHomeDir', async () => {
    const { sandbox } = makeSandbox()
    const infoApi = (sandbox as unknown as { infoApi: { getUserHomeDir: jest.Mock } }).infoApi
    const runtime = sandbox as unknown as { getUserRootDir: () => Promise<string | undefined> }
    infoApi.getUserHomeDir.mockResolvedValue(createApiResponse({ dir: '/home/daytona' }))

    await expect(runtime.getUserRootDir()).resolves.toBe('/home/daytona')
    expect(infoApi.getUserHomeDir).toHaveBeenCalledTimes(1)
  })

  it('updates sandbox secrets and refreshes instance state from the returned dto', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    sandboxApi.updateSandboxSecrets.mockResolvedValue(
      createApiResponse({ ...baseDto, env: { FOO: 'placeholder-foo', BAR: 'placeholder-bar' } }),
    )

    await sandbox.updateSecrets({ FOO: 'foo-secret', BAR: 'bar-secret' })

    expect(sandboxApi.updateSandboxSecrets).toHaveBeenCalledWith('sb-1', {
      secrets: [{ FOO: 'foo-secret' }, { BAR: 'bar-secret' }],
    })
    expect(sandbox.env).toEqual({ FOO: 'placeholder-foo', BAR: 'placeholder-bar' })
  })

  it('updates the daemon environment', async () => {
    const { sandbox } = makeSandbox()
    const serverApi = (sandbox as unknown as { serverApi: { updateEnv: jest.Mock } }).serverApi
    // The daemon responds with a status message, not the resulting environment.
    serverApi.updateEnv.mockResolvedValue(createApiResponse({ message: 'Environment updated successfully' }))

    await expect(sandbox.updateEnv({ NODE_ENV: 'production' }, { unset: ['DEBUG'] })).resolves.toBeUndefined()

    expect(serverApi.updateEnv).toHaveBeenCalledWith({ set: { NODE_ENV: 'production' }, unset: ['DEBUG'] })
  })

  it('creates sandbox snapshots and waits for completion', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'snapshotting' })
    sandboxApi.createSandboxSnapshot.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))

    await sandbox.createSnapshot('snap-1', 5)

    expect(sandboxApi.createSandboxSnapshot).toHaveBeenCalledWith('sb-1', { name: 'snap-1' }, undefined, {
      timeout: 5000,
    })
  })

  it('_experimental_createSnapshot delegates to createSnapshot', async () => {
    const { sandbox } = makeSandbox()
    const spy = jest.spyOn(sandbox, 'createSnapshot').mockResolvedValue(undefined)

    await sandbox._experimental_createSnapshot('snap-2', 10)

    expect(spy).toHaveBeenCalledWith('snap-2', 10)
  })

  it('_experimental_fork delegates to fork', async () => {
    const { sandbox } = makeSandbox()
    const fakeFork = { id: 'forked-sb' } as unknown as import('../Sandbox').Sandbox
    const spy = jest.spyOn(sandbox, 'fork').mockResolvedValue(fakeFork)

    const result = await sandbox._experimental_fork({ name: 'clone' }, 30)

    expect(spy).toHaveBeenCalledWith({ name: 'clone' }, 30)
    expect(result).toBe(fakeFork)
  })

  it('waitUntilStarted throws when sandbox enters an error state', async () => {
    const { sandbox } = makeSandbox({ state: 'starting', errorReason: 'boot failed' })

    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('error')
    })

    await expect(sandbox.waitUntilStarted(5)).rejects.toThrow(
      'Sandbox sb-1 failed to start with status: error, error reason: boot failed',
    )
  })

  it('waitUntilStopped treats deleted sandboxes as destroyed', async () => {
    const { sandbox } = makeSandbox({ state: 'stopping' })

    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('destroyed')
    })

    await expect(sandbox.waitUntilStopped(5)).resolves.toBeUndefined()
    expect(sandbox.state).toBe('destroyed')
  })

  it('waitUntilStarted rejects with DaytonaQueueTimeoutError when the refreshed sandbox was queue timed out', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'starting' }, '')
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({
        ...baseDto,
        state: 'destroyed',
        queueTimeout: 9,
        queueTimedOutAt: '2026-09-29T10:00:00.000Z',
      }),
    )

    const error = await sandbox.waitUntilStarted(5).catch((err: unknown) => err)

    expect(error).toBeInstanceOf(DaytonaQueueTimeoutError)
    expect(error).toBeInstanceOf(DaytonaTimeoutError)
    expect(error).toHaveProperty(
      'message',
      'Sandbox sb-1 was destroyed after waiting 9 minutes for a runner (queue timed out at 2026-09-29T10:00:00.000Z)',
    )
  })

  it('waitUntilStarted omits the duration from the queue timeout error when queueTimeout is unknown', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'starting' }, '')
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({
        ...baseDto,
        state: 'destroyed',
        queueTimeout: null,
        queueTimedOutAt: '2026-09-29T10:00:00.000Z',
      }),
    )

    const error = await sandbox.waitUntilStarted(5).catch((err: unknown) => err)

    expect(error).toBeInstanceOf(DaytonaQueueTimeoutError)
    expect(error).toHaveProperty(
      'message',
      'Sandbox sb-1 was destroyed after waiting for a runner (queue timed out at 2026-09-29T10:00:00.000Z)',
    )
  })

  it('waitUntilStarted rejects with DaytonaSpotEvictedError when the refreshed sandbox was spot evicted', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'starting' }, '')
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({
        ...baseDto,
        state: 'destroyed',
        spot: true,
        spotEvictedAt: '2026-09-29T10:05:00.000Z',
      }),
    )

    const error = await sandbox.waitUntilStarted(5).catch((err: unknown) => err)

    expect(error).toBeInstanceOf(DaytonaSpotEvictedError)
    expect(error).toHaveProperty('message', 'Sandbox sb-1 was evicted by spot preemption at 2026-09-29T10:05:00.000Z')
  })

  it('waitForResizeComplete throws when resize fails', async () => {
    const { sandbox } = makeSandbox({ state: 'resizing', errorReason: 'no capacity' })

    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('error')
    })

    await expect(sandbox.waitForResizeComplete(5)).rejects.toThrow(
      'Sandbox sb-1 resize failed with state: error, error reason: no capacity',
    )
  })

  it.each([true, false])('setPublic(%s) updates the public status', async (isPublic) => {
    const { sandbox, sandboxApi } = makeSandbox({ public: !isPublic })
    sandboxApi.updatePublicStatus.mockResolvedValue(createApiResponse(undefined))

    await sandbox.setPublic(isPublic)

    expect(sandboxApi.updatePublicStatus).toHaveBeenCalledWith(sandbox.id, isPublic)
    expect(sandbox.public).toBe(isPublic)
  })

  it('updates interval properties after successful api calls', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ autoStopInterval: 1, autoArchiveInterval: 2, autoDeleteInterval: 3 })
    sandboxApi.setAutostopInterval.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.setAutoPauseInterval.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.setAutoArchiveInterval.mockResolvedValue(createApiResponse(undefined))
    sandboxApi.setAutoDeleteInterval.mockResolvedValue(createApiResponse(undefined))

    await sandbox.setAutostopInterval(15)
    await sandbox.setAutoPauseInterval(45)
    await sandbox.setAutoArchiveInterval(25)
    await sandbox.setAutoDeleteInterval(-1)

    expect(sandbox.autoStopInterval).toBe(15)
    expect(sandbox.autoPauseInterval).toBe(45)
    expect(sandbox.autoArchiveInterval).toBe(25)
    expect(sandbox.autoDeleteInterval).toBe(-1)
  })

  it('waitUntilStarted and waitUntilStopped complete when state changes', async () => {
    const { sandbox } = makeSandbox({ state: 'starting' })

    const refreshSpy = jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
    })
    await sandbox.waitUntilStarted(5)
    expect(refreshSpy).toHaveBeenCalled()

    sandbox.state = 'stopping'
    refreshSpy.mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('stopped')
    })
    await sandbox.waitUntilStopped(5)
  })

  it('polling mode (no event subscription) polls with a ~100ms initial backoff', async () => {
    const { sandbox } = makeSandbox({ state: 'starting' }, '')
    const times: number[] = []
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      times.push(Date.now())
      if (times.length >= 3) {
        ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
      }
    })

    const start = Date.now()
    await sandbox.waitUntilStarted(5)

    expect(times.length).toBeGreaterThanOrEqual(3)
    expect(times[0] - start).toBeLessThan(400)
  })

  it('event-backed wait polls immediately, then not again before 4s and again by 6s', async () => {
    jest.useFakeTimers()
    try {
      const { sandbox } = makeSandbox({ state: 'starting' }, 'sub-1')
      const times: number[] = []
      jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
        times.push(Date.now())
        if (times.length >= 2) {
          ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
        }
      })

      const start = Date.now()
      const wait = sandbox.waitUntilStarted(60)
      await jest.advanceTimersByTimeAsync(0)
      expect(times).toEqual([start])

      await jest.advanceTimersByTimeAsync(3999)
      expect(times).toHaveLength(1)

      await jest.advanceTimersByTimeAsync(2001)
      await wait
      expect(times).toHaveLength(2)
    } finally {
      jest.useRealTimers()
    }
  })

  it('waitUntilStarted rejects promptly when the error state persists after refresh', async () => {
    const { sandbox } = makeSandbox({ state: 'error', errorReason: 'boom' }, '')
    const refreshSpy = jest.spyOn(sandbox, 'refreshData').mockResolvedValue(undefined)

    await expect(sandbox.waitUntilStarted(60)).rejects.toThrow(/error/)
    expect(refreshSpy).toHaveBeenCalled()
  })

  it('waitUntilStarted refreshes before failing on a stale cached error state', async () => {
    const { sandbox } = makeSandbox({ state: 'error' }, '')
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
    })

    await expect(sandbox.waitUntilStarted(5)).resolves.toBeUndefined()
  })

  it('does not treat stale cached error state as authoritative when the safe refresh fails', async () => {
    const { sandbox } = makeSandbox({ state: 'error', errorReason: 'stale' }, '')
    let calls = 0
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      calls++
      if (calls === 1) {
        throw new Error('Bad Gateway (502)')
      }
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('stopped')
    })

    await expect(sandbox.waitUntilStopped(5)).resolves.toBeUndefined()
    expect(calls).toBeGreaterThanOrEqual(2)
  })

  it('waitUntilStopped tolerates transient refresh errors while polling', async () => {
    const { sandbox } = makeSandbox({ state: 'stopping' }, '')
    let calls = 0
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      calls++
      if (calls === 1) {
        throw new Error('Bad Gateway (502)')
      }
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('stopped')
    })

    await expect(sandbox.waitUntilStopped(5)).resolves.toBeUndefined()
    expect(calls).toBeGreaterThanOrEqual(2)
  })

  it('a near-zero timeout still performs a final refresh before timing out', async () => {
    const { sandbox } = makeSandbox({ state: 'starting' }, '')
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
    })

    await expect(sandbox.waitUntilStarted(0.001)).resolves.toBeUndefined()
  })

  it('pause resolves when the sandbox leaves pausing without landing on paused', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' }, '')
    sandboxApi.pauseSandbox = jest.fn().mockResolvedValue(createApiResponse(undefined))
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('stopped')
    })

    await expect(sandbox.pause(5)).resolves.toBeUndefined()
  })

  it('delete is fire-and-forget by default', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' }, '')
    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroying' }))

    await expect(sandbox.delete(5)).resolves.toBeUndefined()
    expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
  })

  it('delete waits for destroyed when wait is true', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' }, '')
    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroying' }))
    const refreshSpy = jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('destroyed')
    })

    await expect(sandbox.delete(5, true)).resolves.toBeUndefined()
    expect(refreshSpy).toHaveBeenCalled()
    expect(sandbox.state).toBe('destroyed')
  })

  it('delete(wait=true) resolves when a queue-timed-out sandbox reaches destroyed', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' }, '')
    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroying' }))
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({
        ...baseDto,
        state: 'destroyed',
        queueTimeout: 4,
        queueTimedOutAt: '2026-09-29T10:10:00.000Z',
      }),
    )

    await expect(sandbox.delete(0, true)).resolves.toBeUndefined()
    expect(sandbox.state).toBe('destroyed')
  })

  it('processSandboxDto without toolboxProxyUrl preserves the existing hydrated value', () => {
    const { sandbox } = makeSandbox({ toolboxProxyUrl: 'http://hydrated-proxy' })
    expect(sandbox.toolboxProxyUrl).toBe('http://hydrated-proxy')

    const partial = { ...baseDto, toolboxProxyUrl: undefined } as unknown as import('@daytona/api-client').Sandbox
    ;(
      sandbox as unknown as { processSandboxDto: (dto: import('@daytona/api-client').Sandbox) => void }
    ).processSandboxDto(partial)

    expect(sandbox.toolboxProxyUrl).toBe('http://hydrated-proxy')
  })

  it('delete() with undefined response body resolves without throwing', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' }, '')
    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse(undefined))

    await expect(sandbox.delete(5)).resolves.toBeUndefined()
  })

  it('exposes user/work directories and creates LSP server', async () => {
    const { sandbox } = makeSandbox()
    const infoApi = (sandbox as unknown as { infoApi: { getUserHomeDir: jest.Mock; getWorkDir: jest.Mock } }).infoApi
    infoApi.getUserHomeDir.mockResolvedValue(createApiResponse({ dir: '/home/daytona' }))
    infoApi.getWorkDir.mockResolvedValue(createApiResponse({ dir: '/workspace' }))

    await expect(sandbox.getUserHomeDir()).resolves.toBe('/home/daytona')
    await expect(sandbox.getWorkDir()).resolves.toBe('/workspace')

    const lsp = await sandbox.createLspServer('typescript', '/workspace/project')
    expect(lsp).toBeTruthy()
  })
})

describe('Sandbox event subscriptions', () => {
  type StateEventHandler = (eventName: string, data: unknown) => void

  const subscribedHandler = (subscriptionManager: SubscriptionManagerMock, call = 0): StateEventHandler =>
    subscriptionManager.subscribe.mock.calls[call][1] as StateEventHandler

  const slimStateEvent = (state: string, id = 'sb-1') => ({
    sandbox: { id, state, desiredState: 'started' },
    oldState: 'starting',
    newState: state,
  })

  it('constructing a sandbox and calling ordinary methods does not subscribe to events', async () => {
    const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
    sandboxApi.replaceLabels.mockResolvedValue(createApiResponse({ labels: { a: 'b' } }))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))
    sandboxApi.createSshAccess.mockResolvedValue(createApiResponse({ token: 't' }))
    const infoApi = (sandbox as unknown as { infoApi: { getUserHomeDir: jest.Mock } }).infoApi
    infoApi.getUserHomeDir.mockResolvedValue(createApiResponse({ dir: '/home/daytona' }))

    await sandbox.setLabels({ a: 'b' })
    await sandbox.refreshData()
    await sandbox.createSshAccess()
    await sandbox.getUserHomeDir()
    await sandbox.waitUntilStarted(5)

    expect(subscriptionManager.subscribe).not.toHaveBeenCalled()
    expect(subscriptionManager.refresh).not.toHaveBeenCalled()
  })

  it('a wait subscribes to state events only and resolves from a slim state payload', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    jest.spyOn(sandbox, 'refreshData').mockResolvedValue(undefined)

    const wait = sandbox.waitUntilStarted(5)
    expect(subscriptionManager.subscribe).toHaveBeenCalledWith('sb-1', expect.any(Function), ['sandbox.state.updated'])
    expect(subscriptionManager.unsubscribe).not.toHaveBeenCalled()

    subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('started'))

    await expect(wait).resolves.toBeUndefined()
    expect(sandbox.state).toBe('started')
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
  })

  it('unsubscribes when the wait fails on an error state', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('error')
    })

    await expect(sandbox.waitUntilStarted(5)).rejects.toThrow(/error/)
    expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
  })

  it('unsubscribes when the wait times out', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    jest.spyOn(sandbox, 'refreshData').mockResolvedValue(undefined)

    await expect(sandbox.waitUntilStarted(0.05)).rejects.toBeInstanceOf(DaytonaTimeoutError)
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
  })

  it('unsubscribes when a refresh during the wait throws', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    jest.spyOn(sandbox, 'refreshData').mockRejectedValue(new Error('network down'))

    await expect(sandbox.waitUntilStarted(5)).rejects.toThrow('network down')
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
  })

  it('delete(wait=true) holds a subscription only for the wait', async () => {
    const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
    sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroying' }))
    subscriptionManager.isConnected = false
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('destroyed')
    })

    await sandbox.delete(5, true)

    expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
  })

  describe('operations that wait subscribe before their request', () => {
    const flush = () => new Promise<void>((resolve) => setImmediate(resolve))

    // The read must happen without any timer firing, i.e. as the wait's immediate first poll.
    const expectImmediateSingleRead = async (start: () => Promise<void>, getSandbox: jest.Mock): Promise<void> => {
      jest.useFakeTimers()
      try {
        const operation = start()
        await jest.advanceTimersByTimeAsync(0)
        expect(getSandbox).toHaveBeenCalledTimes(1)
        await operation
        expect(getSandbox).toHaveBeenCalledTimes(1)
      } finally {
        jest.useRealTimers()
      }
    }

    it('stop() with the connection up resolves from the stopped event without any GET', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))

      const stop = sandbox.stop(5)
      expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.subscribe.mock.invocationCallOrder[0]).toBeLessThan(
        sandboxApi.stopSandbox.mock.invocationCallOrder[0],
      )
      await flush()
      subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('stopped'))

      await expect(stop).resolves.toBeUndefined()
      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
      expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
    })

    it('stop() with the connection down when the request is sent reads once immediately', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      subscriptionManager.isConnected = false
      sandboxApi.stopSandbox.mockImplementation(async () => {
        subscriptionManager.isConnected = true
        return createApiResponse({ ...baseDto, state: 'stopping' })
      })
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
    })

    it('stop() reads immediately when a state event arrived during the request', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockImplementation(async () => {
        subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('stopped'))
        return createApiResponse({ ...baseDto, state: 'stopping' })
      })
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
      expect(sandbox.state).toBe('stopped')
    })

    it('stop() reads immediately when the connection dropped and reconnected during the request', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockImplementation(async () => {
        // Down and up again while the request ran: the stopped event sent meanwhile is lost.
        subscriptionManager.connectionEpoch++
        return createApiResponse({ ...baseDto, state: 'stopping' })
      })
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
      expect(sandbox.state).toBe('stopped')
    })

    it('stop() with an empty response whose fallback read fails reads again immediately', async () => {
      const { sandbox, sandboxApi } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse(undefined))
      sandboxApi.getSandbox
        .mockRejectedValueOnce(new Error('Bad Gateway (502)'))
        .mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      jest.useFakeTimers()
      try {
        const stop = sandbox.stop(60)
        await jest.advanceTimersByTimeAsync(0)
        // The swallowed fallback read, then the wait's immediate read.
        expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(2)
        await stop
      } finally {
        jest.useRealTimers()
      }
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(2)
      expect(sandbox.state).toBe('stopped')
    })

    it('delete(wait=true) with an empty response reads immediately', async () => {
      const { sandbox, sandboxApi } = makeSandbox({ state: 'started' })
      sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse(undefined))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroyed' }))

      await expectImmediateSingleRead(() => sandbox.delete(60, true), sandboxApi.getSandbox)
      expect(sandbox.state).toBe('destroyed')
    })

    it('stop() reads immediately when a state event is delivered from inside subscribe()', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      subscriptionManager.subscribe.mockImplementation((id: string, handler: StateEventHandler) => {
        handler('sandbox.state.updated', { sandbox: { id, state: 'started' }, newState: 'started' })
        return 'sub-1'
      })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
      expect(sandbox.state).toBe('stopped')
    })

    it('stop() reads immediately when the connection reconnects inside subscribe()', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      subscriptionManager.subscribe.mockImplementation(() => {
        subscriptionManager.connectionEpoch++
        return 'sub-1'
      })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
    })

    it('stop() releases the subscription and propagates the error when the request fails', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      const error = new Error('stop failed')
      sandboxApi.stopSandbox.mockRejectedValue(error)

      await expect(sandbox.stop(5)).rejects.toBe(error)

      expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
    })

    it('stop() resolves at once and releases the subscription when the response is the target state', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await sandbox.stop(5)

      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
      expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
    })

    test.each([
      [
        'delete(wait=true)',
        'deleteSandbox',
        'destroying',
        'destroyed',
        (s: import('../Sandbox').Sandbox) => s.delete(5, true),
      ],
      ['pause()', 'pauseSandbox', 'pausing', 'paused', (s: import('../Sandbox').Sandbox) => s.pause(5)],
      [
        'resize()',
        'resizeSandbox',
        'resizing',
        'started',
        (s: import('../Sandbox').Sandbox) => s.resize({ cpu: 4 }, 5),
      ],
    ])(
      '%s skips the immediate read when the subscription was live',
      async (_name, apiMethod, interim, target, call) => {
        const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
        sandboxApi[apiMethod] = jest.fn().mockResolvedValue(createApiResponse({ ...baseDto, state: interim }))

        const operation = call(sandbox)
        expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(1)
        await flush()
        subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent(target))

        await expect(operation).resolves.toBeUndefined()
        expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
        expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
      },
    )

    it('stop() whose response is already an error state reads immediately and fails at once', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'error' }))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'error' }))

      jest.useFakeTimers()
      try {
        const stop = sandbox.stop(60)
        const outcome = expect(stop).rejects.toThrow(/error/)
        await jest.advanceTimersByTimeAsync(0)
        await outcome
      } finally {
        jest.useRealTimers()
      }
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
    })

    it('fork() with a live connection resolves from the started event without any GET', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.forkSandbox = jest
        .fn()
        .mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-fork', state: 'starting' }))

      const fork = sandbox.fork(undefined, 30)
      await flush()
      expect(subscriptionManager.subscribe).toHaveBeenCalledWith('sb-fork', expect.any(Function), [
        'sandbox.state.updated',
      ])
      subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('started', 'sb-fork'))

      const forked = await fork
      expect(forked.id).toBe('sb-fork')
      expect(forked.state).toBe('started')
      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
      expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-1')
    })

    it('fork() whose response lacks the toolbox proxy URL reads once immediately', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.forkSandbox = jest
        .fn()
        .mockResolvedValue(
          createApiResponse({ ...baseDto, id: 'sb-fork', state: 'starting', toolboxProxyUrl: undefined }),
        )
      sandboxApi.getToolboxProxyUrl = jest.fn().mockResolvedValue(createApiResponse({ url: 'http://proxy' }))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-fork', state: 'starting' }))

      jest.useFakeTimers()
      try {
        const fork = sandbox.fork(undefined, 60)
        await jest.advanceTimersByTimeAsync(0)
        expect(sandboxApi.getToolboxProxyUrl).toHaveBeenCalledWith('sb-fork')
        expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
        expect(sandboxApi.getSandbox).toHaveBeenCalledWith('sb-fork')
        subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('started', 'sb-fork'))
        const forked = await fork
        expect(forked.state).toBe('started')
      } finally {
        jest.useRealTimers()
      }
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
    })

    it('fork() without a live connection reads once immediately', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      subscriptionManager.isConnected = false
      sandboxApi.forkSandbox = jest
        .fn()
        .mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-fork', state: 'starting' }))
      sandboxApi.getSandbox.mockImplementation(async () => {
        subscriptionManager.isConnected = true
        return createApiResponse({ ...baseDto, id: 'sb-fork', state: 'started' })
      })

      await expectImmediateSingleRead(async () => {
        await sandbox.fork(undefined, 60)
      }, sandboxApi.getSandbox)
      expect(sandboxApi.getSandbox).toHaveBeenCalledWith('sb-fork')
    })

    it('fork() whose response is already started takes no wait and no subscription', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.forkSandbox = jest
        .fn()
        .mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-fork', state: 'started' }))

      await sandbox.fork(undefined, 30)

      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
      expect(subscriptionManager.subscribe).not.toHaveBeenCalled()
    })

    test.each([
      ['an empty body', undefined],
      ['a body without a state', { id: 'sb-1' }],
    ])('resize() with %s refreshes once, then waits', async (_name, body) => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.resizeSandbox.mockResolvedValue(createApiResponse(body))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'resizing' }))

      const resize = sandbox.resize({ cpu: 4 }, 30)
      await flush()
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
      expect(sandbox.state).toBe('resizing')
      subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('started'))

      await expect(resize).resolves.toBeUndefined()
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
      expect(sandbox.state).toBe('started')
    })

    it('delete() without wait takes no subscription', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.deleteSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'destroying' }))

      await sandbox.delete(5)

      expect(subscriptionManager.subscribe).not.toHaveBeenCalled()
    })
  })

  it('concurrent waits hold independent subscriptions', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    subscriptionManager.subscribe.mockReturnValueOnce('sub-a').mockReturnValueOnce('sub-b')
    jest.spyOn(sandbox, 'refreshData').mockResolvedValue(undefined)

    const first = sandbox.waitUntilStarted(5)
    const second = sandbox.waitUntilStarted(5)
    expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(2)

    subscribedHandler(subscriptionManager, 1)('sandbox.state.updated', slimStateEvent('started'))

    await expect(Promise.all([first, second])).resolves.toEqual([undefined, undefined])
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-a')
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-b')
  })

  it('re-subscribes when the wait subscription expired', async () => {
    const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
    subscriptionManager.subscribe.mockReturnValueOnce('sub-a').mockReturnValueOnce('sub-b')
    subscriptionManager.refresh.mockReturnValueOnce(false)
    // Disconnected keeps the second poll at ~1s, well inside the test's time budget.
    subscriptionManager.isConnected = false
    let calls = 0
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      calls++
      if (calls >= 2) {
        ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
      }
    })

    await sandbox.waitUntilStarted(5)

    expect(subscriptionManager.subscribe).toHaveBeenCalledTimes(2)
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledTimes(1)
    expect(subscriptionManager.unsubscribe).toHaveBeenCalledWith('sub-b')
  })

  it('polls at ~1s while subscribed but disconnected, then at the event-backed cadence once connected', async () => {
    jest.useFakeTimers()
    try {
      const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
      subscriptionManager.isConnected = false
      const times: number[] = []
      jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
        times.push(Date.now())
        if (times.length === 3) {
          subscriptionManager.isConnected = true
        }
        if (times.length >= 4) {
          ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
        }
      })

      const wait = sandbox.waitUntilStarted(60)
      await jest.advanceTimersByTimeAsync(20_000)
      await wait

      expect(times).toHaveLength(4)
      for (const gap of [times[1] - times[0], times[2] - times[1]]) {
        expect(gap).toBeGreaterThanOrEqual(800)
        expect(gap).toBeLessThanOrEqual(1200)
      }
      expect(times[3] - times[2]).toBeGreaterThanOrEqual(4000)
      expect(times[3] - times[2]).toBeLessThanOrEqual(6000)
    } finally {
      jest.useRealTimers()
    }
  })

  it('advances the fast-poll backoff after 5s even while event-backed', async () => {
    jest.useFakeTimers()
    const random = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
      const times: number[] = []
      jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
        times.push(Date.now())
        // Event-backed polls every 5s for 20s, then the subscription is lost for good.
        if (times.length === 5) {
          subscriptionManager.refresh.mockReturnValue(false)
          subscriptionManager.subscribe.mockReturnValue('')
        }
        if (times.length >= 6) {
          ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
        }
      })

      const wait = sandbox.waitUntilStarted(60)
      await jest.advanceTimersByTimeAsync(30_000)
      await wait

      expect(times).toHaveLength(6)
      expect(times[4] - times[3]).toBe(5000)
      // The fast interval kept growing (x1.1 per poll after 5s) while event-backed,
      // so the first fast poll after losing the subscription is above the initial 100ms.
      expect(times[5] - times[4]).toBeGreaterThan(120)
      expect(times[5] - times[4]).toBeLessThan(1000)
    } finally {
      random.mockRestore()
      jest.useRealTimers()
    }
  })

  test.each([
    ['stop', 'stopSandbox', 'stopped', (s: import('../Sandbox').Sandbox) => s.stop(5)],
    ['pause', 'pauseSandbox', 'paused', (s: import('../Sandbox').Sandbox) => s.pause(5)],
    ['archive', 'archiveSandbox', 'archived', (s: import('../Sandbox').Sandbox) => s.archive()],
  ])('%s applies the returned sandbox without a GET', async (_name, apiMethod, state, call) => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' })
    sandboxApi[apiMethod] = jest.fn().mockResolvedValue(createApiResponse({ ...baseDto, state }))

    await call(sandbox)

    expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(sandbox.state).toBe(state)
  })

  test.each([
    ['stop', 'stopSandbox', 'stopped', (s: import('../Sandbox').Sandbox) => s.stop(5)],
    ['pause', 'pauseSandbox', 'paused', (s: import('../Sandbox').Sandbox) => s.pause(5)],
    ['archive', 'archiveSandbox', 'archived', (s: import('../Sandbox').Sandbox) => s.archive()],
  ])('%s refreshes once when the response is empty', async (_name, apiMethod, state, call) => {
    const { sandbox, sandboxApi } = makeSandbox({ state: 'started' })
    sandboxApi[apiMethod] = jest.fn().mockResolvedValue(createApiResponse(undefined))
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state }))

    await call(sandbox)

    expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
    expect(sandbox.state).toBe(state)
  })
})

describe('waitPollIntervalMs', () => {
  it('uses the fast cadence unchanged and without jitter when not subscribed', () => {
    const random = jest.fn(() => 0)
    expect(waitPollIntervalMs(false, false, 100, random)).toBe(100)
    expect(waitPollIntervalMs(false, true, 133.1, random)).toBe(133.1)
    expect(random).not.toHaveBeenCalled()
  })

  it('polls every 5s x jitter when subscribed and connected', () => {
    expect(waitPollIntervalMs(true, true, 100, () => 0)).toBe(4000)
    expect(waitPollIntervalMs(true, true, 100, () => 0.5)).toBe(5000)
    expect(waitPollIntervalMs(true, true, 100, () => 1)).toBe(6000)
  })

  it('polls every 1s x jitter when subscribed but disconnected', () => {
    expect(waitPollIntervalMs(true, false, 100, () => 0)).toBe(800)
    expect(waitPollIntervalMs(true, false, 100, () => 0.5)).toBe(1000)
    expect(waitPollIntervalMs(true, false, 100, () => 1)).toBe(1200)
  })
})

describe('Sandbox wait telemetry', () => {
  type Recorded = {
    spans: string[]
    spanAttributes: Record<string, unknown>[]
    histograms: string[]
    labels: unknown[]
  }

  // Loads a fresh Sandbox and instrumentation module (empty histogram cache) with a recording tracer and meter.
  const instrumentedSandbox = (
    overrides: Partial<SandboxDto>,
  ): ReturnType<typeof makeSandbox> & { recorded: Recorded } => {
    const recorded: Recorded = { spans: [], spanAttributes: [], histograms: [], labels: [] }
    let made: ReturnType<typeof makeSandbox> | undefined
    jest.isolateModules(() => {
      const otel = require('@opentelemetry/api') as typeof import('@opentelemetry/api')
      const span = { setStatus: jest.fn(), recordException: jest.fn(), end: jest.fn() }
      jest.spyOn(otel.trace, 'getTracer').mockReturnValue({
        startSpan: (name: string, options?: { attributes?: Record<string, unknown> }) => {
          recorded.spans.push(name)
          recorded.spanAttributes.push(options?.attributes ?? {})
          return span
        },
      } as unknown as ReturnType<typeof otel.trace.getTracer>)
      jest.spyOn(otel.metrics, 'getMeter').mockReturnValue({
        createHistogram: (name: string) => {
          recorded.histograms.push(name)
          return { record: (_value: number, labels: unknown) => recorded.labels.push(labels) }
        },
      } as unknown as ReturnType<typeof otel.metrics.getMeter>)
      made = makeSandbox(overrides, '')
    })
    if (!made) throw new Error('sandbox was not created')
    return { ...made, recorded }
  }

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('stop() records the Sandbox.waitUntilStopped span and histogram', async () => {
    const { sandbox, sandboxApi, recorded } = instrumentedSandbox({ state: 'started' })
    sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

    await sandbox.stop(5)

    expect(recorded.spans).toEqual(['Sandbox.stop', 'Sandbox.waitUntilStopped'])
    expect(recorded.spanAttributes[1]).toEqual({ component: 'Sandbox', method: 'waitUntilStopped' })
    expect(recorded.histograms).toEqual(['sandbox_stop_duration', 'sandbox_wait_until_stopped_duration'])
    expect(recorded.labels[0]).toEqual({ component: 'Sandbox', method: 'waitUntilStopped', status: 'success' })
  })

  it('fork() records the Sandbox.waitUntilStarted span', async () => {
    const { sandbox, sandboxApi, recorded } = instrumentedSandbox({ state: 'started' })
    sandboxApi.forkSandbox = jest
      .fn()
      .mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-fork', state: 'started' }))

    await sandbox.fork(undefined, 5)

    expect(recorded.spans).toEqual(['Sandbox.fork', 'Sandbox.waitUntilStarted'])
    expect(recorded.histograms).toContain('sandbox_wait_until_started_duration')
  })

  it('resize() records the Sandbox.waitForResizeComplete span', async () => {
    const { sandbox, sandboxApi, recorded } = instrumentedSandbox({ state: 'started' })
    sandboxApi.resizeSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'started' }))

    await sandbox.resize({ cpu: 4 }, 5)

    expect(recorded.spans).toEqual(['Sandbox.resize', 'Sandbox.waitForResizeComplete'])
    expect(recorded.histograms).toContain('sandbox_wait_for_resize_complete_duration')
  })

  it('the public waits record their span once', async () => {
    const { sandbox, recorded } = instrumentedSandbox({ state: 'started' })

    await sandbox.waitUntilStarted(5)
    await sandbox.waitForResizeComplete(5)
    sandbox.state = 'stopped' as SandboxDto['state']
    await sandbox.waitUntilStopped(5)

    expect(recorded.spans).toEqual([
      'Sandbox.waitUntilStarted',
      'Sandbox.waitForResizeComplete',
      'Sandbox.waitUntilStopped',
    ])
  })
})

// Socket.IO client stand-in (the same shape as in EventDispatcher.test.ts).
type Listener = (...args: unknown[]) => void

class FakeEmitter {
  private readonly handlers = new Map<string, Listener[]>()

  on(event: string, handler: Listener): this {
    const list = this.handlers.get(event) ?? []
    list.push(handler)
    this.handlers.set(event, list)
    return this
  }

  fire(event: string, ...args: unknown[]): void {
    for (const handler of this.handlers.get(event) ?? []) {
      handler(...args)
    }
  }

  removeAllListeners(): this {
    this.handlers.clear()
    return this
  }
}

class FakeSocket extends FakeEmitter {
  connected = false
  auth: unknown
  readonly io = new FakeEmitter()
  readonly emit = jest.fn()
  readonly connect = jest.fn(() => this)
  readonly disconnect = jest.fn(() => {
    this.connected = false
    return this
  })

  serverAccepts(): void {
    this.connected = true
    this.fire('connect')
  }

  drop(): void {
    this.connected = false
    this.fire('disconnect', 'transport close')
  }
}

describe('Sandbox waits over the per-connection subscription cap', () => {
  const ioMock = io as unknown as jest.Mock
  let socket: FakeSocket
  let dispatcher: EventDispatcher
  let manager: EventSubscriptionManager

  const subscribedIds = (event: 'subscribe' | 'unsubscribe'): string[] =>
    socket.emit.mock.calls
      .filter(([name]) => name === event)
      .flatMap(([, payload]) => (payload as { sandboxIds: string[] }).sandboxIds)

  const stateEvent = (id: string, state: string) => ({ sandbox: { id, state }, newState: state })

  // A sandbox whose stop request returns 'stopping' and whose reads keep saying 'stopping'.
  const stoppingSandbox = (id: string) => {
    const made = makeSandbox({ id, state: 'started' }, 'unused', manager)
    made.sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, id, state: 'stopping' }))
    made.sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, id, state: 'stopping' }))
    return made
  }

  beforeEach(async () => {
    jest.useFakeTimers()
    ioMock.mockReset()
    ioMock.mockImplementation(() => {
      socket = new FakeSocket()
      return socket
    })
    dispatcher = new EventDispatcher('https://api.example.test/api', 'token', undefined, undefined, undefined, 2)
    manager = new EventSubscriptionManager(dispatcher)
    const connecting = dispatcher.connect()
    socket.serverAccepts()
    await connecting
  })

  afterEach(() => {
    manager.shutdown()
    dispatcher.disconnect()
    jest.useRealTimers()
  })

  it('an overflow wait reads first and polls every ~1s; the served waits stay event-backed', async () => {
    const a = stoppingSandbox('sb-a')
    const b = stoppingSandbox('sb-b')
    const c = stoppingSandbox('sb-c')

    const stops = [a.sandbox.stop(60), b.sandbox.stop(60), c.sandbox.stop(60)]
    await jest.advanceTimersByTimeAsync(0)

    expect(subscribedIds('subscribe')).toEqual(['sb-a', 'sb-b'])
    expect(manager.isEventBacked('sb-a')).toBe(true)
    expect(manager.isEventBacked('sb-c')).toBe(false)
    expect(a.sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(b.sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(c.sandboxApi.getSandbox).toHaveBeenCalledTimes(1)

    // 1 s x jitter is at most 1.2 s; the event-backed waits poll no earlier than 4 s.
    await jest.advanceTimersByTimeAsync(2500)
    expect(c.sandboxApi.getSandbox.mock.calls.length).toBeGreaterThanOrEqual(3)
    expect(c.sandboxApi.getSandbox.mock.calls.length).toBeLessThanOrEqual(4)
    expect(a.sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(b.sandboxApi.getSandbox).not.toHaveBeenCalled()

    socket.fire('sandbox.state.updated', stateEvent('sb-a', 'stopped'))
    socket.fire('sandbox.state.updated', stateEvent('sb-b', 'stopped'))
    c.sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-c', state: 'stopped' }))
    await jest.advanceTimersByTimeAsync(1500)
    await Promise.all(stops)
    expect(subscribedIds('subscribe')).toEqual(['sb-a', 'sb-b'])
  })

  it('a freed slot goes to the next new wait, not to the overflow wait', async () => {
    const a = stoppingSandbox('sb-a')
    const b = stoppingSandbox('sb-b')
    const c = stoppingSandbox('sb-c')
    const stopA = a.sandbox.stop(60)
    const stopB = b.sandbox.stop(60)
    const stopC = c.sandbox.stop(60)
    await jest.advanceTimersByTimeAsync(0)

    socket.fire('sandbox.state.updated', stateEvent('sb-a', 'stopped'))
    await stopA
    expect(subscribedIds('unsubscribe')).toEqual(['sb-a'])
    expect(manager.isEventBacked('sb-c')).toBe(false)

    const d = stoppingSandbox('sb-d')
    const stopD = d.sandbox.stop(60)
    await jest.advanceTimersByTimeAsync(0)
    expect(subscribedIds('subscribe')).toEqual(['sb-a', 'sb-b', 'sb-d'])
    expect(d.sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(manager.isEventBacked('sb-c')).toBe(false)

    socket.fire('sandbox.state.updated', stateEvent('sb-b', 'stopped'))
    socket.fire('sandbox.state.updated', stateEvent('sb-d', 'stopped'))
    // The gateway sends nothing for an unserved id: sb-c resolves through its ~1 s poll.
    c.sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, id: 'sb-c', state: 'stopped' }))
    await jest.advanceTimersByTimeAsync(1500)
    await Promise.all([stopB, stopC, stopD])
    // sb-c was never served, so its last listener sends no unsubscribe.
    expect(subscribedIds('unsubscribe')).toEqual(['sb-a', 'sb-b', 'sb-d'])
  })
})
