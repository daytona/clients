// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Configuration, Sandbox as SandboxDto } from '@daytona/api-client'
import { ProcessApi } from '@daytona/toolbox-api-client'
import { createApiResponse } from './helpers'
import { io } from 'socket.io-client'
import { EventDispatcher, SUBSCRIPTION_REJECTED_EVENT } from '../utils/EventDispatcher'
import { EventSubscriptionManager } from '../utils/EventSubscriptionManager'
import {
  DaytonaConflictError,
  DaytonaError,
  DaytonaInternalServerError,
  DaytonaQueueTimeoutError,
  DaytonaRateLimitError,
  DaytonaSpotEvictedError,
  DaytonaTimeoutError,
} from '../errors/DaytonaError'
import { AxiosHeaders } from 'axios'
import { FALLBACK_POLL_MAX_INTERVAL_MS, WaitPollCadence, isRateLimitError, retryAfterMs } from '../utils/WaitPolling'

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

// baseDto for another sandbox id: its head node carries the same id.
const dtoFor = (id: string, overrides: Partial<SandboxDto> = {}): SandboxDto => ({
  ...baseDto,
  ...overrides,
  id,
  nodes: [{ ...baseDto.nodes![0], id }],
})

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
    addSandboxNodes: jest.fn(),
    removeSandboxNodes: jest.fn(),
    replaceSandboxNode: jest.fn(),
    getGangMembershipOperation: jest.fn(),
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

it.each([true, false])('opens a directly fetched worker at its physical rank (member array=%s)', (full) => {
  const { sandbox } = makeSandbox({
    id: 'worker',
    headSandboxId: 'head',
    nodeRank: 2,
    nodeCount: 3,
    nodes: full ? [{ ...baseDto.nodes![0], id: 'worker', rank: 2 }] : undefined,
  })
  expect(sandbox.nodes.map((node) => [node.id, node.rank])).toEqual([['worker', 2]])
  expect(sandbox.nodes[0].cpu).toBe(baseDto.cpu)
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
    expect(sandboxApi.addSandboxNodes.mock.calls[0][3]).toEqual({ timeout: 2500 })
    expect(sandboxApi.getSandbox.mock.calls[0][3]).toEqual({ timeout: 1500 })
  })

  it('returns a confirmed operation even when the deadline prevents refreshing handles', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getGangMembershipOperation.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' })), 2000)
        }),
    )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 2).catch((error) => error)
    await jest.advanceTimersByTimeAsync(3000)
    expect(await result).toMatchObject({ id: 'op-1', status: 'COMPLETED' })
    expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
  })

  it('does not start a poll after the budget expires during sleep', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getGangMembershipOperation.mockResolvedValue(
      createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' }),
    )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 1).catch((error) => error)
    await jest.advanceTimersByTimeAsync(1000)
    expect(await result).toBeInstanceOf(DaytonaTimeoutError)
    expect(await result).toMatchObject({ operationId: 'op-1' })
    expect(sandboxApi.getGangMembershipOperation).not.toHaveBeenCalled()
  })

  it('returns a confirmed operation when the final refresh arrives late', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.addSandboxNodes.mockResolvedValue(createApiResponse({ id: 'op-1', type: 'ADD', status: 'COMPLETED' }))
    sandboxApi.getSandbox.mockReset().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(createApiResponse(baseDto)), 3000)
        }),
    )
    const result = sandbox.nodes.add([{ cpu: 16, memory: 192, disk: 512 }], 2).catch((error) => error)
    await jest.advanceTimersByTimeAsync(3000)
    expect(await result).toMatchObject({ id: 'op-1', status: 'COMPLETED' })
  })

  it('attaches the operation ID when the server reports terminal failure', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.removeSandboxNodes.mockResolvedValue(
      createApiResponse({ id: 'op-1', type: 'REMOVE', status: 'FAILED', errorReason: 'capacity lost' }),
    )
    await expect(sandbox.nodes.release()).rejects.toMatchObject({ message: 'capacity lost', operationId: 'op-1' })
    expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
  })

  it('retains a completed release if refreshing its handles fails', async () => {
    const { sandbox, sandboxApi } = prepare()
    sandboxApi.getSandbox.mockReset().mockRejectedValueOnce(new Error('503'))
    const op = { id: 'release-1', status: 'COMPLETED', type: 'REMOVE' }
    sandboxApi.removeSandboxNodes.mockResolvedValue(createApiResponse(op))
    await expect(sandbox.nodes.release(1)).resolves.toBe(op)
    expect(sandboxApi.removeSandboxNodes).toHaveBeenCalledTimes(1)
  })
})

describe('all-node execution deadlines', () => {
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
      }
      Object.assign(node.process, methods)
      return methods
    })
    return { sandbox, processes }
  }

  it.each([0, 15])('forwards the buffered execution timeout %s to each rank', async (timeout) => {
    const { sandbox, processes } = makeNodes(2)
    const options = { timeout }

    const results = await sandbox.nodes.process.executeCommand('workload', options)

    expect(Object.keys(results)).toEqual(['0', '1'])
    processes.forEach((process) =>
      expect(process.executeCommand).toHaveBeenCalledWith('workload', undefined, undefined, timeout),
    )
  })

  it.each([-1, 1.5, NaN, Infinity])('rejects invalid timeout %s before dispatch', async (timeout) => {
    const { sandbox, processes } = makeNodes()
    const options = { timeout }

    await expect(sandbox.nodes.process.executeCommand('workload', options)).rejects.toThrow(/timeout/i)
    expect(processes[0].executeCommand).not.toHaveBeenCalled()
  })

  it('bounds the RDMA prerequisite check', async () => {
    const { sandbox, processes } = makeNodes(2)
    await sandbox.nodes.process.executeCommand('workload', { timeout: 1 })
    for (const process of processes)
      expect(process.executeCommand.mock.calls[0].slice(1)).toEqual([undefined, undefined, 5])
  })

  it('reuses the RDMA probe until the node incarnation changes', async () => {
    const { sandbox, processes } = makeNodes(2)
    await Promise.all([sandbox.nodes.process.executeCommand('a'), sandbox.nodes.process.executeCommand('b')])
    sandbox.nodes[1].update({ ...sandbox.nodes[1], incarnation: 2 })
    await sandbox.nodes.process.executeCommand('c')
    expect(processes.map((p) => p.executeCommand.mock.calls.filter((call) => call[3] === 5).length)).toEqual([1, 2])
  })

  it('still executes when the advisory RDMA probe fails', async () => {
    const { sandbox, processes } = makeNodes(2)
    processes[0].executeCommand.mockRejectedValueOnce(new Error('probe timed out'))
    const results = await sandbox.nodes.process.executeCommand('workload')
    expect(results[0]).toMatchObject({ exitCode: 0, result: 'done', artifacts: { stdout: 'done' } })
    expect(processes[0].executeCommand).toHaveBeenCalledWith('workload')
    expect(sandbox.nodes[0].warnings.join(' ')).toMatch(/unable|could not|failed/i)
  })

  it('keeps completed ranks when another rank fails', async () => {
    const { sandbox, processes } = makeNodes(2)
    const error = new DaytonaTimeoutError('rank 0 exceeded its timeout')
    processes[0].executeCommand.mockResolvedValueOnce({ exitCode: 0 }).mockRejectedValueOnce(error)
    await expect(sandbox.nodes.process.executeCommand('workload', { timeout: 1 })).rejects.toMatchObject({
      nodeFailures: { 0: error },
      nodeResults: { 1: { result: 'done' } },
    })
  })
})

describe('Sandbox', () => {
  it('normalizes an older API response to one head node', () => {
    const { sandbox } = makeSandbox({ nodes: undefined, nodeCount: undefined })

    expect(sandbox.nodeCount).toBe(1)
    expect(sandbox.nodes.map(({ rank, id }) => ({ rank, id }))).toEqual([{ rank: 0, id: sandbox.id }])
  })

  it('waits for member details before exposing ranks on a fresh gang', async () => {
    const { sandbox, sandboxApi } = makeSandbox({ nodeCount: 2, nodes: undefined })
    const nodes = sandbox.nodes

    expect(sandbox.nodeCount).toBe(2)
    expect(nodes).toHaveLength(0)
    await expect(nodes.process.executeCommand('hostname')).rejects.toThrow(/membership is unavailable/i)
    await expect(nodes.fs.uploadFile(Buffer.from('data'), '/file')).rejects.toThrow(/membership is unavailable/i)

    const head = baseDto.nodes![0]
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({
        ...baseDto,
        nodeCount: 2,
        nodes: [head, { ...head, rank: 1, id: 'worker' }],
      }),
    )
    await sandbox.refreshData()
    expect(nodes.map((node) => node.id)).toEqual([sandbox.id, 'worker'])
  })

  it('refreshes an existing single-node handle when the API omits the member array', async () => {
    const single = { ...baseDto }
    delete single.nodes
    const { sandbox, sandboxApi } = makeSandbox({ ...single, nodes: undefined })
    const head = sandbox.nodes[0]
    sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...single, cpu: 4, state: 'started' }))
    await sandbox.refreshData()
    expect(sandbox.nodes[0]).toBe(head)
    expect(head).toMatchObject({ cpu: 4, state: 'started' })
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

  it('uploads to every node and identifies the rank whose upload failed', async () => {
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
    sandboxApi.getSandbox.mockResolvedValueOnce(createApiResponse(updated))
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

  it('releases against the membership the caller saw even after a sparse refresh', async () => {
    const { sandbox, sandboxApi } = makeSandbox({
      nodeCount: 2,
      nodes: [baseDto.nodes![0], { ...baseDto.nodes![0], rank: 1, id: 'worker' }],
    })
    sandboxApi.getSandbox.mockResolvedValue(
      createApiResponse({ ...baseDto, nodeCount: 3, topologyGeneration: 2, nodes: undefined }),
    )
    await sandbox.refreshData()
    const conflict = new DaytonaConflictError('Topology changed')
    sandboxApi.removeSandboxNodes.mockRejectedValue(conflict)

    await expect(sandbox.nodes.release(1, 0)).rejects.toBe(conflict)
    expect(sandboxApi.removeSandboxNodes).toHaveBeenCalledWith(
      'sb-1',
      { expectedGeneration: 1, count: 1 },
      undefined,
      undefined,
    )
    expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
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

  it('waitUntilStarted reports the error reason carried by the state event', async () => {
    const { sandbox } = makeSandbox({ state: 'starting' })
    const handleEvent = (sandbox as unknown as { handleEvent: (name: string, data: unknown) => void }).handleEvent.bind(
      sandbox,
    )

    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      handleEvent('sandbox.state.updated', {
        sandbox: { id: 'sb-1', state: 'error', desiredState: 'started', errorReason: 'disk full' },
        oldState: 'starting',
        newState: 'error',
      })
    })

    await expect(sandbox.waitUntilStarted(5)).rejects.toThrow(
      'Sandbox sb-1 failed to start with status: error, error reason: disk full',
    )
  })

  it('a state event without an error reason clears the previous one', () => {
    const { sandbox } = makeSandbox({ state: 'error', errorReason: 'transient' })

    ;(sandbox as unknown as { handleEvent: (name: string, data: unknown) => void }).handleEvent(
      'sandbox.state.updated',
      { sandbox: { id: 'sb-1', state: 'started' }, oldState: 'error', newState: 'started' },
    )

    expect(sandbox.state).toBe('started')
    expect(sandbox.errorReason).toBeUndefined()
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
      sandboxApi.forkSandbox = jest.fn().mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'starting' })))

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
        .mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'starting', toolboxProxyUrl: undefined })))
      sandboxApi.getToolboxProxyUrl = jest.fn().mockResolvedValue(createApiResponse({ url: 'http://proxy' }))
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'starting' })))

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
      sandboxApi.forkSandbox = jest.fn().mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'starting' })))
      sandboxApi.getSandbox.mockImplementation(async () => {
        subscriptionManager.isConnected = true
        return createApiResponse(dtoFor('sb-fork', { state: 'started' }))
      })

      await expectImmediateSingleRead(async () => {
        await sandbox.fork(undefined, 60)
      }, sandboxApi.getSandbox)
      expect(sandboxApi.getSandbox).toHaveBeenCalledWith('sb-fork')
    })

    it('fork() whose response is already started takes no wait and no subscription', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.forkSandbox = jest.fn().mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'started' })))

      await sandbox.fork(undefined, 30)

      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
      expect(subscriptionManager.subscribe).not.toHaveBeenCalled()
    })

    test.each([
      ['an empty body', undefined],
      ['a body without a state', { id: 'sb-1' }],
      ['a body with an empty state', { id: 'sb-1', state: '' }],
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

    it('stop() reads first when its subscription expired during the request', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockImplementation(async () => {
        // The request outlived the subscription's TTL: the manager dropped it.
        subscriptionManager.refresh.mockReturnValueOnce(false)
        return createApiResponse({ ...baseDto, state: 'stopping' })
      })
      sandboxApi.getSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopped' }))

      await expectImmediateSingleRead(() => sandbox.stop(60), sandboxApi.getSandbox)
    })

    it('a rejection during a read runs exactly one more read right after it, then the fallback cadence', async () => {
      jest.useFakeTimers()
      const random = jest.spyOn(Math, 'random').mockReturnValue(0.5)
      try {
        const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
        sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))
        let inFlight = 0
        let maxInFlight = 0
        const reads: number[] = []
        let release: (() => void) | undefined
        sandboxApi.getSandbox.mockImplementation(async () => {
          inFlight++
          maxInFlight = Math.max(maxInFlight, inFlight)
          reads.push(Date.now())
          if (reads.length === 1) {
            await new Promise<void>((resolve) => (release = resolve))
          }
          inFlight--
          return createApiResponse({ ...baseDto, state: reads.length >= 3 ? 'stopped' : 'stopping' })
        })
        subscriptionManager.isConnected = false

        const stop = sandbox.stop(60)
        await jest.advanceTimersByTimeAsync(0)
        expect(reads).toHaveLength(1)
        subscriptionManager.isConnected = true
        subscribedHandler(subscriptionManager)(SUBSCRIPTION_REJECTED_EVENT, { id: 'sb-1', error: 'OVERLOADED' })
        subscriptionManager.isConnected = false
        await jest.advanceTimersByTimeAsync(0)
        expect(reads).toHaveLength(1)

        release?.()
        await jest.advanceTimersByTimeAsync(0)
        expect(reads).toHaveLength(2)
        expect(reads[1] - reads[0]).toBe(0)

        await jest.advanceTimersByTimeAsync(1000)
        await stop
        expect(reads).toHaveLength(3)
        expect(reads[2] - reads[1]).toBe(1000)
        expect(maxInFlight).toBe(1)
      } finally {
        random.mockRestore()
        jest.useRealTimers()
      }
    })

    it('an error state from an event reports the reason the event carried, without a read', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))

      const stop = sandbox.stop(30)
      await flush()
      subscribedHandler(subscriptionManager)('sandbox.state.updated', {
        sandbox: { id: 'sb-1', state: 'error', errorReason: 'disk full' },
        newState: 'error',
      })

      await expect(stop).rejects.toThrow('error reason: disk full')
      expect(sandboxApi.getSandbox).not.toHaveBeenCalled()
    })

    it('an error state from an event without its reason reads the sandbox first, and reports its reason', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started', errorReason: 'stale' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))
      sandboxApi.getSandbox.mockResolvedValue(
        createApiResponse({ ...baseDto, state: 'error', errorReason: 'from the read' }),
      )

      const stop = sandbox.stop(30)
      await flush()
      subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('error'))

      await expect(stop).rejects.toThrow('error reason: from the read')
      expect(sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
    })

    it('an error state from an event whose read fails still fails the wait', async () => {
      const { sandbox, sandboxApi, subscriptionManager } = makeSandbox({ state: 'started' })
      sandboxApi.stopSandbox.mockResolvedValue(createApiResponse({ ...baseDto, state: 'stopping' }))
      sandboxApi.getSandbox.mockRejectedValue(new Error('network down'))

      const stop = sandbox.stop(30)
      await flush()
      subscribedHandler(subscriptionManager)('sandbox.state.updated', slimStateEvent('error'))

      await expect(stop).rejects.toThrow('Sandbox failed to stop with status: error')
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

  it('a not-event-backed wait backs off from 1s to 10s, polls every 5s once event-backed, and restarts at 1s', async () => {
    jest.useFakeTimers()
    const random = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const { sandbox, subscriptionManager } = makeSandbox({ state: 'starting' })
      subscriptionManager.isConnected = false
      const times: number[] = []
      jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
        times.push(Date.now())
        // Polls 1-8 are not event-backed, 9-10 are, and from 11 on the connection is down again.
        if (times.length === 9) subscriptionManager.isConnected = true
        if (times.length === 11) subscriptionManager.isConnected = false
        if (times.length >= 13) {
          ;(sandbox as unknown as { applyState: (state: string) => void }).applyState('started')
        }
      })

      const wait = sandbox.waitUntilStarted(120)
      await jest.advanceTimersByTimeAsync(80_000)
      await wait

      const gaps = times.slice(1).map((t, i) => t - times[i])
      const expected = [1000, 1500, 2250, 3375, 5062.5, 7593.75, 10_000, 10_000, 5000, 5000, 1000, 1500]
      expect(gaps).toHaveLength(expected.length)
      gaps.forEach((gap, i) => expect(Math.abs(gap - expected[i])).toBeLessThanOrEqual(1))
    } finally {
      random.mockRestore()
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

describe('WaitPollCadence', () => {
  test.each([
    ['low', 0, 800, 3000],
    ['middle', 0.5, 1000, 3300],
    ['high', 1, 1200, 3600],
  ])('a rate-limited poll backs off, and Retry-After gets upward jitter only (jitter %s)', (_n, r, plain, retry) => {
    // Without a subscription the fallback backoff still applies: max(fast 100 ms, 1 s x jitter).
    expect(new WaitPollCadence(() => r).rateLimited(false, false, 100)).toBeCloseTo(plain, 6)
    expect(new WaitPollCadence(() => r).rateLimited(true, false, 100, 3000)).toBeCloseTo(retry, 6)
    // A Retry-After shorter than the interval changes nothing.
    expect(new WaitPollCadence(() => r).rateLimited(true, false, 100, 10)).toBeCloseTo(plain, 6)
    // Event-backed: the 5 s interval wins over the 1 s fallback.
    expect(new WaitPollCadence(() => r).rateLimited(true, true, 100)).toBeCloseTo(plain * 5, 6)
  })

  it('every rate-limited poll takes one backoff step', () => {
    const cadence = new WaitPollCadence(() => 0.5)
    expect([1, 2, 3].map(() => cadence.rateLimited(false, false, 100))).toEqual([1000, 1500, 2250])
    expect(cadence.next(true, false, 100)).toBe(3375)
  })

  const base = [1000, 1500, 2250, 3375, 5062.5, 7593.75, 10_000, 10_000]

  it('uses the fast cadence unchanged and without jitter when not subscribed', () => {
    const random = jest.fn(() => 0)
    const cadence = new WaitPollCadence(random)
    expect(cadence.next(false, false, 100)).toBe(100)
    expect(cadence.next(false, true, 133.1)).toBe(133.1)
    expect(random).not.toHaveBeenCalled()
  })

  it('polls every 5s x jitter when event-backed', () => {
    expect(new WaitPollCadence(() => 0).next(true, true, 100)).toBe(4000)
    expect(new WaitPollCadence(() => 0.5).next(true, true, 100)).toBe(5000)
    expect(new WaitPollCadence(() => 1).next(true, true, 100)).toBe(6000)
  })

  test.each([
    ['low', 0, 0.8],
    ['middle', 0.5, 1],
    ['high', 1, 1.2],
  ])('backs off 1s x1.5 up to 10s when not event-backed (jitter %s)', (_name, r, factor) => {
    const cadence = new WaitPollCadence(() => r)
    const intervals = base.map(() => cadence.next(true, false, 100))
    intervals.forEach((interval, i) => expect(interval).toBeCloseTo(base[i] * factor, 6))
    expect(Math.max(...intervals)).toBeCloseTo(FALLBACK_POLL_MAX_INTERVAL_MS * factor, 6)
  })

  it('restarts the backoff at 1s after a stretch of being event-backed', () => {
    const cadence = new WaitPollCadence(() => 0.5)
    expect([1, 2, 3].map(() => cadence.next(true, false, 100))).toEqual([1000, 1500, 2250])
    expect(cadence.next(true, true, 100)).toBe(5000)
    expect([1, 2].map(() => cadence.next(true, false, 100))).toEqual([1000, 1500])
  })
})

describe('retryAfterMs', () => {
  const rateLimited = (headers?: Record<string, string>) =>
    new DaytonaRateLimitError('Too Many Requests', 429, headers ? new AxiosHeaders(headers) : undefined)

  it('reads seconds and HTTP dates, and ignores a missing or invalid header', () => {
    const now = Date.parse('Wed, 21 Oct 2026 07:28:00 GMT')
    expect(retryAfterMs(rateLimited({ 'Retry-After': '3' }), now)).toBe(3000)
    expect(retryAfterMs(rateLimited({ 'retry-after': 'Wed, 21 Oct 2026 07:28:05 GMT' }), now)).toBe(5000)
    expect(retryAfterMs(rateLimited({ 'retry-after': 'Wed, 21 Oct 2026 07:27:00 GMT' }), now)).toBe(0)
    expect(retryAfterMs(rateLimited({ 'retry-after': 'soon' }), now)).toBeUndefined()
    expect(retryAfterMs(rateLimited(), now)).toBeUndefined()
  })

  test.each([
    ['inf'],
    ['-inf'],
    ['nan'],
    ['NaN'],
    ['Infinity'],
    ['-Infinity'],
    ['-1'],
    ['-0.5'],
    ['1e400'],
    ['1.5'],
    [''],
  ])('treats %j as absent', (value) => {
    expect(retryAfterMs(rateLimited({ 'retry-after': value }))).toBeUndefined()
  })

  test.each([
    ['120', 60_000],
    ['99999999999999999999', 60_000],
    ['60', 60_000],
    ['59', 59_000],
  ])('caps %s seconds at 60 s', (value, expected) => {
    expect(retryAfterMs(rateLimited({ 'retry-after': value }))).toBe(expected)
  })

  it('caps an HTTP date far ahead at 60 s', () => {
    const now = Date.parse('Wed, 21 Oct 2026 07:28:00 GMT')
    expect(retryAfterMs(rateLimited({ 'retry-after': 'Wed, 21 Oct 2026 09:00:00 GMT' }), now)).toBe(60_000)
  })

  it('treats any 429 as a rate limit', () => {
    expect(isRateLimitError(rateLimited())).toBe(true)
    expect(isRateLimitError(new DaytonaError('Too Many Requests', 429))).toBe(true)
    expect(isRateLimitError(new DaytonaInternalServerError('boom', 500))).toBe(false)
  })
})

describe('Sandbox wait polls that hit a rate limit', () => {
  const rateLimited = (retryAfter?: string) =>
    new DaytonaRateLimitError(
      'Too Many Requests',
      429,
      retryAfter === undefined ? undefined : new AxiosHeaders({ 'retry-after': retryAfter }),
    )

  // A wait whose reads follow `reads`: subscribed but disconnected (fallback cadence) by default, or
  // without a subscription (fast cadence).
  const run = async (
    method: 'waitUntilStarted' | 'waitUntilStopped',
    reads: Array<Error | string>,
    subscription: 'fallback' | 'none' = 'fallback',
  ): Promise<{ times: number[]; outcome: Promise<unknown> }> => {
    const initial = method === 'waitUntilStarted' ? 'starting' : 'stopping'
    const { sandbox, subscriptionManager } = makeSandbox({ state: initial }, subscription === 'none' ? '' : 'sub-1')
    subscriptionManager.isConnected = false
    const times: number[] = []
    jest.spyOn(sandbox, 'refreshData').mockImplementation(async () => {
      times.push(Date.now())
      const next = reads[Math.min(times.length - 1, reads.length - 1)]
      if (next instanceof Error) throw next
      ;(sandbox as unknown as { applyState: (state: string) => void }).applyState(next)
    })
    const outcome = sandbox[method](120).then(
      () => 'resolved',
      (error: unknown) => error,
    )
    await jest.advanceTimersByTimeAsync(0)
    return { times, outcome }
  }
  const gaps = (times: number[]): number[] => times.slice(1).map((t, i) => t - times[i])

  let random: jest.SpyInstance
  beforeEach(() => {
    jest.useFakeTimers()
    random = jest.spyOn(Math, 'random').mockReturnValue(0.5)
  })
  afterEach(() => {
    random.mockRestore()
    jest.useRealTimers()
  })

  test.each([
    ['waitUntilStarted' as const, 0, 3000],
    ['waitUntilStarted' as const, 1, 3600],
    ['waitUntilStopped' as const, 0.5, 3300],
  ])(
    '%s: a 429 with Retry-After: 3 (jitter %s) waits 3 s plus up to 20%%, and the wait still resolves',
    async (method, jitter, expectedMs) => {
      random.mockReturnValue(jitter)
      const target = method === 'waitUntilStarted' ? 'started' : 'stopped'
      const { times, outcome } = await run(method, [rateLimited('3'), target])
      expect(times).toHaveLength(1)

      await jest.advanceTimersByTimeAsync(expectedMs - 1)
      expect(times).toHaveLength(1)
      await jest.advanceTimersByTimeAsync(1)
      expect(times).toHaveLength(2)
      await expect(outcome).resolves.toBe('resolved')
    },
  )

  it('a 429 that is not a DaytonaRateLimitError still backs off and honours Retry-After', async () => {
    random.mockReturnValue(0)
    const limited = new DaytonaError('Too Many Requests', 429, new AxiosHeaders({ 'retry-after': '2' }))
    const { times, outcome } = await run('waitUntilStarted', [limited, 'started'])

    await jest.advanceTimersByTimeAsync(5000)
    await expect(outcome).resolves.toBe('resolved')
    expect(gaps(times)).toEqual([2000])
  })

  it('caps a huge Retry-After at 60 s before the jitter', async () => {
    random.mockReturnValue(0)
    const { times, outcome } = await run('waitUntilStarted', [rateLimited('3600'), 'started'])

    await jest.advanceTimersByTimeAsync(59_999)
    expect(times).toHaveLength(1)
    await jest.advanceTimersByTimeAsync(1)
    await expect(outcome).resolves.toBe('resolved')
    expect(gaps(times)).toEqual([60_000])
  })

  test.each([
    ['without Retry-After', undefined],
    ['with a Retry-After shorter than the interval', '0'],
  ])('a 429 %s waits the current interval, which takes its backoff step', async (_name, retryAfter) => {
    const { times, outcome } = await run('waitUntilStarted', [rateLimited(retryAfter), 'starting', 'started'])

    await jest.advanceTimersByTimeAsync(10_000)
    await expect(outcome).resolves.toBe('resolved')
    // 1 s (the current interval), then 1.5 s: the rate-limited poll counted as one backoff step.
    expect(gaps(times)).toEqual([1000, 1500])
  })

  it('repeated 429s back off from 1 s to 10 s even without a subscription', async () => {
    const reads: Array<Error | string> = Array.from({ length: 9 }, () => rateLimited())
    const { times, outcome } = await run('waitUntilStarted', [...reads, 'started'], 'none')

    await jest.advanceTimersByTimeAsync(60_000)
    await expect(outcome).resolves.toBe('resolved')
    const expected = [1000, 1500, 2250, 3375, 5062.5, 7593.75, 10_000, 10_000, 10_000]
    expect(gaps(times)).toHaveLength(expected.length)
    // Fake timers run fractional delays on whole milliseconds.
    gaps(times).forEach((gap, i) => expect(Math.abs(gap - expected[i])).toBeLessThanOrEqual(1))
  })

  it('without a subscription the fast cadence resumes after a successful poll', async () => {
    const { times, outcome } = await run('waitUntilStarted', [rateLimited(), 'starting', 'started'], 'none')

    await jest.advanceTimersByTimeAsync(5000)
    await expect(outcome).resolves.toBe('resolved')
    expect(gaps(times)).toEqual([1000, 100])
  })

  it('a 500 fails the wait exactly as before', async () => {
    const failure = new DaytonaInternalServerError('boom', 500)
    const { times, outcome } = await run('waitUntilStarted', [failure])

    await expect(outcome).resolves.toBe(failure)
    expect(times).toHaveLength(1)
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
    sandboxApi.forkSandbox = jest.fn().mockResolvedValue(createApiResponse(dtoFor('sb-fork', { state: 'started' })))

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
  // What the client sent, without acknowledgement callbacks.
  readonly sent = jest.fn()
  // Acknowledgement callbacks of the messages that asked for one.
  readonly acks: { event: string; payload: unknown; ack: (response?: unknown) => void }[] = []
  emit(event: string, payload: unknown, ack?: (response?: unknown) => void): this {
    this.sent(event, payload)
    if (ack) this.acks.push({ event, payload, ack })
    return this
  }
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
    socket.sent.mock.calls
      .filter(([name]) => name === event)
      .flatMap(([, payload]) => (payload as { sandboxIds: string[] }).sandboxIds)

  const stateEvent = (id: string, state: string) => ({ sandbox: { id, state }, newState: state })

  // A sandbox whose stop request returns 'stopping' and whose reads keep saying 'stopping'.
  const stoppingSandbox = (id: string) => {
    const made = makeSandbox(dtoFor(id, { state: 'started' }), 'unused', manager)
    made.sandboxApi.stopSandbox.mockResolvedValue(createApiResponse(dtoFor(id, { state: 'stopping' })))
    made.sandboxApi.getSandbox.mockResolvedValue(createApiResponse(dtoFor(id, { state: 'stopping' })))
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

    // The fallback polls come after 1 s and 1.5 s more (x0.8-1.2 jitter, so at most 3 s together);
    // the event-backed waits poll no earlier than 4 s.
    await jest.advanceTimersByTimeAsync(3000)
    expect(c.sandboxApi.getSandbox).toHaveBeenCalledTimes(3)
    expect(a.sandboxApi.getSandbox).not.toHaveBeenCalled()
    expect(b.sandboxApi.getSandbox).not.toHaveBeenCalled()

    socket.fire('sandbox.state.updated', stateEvent('sb-a', 'stopped'))
    socket.fire('sandbox.state.updated', stateEvent('sb-b', 'stopped'))
    c.sandboxApi.getSandbox.mockResolvedValue(createApiResponse(dtoFor('sb-c', { state: 'stopped' })))
    // The next fallback poll is 2.25 s (at most 2.7 s) after the previous one.
    await jest.advanceTimersByTimeAsync(3000)
    await Promise.all(stops)
    expect(c.sandboxApi.getSandbox).toHaveBeenCalledTimes(4)
    expect(subscribedIds('subscribe')).toEqual(['sb-a', 'sb-b'])
  })

  it('a rejected subscription makes its wait read at once, then poll at the fallback cadence', async () => {
    const random = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    try {
      const a = stoppingSandbox('sb-a')
      const handler = jest.fn()
      const stop = a.sandbox.stop(60)
      await jest.advanceTimersByTimeAsync(0)
      // Live and event-backed before the request: the wait skipped its first read.
      expect(a.sandboxApi.getSandbox).not.toHaveBeenCalled()
      dispatcher.subscribe('sb-a', handler, ['sandbox.state.updated'])

      const ack = socket.acks.find(({ payload }) => (payload as { sandboxIds: string[] }).sandboxIds.includes('sb-a'))
      ack?.ack({ ok: false, error: 'OVERLOADED', message: 'shedding' })
      expect(handler).toHaveBeenCalledWith(SUBSCRIPTION_REJECTED_EVENT, expect.anything())
      expect(manager.isEventBacked('sb-a')).toBe(false)
      await jest.advanceTimersByTimeAsync(0)
      expect(a.sandboxApi.getSandbox).toHaveBeenCalledTimes(1)

      await jest.advanceTimersByTimeAsync(999)
      expect(a.sandboxApi.getSandbox).toHaveBeenCalledTimes(1)
      await jest.advanceTimersByTimeAsync(1)
      expect(a.sandboxApi.getSandbox).toHaveBeenCalledTimes(2)
      await jest.advanceTimersByTimeAsync(1500)
      expect(a.sandboxApi.getSandbox).toHaveBeenCalledTimes(3)

      a.sandboxApi.getSandbox.mockResolvedValue(createApiResponse(dtoFor('sb-a', { state: 'stopped' })))
      await jest.advanceTimersByTimeAsync(2250)
      await stop
      expect(a.sandboxApi.getSandbox).toHaveBeenCalledTimes(4)
    } finally {
      random.mockRestore()
    }
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
    c.sandboxApi.getSandbox.mockResolvedValue(createApiResponse(dtoFor('sb-c', { state: 'stopped' })))
    await jest.advanceTimersByTimeAsync(1500)
    await Promise.all([stopB, stopC, stopD])
    // sb-c was never served, so its last listener sends no unsubscribe.
    expect(subscribedIds('unsubscribe')).toEqual(['sb-a', 'sb-b', 'sb-d'])
  })
})

describe('lightweight gang events', () => {
  it('preserves node details when a sandbox event omits members', () => {
    const { sandbox } = makeSandbox({
      nodeCount: 1,
      nodes: [{ ...baseDto.nodes![0], incarnation: 3, fabricHealthy: true }],
    })
    const head = sandbox.nodes[0]
    const event = { ...baseDto, nodes: undefined, cpu: 6, gpu: 1, memory: 12, disk: 40, state: 'started' }
    const listener = sandbox as unknown as {
      handleEvent(name: string, data: unknown): void
      processSandboxDto(dto: unknown): void
    }
    listener.processSandboxDto(event)
    expect(sandbox.nodes[0]).toBe(head)
    expect(head).toMatchObject({
      cpu: 6,
      gpu: 1,
      memory: 12,
      disk: 40,
      state: 'started',
      incarnation: 3,
      fabricHealthy: true,
    })

    listener.processSandboxDto({ ...event, incarnation: 4 })
    expect(head).toMatchObject({ incarnation: 4, fabricHealthy: undefined })
    listener.handleEvent('sandbox.state.updated', { newState: 'stopped' })
    expect(head.state).toBe('stopped')
  })

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
