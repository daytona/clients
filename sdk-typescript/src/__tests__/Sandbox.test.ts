// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Configuration, Sandbox as SandboxDto } from '@daytona/api-client'
import { ProcessApi } from '@daytona/toolbox-api-client'
import { createApiResponse } from './helpers'
import type { EventSubscriptionManager } from '../utils/EventSubscriptionManager'
import { DaytonaQueueTimeoutError, DaytonaSpotEvictedError, DaytonaTimeoutError } from '../errors/DaytonaError'

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
  nodes: [{ rank: 0, id: 'sb-1', state: 'stopped', incarnation: 1, cpu: 2, gpu: 0, memory: 4, disk: 10 }],
  topologyGeneration: 1,
  nodeCount: 1,
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

const makeSandbox = (
  overrides: Partial<SandboxDto> = {},
  subscribeReturns = 'sub-1',
): { sandbox: import('../Sandbox').Sandbox; sandboxApi: Record<string, jest.Mock> } => {
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
    addSandboxNodes: jest.fn(),
    removeSandboxNodes: jest.fn(),
    replaceSandboxNode: jest.fn(),
    getGangMembershipOperation: jest.fn(),
  }
  const subscriptionManager = {
    subscribe: jest.fn(() => subscribeReturns),
    refresh: jest.fn(() => true),
    unsubscribe: jest.fn(),
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
    subscriptionManager as unknown as EventSubscriptionManager,
  )

  return { sandbox, sandboxApi }
}

describe('node SSH access', () => {
  it.each(['', '.', '..'])('rejects an invalid node path id %j before constructing a handle', (id) => {
    const { SandboxNode } = require('../SandboxNode') as typeof import('../SandboxNode')
    const { DaytonaInvalidArgumentError } = require('../errors/DaytonaError') as typeof import('../errors/DaytonaError')
    expect(
      () =>
        new SandboxNode(
          { ...baseDto.nodes![0], id },
          'http://proxy',
          { basePath: 'http://proxy' } as Configuration,
          {} as never,
        ),
    ).toThrow(DaytonaInvalidArgumentError)
  })

  it.each([true, false])('refreshes existing node proxy URLs (full response=%s)', (full) => {
    const { sandbox } = makeSandbox({
      nodeCount: 2,
      nodes: [baseDto.nodes![0], { ...baseDto.nodes![0], rank: 1, id: 'worker-1' }],
    })
    const handles = [...sandbox.nodes]
    const configs = (ProcessApi as jest.Mock).mock.calls.slice(-2).map((args: unknown[]) => args[0] as Configuration)
    const update: Partial<SandboxDto> = { ...baseDto, nodeCount: 2, toolboxProxyUrl: 'https://new.example/toolbox' }
    delete update.nodes
    if (full) update.nodes = [baseDto.nodes![0], { ...baseDto.nodes![0], rank: 1, id: 'worker-1' }]
    ;(sandbox as unknown as { processSandboxDto(dto: Partial<SandboxDto>): void }).processSandboxDto(update)
    expect(sandbox.nodes[0]).toBe(handles[0])
    expect(sandbox.nodes[1]).toBe(handles[1])
    expect(configs.map((config) => config.basePath)).toEqual([
      'https://new.example/toolbox/sb-1',
      'https://new.example/toolbox/worker-1',
    ])
  })

  it.each([
    [0, undefined],
    [1, undefined],
    [0, 30],
    [1, 30],
  ] as const)('targets rank %s with expiry %s', async (rank, expiresInMinutes) => {
    const { sandbox, sandboxApi } = makeSandbox({
      nodeCount: 2,
      nodes: [
        { ...baseDto.nodes![0], rank: 0, id: 'sb-1' },
        { ...baseDto.nodes![0], rank: 1, id: 'worker-1' },
      ],
    })
    const access = { token: 'ssh-token', sshCommand: 'ssh ssh-token@gateway.example.com' }
    sandboxApi.createSshAccess.mockResolvedValue(createApiResponse(access))
    sandboxApi.revokeSshAccess.mockResolvedValue(createApiResponse({}))
    const node = sandbox.nodes[rank]

    expect(await node.createSshAccess(expiresInMinutes)).toBe(access)
    await expect(node.revokeSshAccess(access.token)).resolves.toBeUndefined()

    expect(sandboxApi.createSshAccess).toHaveBeenCalledTimes(1)
    expect(sandboxApi.createSshAccess).toHaveBeenCalledWith(node.id, undefined, expiresInMinutes)
    expect(sandboxApi.revokeSshAccess).toHaveBeenCalledTimes(1)
    expect(sandboxApi.revokeSshAccess).toHaveBeenCalledWith(node.id, undefined, access.token)
  })
})

describe('membership operation deadlines', () => {
  beforeEach(() => jest.useFakeTimers({ now: 0 }))
  afterEach(() => jest.useRealTimers())

  const prepare = () => {
    const ctx = makeSandbox({ state: 'started' })
    ctx.sandboxApi.getSandbox.mockResolvedValue(createApiResponse(baseDto))
    ctx.sandboxApi.addSandboxNodes.mockResolvedValue(createApiResponse({ id: 'op-1', type: 'ADD', status: 'RUNNING' }))
    return ctx
  }

  it('bounds polling and final refresh by the remaining budget', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getGangMembershipOperation.mockResolvedValue(
      createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' }),
    )
    const waiting = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 2.5)
    await jest.advanceTimersByTimeAsync(1000)
    await waiting
    expect(sandboxApi.getGangMembershipOperation.mock.calls[0][3]).toEqual({ timeout: 1500 })
    expect(sandboxApi.getSandbox.mock.calls[0][3]).toEqual({ timeout: 2500 })
    expect(sandboxApi.getSandbox.mock.calls[1][3]).toEqual({ timeout: 1500 })
  })

  it('rejects a terminal poll response that arrives after the deadline', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getGangMembershipOperation.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' })), 2000)
        }),
    )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 2).catch((error) => error)
    await jest.advanceTimersByTimeAsync(3000)
    expect(await result).toBeInstanceOf(DaytonaTimeoutError)
  })

  it('does not start a poll after the budget expires during sleep', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getGangMembershipOperation.mockResolvedValue(
      createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' }),
    )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 1).catch((error) => error)
    await jest.advanceTimersByTimeAsync(1000)
    expect(await result).toBeInstanceOf(DaytonaTimeoutError)
    expect(sandboxApi.getGangMembershipOperation).not.toHaveBeenCalled()
  })

  it('rejects a final refresh that arrives after the deadline', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.addSandboxNodes.mockResolvedValue(createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' }))
    sandboxApi.getSandbox
      .mockReset()
      .mockResolvedValueOnce(createApiResponse(baseDto))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            setTimeout(() => resolve(createApiResponse(baseDto)), 3000)
          }),
      )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 2).catch((error) => error)
    await jest.advanceTimersByTimeAsync(3000)
    expect(await result).toBeInstanceOf(DaytonaTimeoutError)
  })
})

describe('all-node execution deadlines', () => {
  beforeEach(() => jest.useFakeTimers())
  afterEach(() => jest.useRealTimers())

  const makeNodes = (count = 1) => {
    const { sandbox } = makeSandbox({
      nodeCount: count,
      state: 'started',
      nodes: Array.from({ length: count }, (_, rank) => ({
        rank,
        id: rank === 0 ? 'sb-1' : `node-${rank}`,
        state: 'started' as const,
        incarnation: 1,
        cpu: 16,
        gpu: 8,
        memory: 192,
        disk: 512,
      })),
    })
    const processes = sandbox.nodes.map((node) => {
      const methods = {
        executeCommand: jest.fn().mockResolvedValue({ exitCode: 0, result: 'done', artifacts: { stdout: 'done' } }),
        createSession: jest.fn().mockResolvedValue(undefined),
        executeSessionCommand: jest.fn().mockResolvedValue({ cmdId: `command-${node.rank}` }),
        getSessionCommand: jest.fn().mockResolvedValue({ exitCode: 0 }),
        getSessionCommandLogs: jest.fn(async (...args: unknown[]) =>
          args.length > 2 ? undefined : { output: 'done', stdout: 'done', stderr: '' },
        ),
        deleteSession: jest.fn().mockResolvedValue(undefined),
      }
      Object.assign(node.process, methods)
      return methods
    })
    return { sandbox, processes }
  }

  it.each([0, 15])('forwards the buffered execution timeout %s to each rank', async (timeout) => {
    const { sandbox, processes } = makeNodes(2)
    const options = { timeout, onStdout: undefined }

    const results = await sandbox.nodes.process.executeCommand('workload', options)

    expect(Object.keys(results)).toEqual(['0', '1'])
    processes.forEach((process) =>
      expect(process.executeCommand).toHaveBeenCalledWith('workload', undefined, undefined, timeout),
    )
  })

  it.each([-1, 1.5, NaN, Infinity])('rejects invalid timeout %s before dispatch', async (timeout) => {
    const { sandbox, processes } = makeNodes()
    const options = { timeout, onStdout: undefined }

    await expect(sandbox.nodes.process.executeCommand('workload', options)).rejects.toThrow(/timeout/i)
    expect(processes[0].executeCommand).not.toHaveBeenCalled()
    expect(processes[0].createSession).not.toHaveBeenCalled()
  })

  it('bounds the RDMA prerequisite check', async () => {
    const { sandbox, processes } = makeNodes(2)
    await sandbox.nodes.process.executeCommand('workload', { timeout: 1 })
    for (const process of processes)
      expect(process.executeCommand.mock.calls[0].slice(1)).toEqual([undefined, undefined, 5])
  })

  it('still executes when the advisory RDMA probe fails', async () => {
    const { sandbox, processes } = makeNodes(2)
    processes[0].executeCommand.mockRejectedValueOnce(new Error('probe timed out'))
    const results = await sandbox.nodes.process.executeCommand('workload')
    expect(results[0].exitCode).toBe(0)
    expect(processes[0].executeCommand).toHaveBeenCalledWith('workload')
    expect(sandbox.nodes[0].warnings.join(' ')).toMatch(/unable|could not|failed/i)
  })

  it('returns completed command results when session cleanup fails', async () => {
    const { sandbox, processes } = makeNodes()
    processes[0].deleteSession.mockRejectedValueOnce(new Error('cleanup unavailable'))
    const results = await sandbox.nodes.process.executeCommand('workload', { onStdout: jest.fn() })
    expect(results[0].result).toBe('done')
    expect(sandbox.nodes[0].warnings.join(' ')).toMatch(/cleanup|session/i)
  })

  it.each([undefined, 0, 10])('cleans up a completed stream and its timer (timeout=%s)', async (timeout) => {
    const { sandbox, processes } = makeNodes()
    const options = { timeout, onStdout: jest.fn() }

    const results = await sandbox.nodes.process.executeCommand('workload', options)

    expect(results[0].result).toBe('done')
    const sessionId = processes[0].createSession.mock.calls[0][0]
    expect(processes[0].deleteSession).toHaveBeenCalledTimes(1)
    if (timeout) {
      expect(processes[0].createSession).toHaveBeenCalledWith(sessionId, 10)
      expect(processes[0].deleteSession).toHaveBeenCalledWith(sessionId, 10)
    } else {
      expect(processes[0].createSession).toHaveBeenCalledWith(sessionId)
      expect(processes[0].deleteSession).toHaveBeenCalledWith(sessionId)
    }
    expect(jest.getTimerCount()).toBe(0)
  })

  it('times out a stream and cleans up only its owned session', async () => {
    const { sandbox, processes } = makeNodes(2)
    processes[0].getSessionCommandLogs.mockImplementationOnce(() => new Promise<never>(jest.fn()))
    const options = { timeout: 1, onStdout: jest.fn() }
    let outcome: unknown
    const completed = sandbox.nodes.process.executeCommand('workload', options).catch((error) => {
      outcome = error
    })

    await jest.advanceTimersByTimeAsync(1000)

    expect(outcome).toBeInstanceOf(DaytonaTimeoutError)
    expect((outcome as Error).message).toContain('rank 0')
    expect(processes[0].getSessionCommand).not.toHaveBeenCalled()
    expect(processes[1].getSessionCommand).toHaveBeenCalledTimes(1)
    processes.forEach((process) => {
      const sessionId = process.createSession.mock.calls[0][0]
      expect(process.deleteSession).toHaveBeenCalledTimes(1)
      expect(process.deleteSession).toHaveBeenCalledWith(sessionId, 10)
    })
    expect(jest.getTimerCount()).toBe(0)
    await completed
  })

  it('preserves the timeout error if session cleanup also fails', async () => {
    const { sandbox, processes } = makeNodes()
    const cleanupError = new Error('session cleanup failed')
    processes[0].getSessionCommandLogs.mockImplementationOnce(() => new Promise<never>(jest.fn()))
    processes[0].deleteSession.mockRejectedValueOnce(cleanupError)
    const options = { timeout: 1, onStderr: jest.fn() }
    let outcome: unknown
    const completed = sandbox.nodes.process.executeCommand('workload', options).catch((error) => {
      outcome = error
    })

    await jest.advanceTimersByTimeAsync(1000)

    expect(outcome).toBeInstanceOf(DaytonaTimeoutError)
    expect((outcome as Error).cause).toBe(cleanupError)
    await completed
  })

  it('preserves a session-start failure and clears its deadline if cleanup also fails', async () => {
    const { sandbox, processes } = makeNodes()
    const original = new Error('command start failed')
    const cleanupError = new Error('cleanup failed')
    processes[0].executeSessionCommand.mockRejectedValueOnce(original)
    processes[0].deleteSession.mockRejectedValueOnce(cleanupError)

    await expect(sandbox.nodes.process.executeCommand('workload', { timeout: 1, onStdout: jest.fn() })).rejects.toBe(
      original,
    )

    expect(original.cause).toBe(cleanupError)
    expect(jest.getTimerCount()).toBe(0)
  })
})

describe('Sandbox', () => {
  it('normalizes an older API response to one head node', () => {
    const { sandbox } = makeSandbox({ nodes: undefined, nodeCount: undefined })

    expect(sandbox.nodeCount).toBe(1)
    expect(sandbox.nodes.map(({ rank, id }) => ({ rank, id }))).toEqual([{ rank: 0, id: sandbox.id }])
  })

  it('builds all node handles in rank order with the head at index zero', () => {
    const { sandbox } = makeSandbox({
      nodeCount: 2,
      nodes: [
        {
          rank: 0,
          id: 'sb-1',
          state: 'started',
          incarnation: 1,
          cpu: 16,
          gpu: 8,
          memory: 256,
          disk: 180,
          fabricHealthy: true,
        },
        {
          rank: 1,
          id: 'sb-2',
          state: 'started',
          incarnation: 1,
          cpu: 32,
          gpu: 8,
          memory: 256,
          disk: 300,
          fabricHealthy: false,
        },
      ],
    })

    expect(sandbox.nodes.map(({ rank, id }) => ({ rank, id }))).toEqual([
      { rank: 0, id: 'sb-1' },
      { rank: 1, id: 'sb-2' },
    ])
    expect(sandbox.nodes.map(({ cpu, memory, disk }) => ({ cpu, memory, disk }))).toEqual([
      { cpu: 16, memory: 256, disk: 180 },
      { cpu: 32, memory: 256, disk: 300 },
    ])
    expect(sandbox.nodes[1].fabricHealthy).toBe(false)
    expect(sandbox.nodes[1].state).toBe('started')
    expect(sandbox.nodes[1].incarnation).toBe(1)
  })

  it('executes one command on every node in parallel and keys results by rank', async () => {
    const { sandbox } = makeSandbox({
      nodeCount: 2,
      nodes: [
        { rank: 0, id: 'sb-1', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
        { rank: 1, id: 'sb-2', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
      ],
    })
    for (const node of sandbox.nodes) {
      jest
        .spyOn(node.process, 'executeCommand')
        .mockResolvedValueOnce({ exitCode: 0, result: '', artifacts: { stdout: '' } })
        .mockResolvedValueOnce({ exitCode: node.rank, result: `node-${node.rank}`, artifacts: { stdout: '' } })
    }

    const result = await sandbox.nodes.process.executeCommand('hostname')

    expect(Object.keys(result)).toEqual(['0', '1'])
    expect(result[0].result).toBe('node-0')
    expect(result[1].exitCode).toBe(1)
    expect(
      sandbox.nodes.every((node) => (node.process.executeCommand as jest.Mock).mock.calls[1][0] === 'hostname'),
    ).toBe(true)
  })

  it('uploads to every node and reports all failed ranks without rollback', async () => {
    const { sandbox } = makeSandbox({
      nodeCount: 2,
      nodes: [
        { rank: 0, id: 'sb-1', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
        { rank: 1, id: 'sb-2', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
      ],
    })
    jest.spyOn(sandbox.nodes[0].fs, 'uploadFile').mockResolvedValue(undefined)
    jest.spyOn(sandbox.nodes[1].fs, 'uploadFile').mockRejectedValue(new Error('disk full'))

    await expect(sandbox.nodes.fs.uploadFile(Buffer.from('data'), '/workspace/data.bin')).rejects.toThrow(
      'node ranks [1]',
    )
    expect(sandbox.nodes[0].fs.uploadFile).toHaveBeenCalledTimes(1)
    expect(sandbox.nodes[1].fs.uploadFile).toHaveBeenCalledTimes(1)
  })

  it('keeps the native nodes array live across an add operation', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    const nodes = sandbox.nodes
    const updated = {
      ...baseDto,
      topologyGeneration: 2,
      nodeCount: 2,
      cpu: 34,
      memory: 260,
      disk: 310,
      nodes: [
        { rank: 0, id: 'sb-1', state: 'started', incarnation: 1, cpu: 2, gpu: 0, memory: 4, disk: 10 },
        { rank: 1, id: 'sb-2', state: 'started', incarnation: 1, cpu: 32, gpu: 0, memory: 256, disk: 300 },
      ],
    } satisfies SandboxDto
    const operation = {
      id: 'op-1',
      sandboxId: 'sb-1',
      type: 'ADD',
      status: 'COMPLETED',
      phase: 'COMMITTED',
      expectedGeneration: 1,
      targetGeneration: 2,
      policyRevision: 1,
      createdAt: '2026-10-06T00:00:00Z',
      updatedAt: '2026-10-06T00:00:01Z',
    }
    sandboxApi.getSandbox
      .mockResolvedValueOnce(createApiResponse(baseDto))
      .mockResolvedValueOnce(createApiResponse(updated))
    sandboxApi.addSandboxNodes.mockResolvedValue(createApiResponse(operation))

    const result = await nodes.add([{ cpu: 32, memory: 256, disk: 300 }], 0)

    expect(Array.isArray(nodes)).toBe(true)
    expect(sandbox.nodes).toBe(nodes)
    expect(nodes.map(({ rank, id }) => ({ rank, id }))).toEqual([
      { rank: 0, id: 'sb-1' },
      { rank: 1, id: 'sb-2' },
    ])
    expect(result).toBe(operation)
    expect(sandboxApi.addSandboxNodes).toHaveBeenCalledWith(
      'sb-1',
      { expectedGeneration: 1, nodes: [{ cpu: 32, memory: 256, disk: 300 }] },
      undefined,
      undefined,
    )
  })

  it('sends generation-checked release and replacement requests', async () => {
    const { sandbox, sandboxApi } = makeSandbox()
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse(baseDto))
    sandboxApi.removeSandboxNodes.mockResolvedValue(
      createApiResponse({ id: 'remove-1', type: 'REMOVE', status: 'COMPLETED' }),
    )
    sandboxApi.replaceSandboxNode.mockResolvedValue(
      createApiResponse({ id: 'replace-1', type: 'REPLACE', status: 'COMPLETED' }),
    )

    await sandbox.nodes.release(1, 0)
    await sandbox.nodes.replace(2, 0)

    expect(sandboxApi.removeSandboxNodes).toHaveBeenCalledWith(
      'sb-1',
      { expectedGeneration: 1, count: 1 },
      undefined,
      undefined,
    )
    expect(sandboxApi.replaceSandboxNode).toHaveBeenCalledWith(
      'sb-1',
      '2',
      { expectedGeneration: 1 },
      undefined,
      undefined,
    )
  })

  it('rejects node arrays that violate the rank invariant', () => {
    expect(() =>
      makeSandbox({
        nodeCount: 2,
        nodes: [
          { rank: 0, id: 'sb-1', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
          { rank: 2, id: 'sb-2', state: 'started', incarnation: 1, cpu: 16, gpu: 8, memory: 192, disk: 512 },
        ],
      }),
    ).toThrow('nodes[i].rank must equal i')
  })

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

  it('event-streaming mode polls immediately, then at the ~1s safety-net cadence', async () => {
    const { sandbox } = makeSandbox({ state: 'starting' }, 'sub-1')
    const times: number[] = []
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      times.push(Date.now())
      if (times.length >= 2) {
        ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
      }
    })

    const start = Date.now()
    await sandbox.waitUntilStarted(5)

    expect(times[0] - start).toBeLessThan(400)
    expect(times[1] - times[0]).toBeGreaterThan(800)
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

describe('lightweight gang events', () => {
  it('preserves topology metadata omitted from list-item events', () => {
    const { sandbox } = makeSandbox({
      nodeCount: 2,
      degraded: true,
      topologyGeneration: 7,
      nodes: [baseDto.nodes![0], { ...baseDto.nodes![0], rank: 1, id: 'worker-1' }],
    })
    const { nodeCount, degraded, topologyGeneration, nodes, ...event } = baseDto
    void [nodeCount, degraded, topologyGeneration, nodes]
    ;(sandbox as unknown as { processSandboxDto: (dto: typeof event) => void }).processSandboxDto(event)
    expect(sandbox.nodeCount).toBe(2)
    expect(sandbox.degraded).toBe(true)
    expect(sandbox.topologyGeneration).toBe(7)
    expect(sandbox.nodes).toHaveLength(2)
  })
})
