// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Configuration, Process as ProcessRecord } from '@daytona/toolbox-api-client'
import { DaytonaCursorExpiredError, DaytonaInvalidArgumentError } from '../errors/DaytonaError'
import { createApiResponse } from './helpers'

const mockCreateSandboxWebSocket = jest.fn()

jest.mock('../utils/WebSocket', () => ({
  createSandboxWebSocket: (...args: unknown[]) => mockCreateSandboxWebSocket(...args),
}))

function makeProcessRecord(id: string, overrides: Partial<ProcessRecord> = {}): ProcessRecord {
  return {
    id,
    createdAt: '2026-07-29T00:00:00.000Z',
    kind: 'exec',
    state: 'running',
    ...overrides,
  }
}

function makeSseResponse(
  chunks: readonly string[],
  trailing?: { readonly chunks: readonly string[]; readonly afterMs: number },
): Response {
  const encoder = new TextEncoder()
  let trailingTimer: ReturnType<typeof setTimeout> | undefined
  let cancelled = false
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      if (trailing === undefined) {
        controller.close()
        return
      }
      // A run() deadline cancels this stream before the trailing chunks are due.
      // Enqueueing into a cancelled stream throws from the timer callback,
      // outside any test's await, so the failure lands on an unrelated test.
      trailingTimer = setTimeout(() => {
        if (cancelled) {
          return
        }
        for (const chunk of trailing.chunks) {
          controller.enqueue(encoder.encode(chunk))
        }
        controller.close()
      }, trailing.afterMs)
    },
    cancel() {
      cancelled = true
      if (trailingTimer !== undefined) {
        clearTimeout(trailingTimer)
        trailingTimer = undefined
      }
    },
  })

  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('Process surface', () => {
  let fetchMock: jest.Mock<ReturnType<typeof fetch>, Parameters<typeof fetch>>
  const originalFetch = global.fetch

  const makeProcess = async () => {
    const { Process } = await import('../Process')
    const apiClient = {
      createProcess: jest.fn(),
      getProcess: jest.fn(),
      listProcesses: jest.fn(),
      readProcessLogs: jest.fn(),
      sendProcessStdin: jest.fn(),
      signalProcess: jest.fn(),
      resizeProcess: jest.fn(),
      waitForProcess: jest.fn(),
    }
    const cfg: Configuration = {
      basePath: 'http://sandbox/sb-1',
      baseOptions: {
        headers: {
          Authorization: 'Bearer t',
          'X-Daytona-SDK-Version': '0.0.0-test',
        },
      },
    } as unknown as Configuration

    const process = new Process(cfg, apiClient as never, async () => 'preview-token', 'python')

    return { process, apiClient }
  }

  beforeEach(() => {
    jest.clearAllMocks()
    fetchMock = jest.fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    global.fetch = fetchMock
  })

  afterAll(() => {
    global.fetch = originalFetch
  })

  it('start creates a process handle and preserves raw argv execution', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-1')))

    const handle = await process.start({ argv: ['echo', '$HOME'], timeoutMs: 5000 })

    expect(apiClient.createProcess).toHaveBeenCalledWith({ argv: ['echo', '$HOME'], timeoutMs: 5000 })
    expect(handle.id).toBe('prc-1')
    expect(apiClient.createProcess.mock.calls[0][0]).not.toHaveProperty('keepLogs')
  })

  it('run defaults keepLogs to on_exit_ttl and returns the handle metadata', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2')))
    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ exitCode: 0, reason: 'exited' }))
    apiClient.readProcessLogs.mockResolvedValue(
      createApiResponse({
        frames: [
          { seq: 1, cursor: 'c1', channel: 'stdout', timestamp: 't', data: 'hello\n', encoding: 'text' },
          { seq: 2, cursor: 'c2', channel: 'stderr', timestamp: 't', data: 'oops\n', encoding: 'text' },
        ],
        nextCursor: 'c2',
        truncatedHead: false,
        eof: true,
      }),
    )

    const result = await process.run({ shellCommand: 'echo hello', waitTimeoutMs: 900 })

    expect(apiClient.createProcess).toHaveBeenCalledWith({ shellCommand: 'echo hello', keepLogs: 'on_exit_ttl' })
    expect(apiClient.waitForProcess).toHaveBeenCalledWith('prc-2', 900)
    expect(result.id).toBe('prc-2')
    expect(result.handle.id).toBe('prc-2')
    expect(result.stdout).toBe('hello\n')
    expect(result.stderr).toBe('oops\n')
  })

  it('omits an empty argv so the request carries only the shell command', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2a')))

    await process.start({ argv: [], shellCommand: 'echo hello' })

    expect(apiClient.createProcess).toHaveBeenCalledWith({ shellCommand: 'echo hello' })
    expect(apiClient.createProcess.mock.calls[0][0]).not.toHaveProperty('argv')
  })

  it('omits a whitespace-only shellCommand but preserves a non-blank one verbatim', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2b')))

    await process.start({ argv: ['echo', 'hi'], shellCommand: '   ' })
    expect(apiClient.createProcess).toHaveBeenNthCalledWith(1, { argv: ['echo', 'hi'] })
    expect(apiClient.createProcess.mock.calls[0][0]).not.toHaveProperty('shellCommand')

    await process.start({ shellCommand: '  echo hi  ' })
    expect(apiClient.createProcess).toHaveBeenNthCalledWith(2, { shellCommand: '  echo hi  ' })
  })

  it('validates initial terminal dimensions before creating a pty process', async () => {
    const { process, apiClient } = await makeProcess()

    await expect(process.start({ kind: 'pty', terminal: { cols: 0, rows: 24 } })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    await expect(process.start({ kind: 'pty', terminal: { cols: 80, rows: 24.5 } })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    await expect(process.start({ kind: 'pty', terminal: { cols: Number.NaN, rows: 24 } })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    expect(apiClient.createProcess).not.toHaveBeenCalled()
  })

  it('rejects non-finite timeouts and escalation delays', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2c')))

    await expect(process.start({ shellCommand: 'true', timeoutMs: Number.NaN })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    await expect(process.run({ shellCommand: 'true', waitTimeoutMs: Number.POSITIVE_INFINITY })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    expect(apiClient.createProcess).not.toHaveBeenCalled()

    const handle = await process.start({ shellCommand: 'true' })
    await expect(handle.wait({ timeoutMs: Number.NaN })).rejects.toBeInstanceOf(DaytonaInvalidArgumentError)
    await expect(handle.kill({ escalateAfterMs: Number.NaN })).rejects.toBeInstanceOf(DaytonaInvalidArgumentError)
    await expect(handle.kill({ escalateAfterMs: Number.POSITIVE_INFINITY })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    expect(apiClient.waitForProcess).not.toHaveBeenCalled()
    expect(apiClient.signalProcess).not.toHaveBeenCalled()
  })

  it("rejects run with keepLogs 'none' unless output is consumed by callbacks", async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2d')))
    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ exitCode: 0, reason: 'exited' }))

    await expect(process.run({ shellCommand: 'true', keepLogs: 'none' })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    expect(apiClient.createProcess).not.toHaveBeenCalled()

    await expect(process.start({ shellCommand: 'true', keepLogs: 'none' })).resolves.toBeDefined()
    expect(apiClient.createProcess).toHaveBeenCalledWith({ shellCommand: 'true', keepLogs: 'none' })
  })

  it('reports timedOut on the collected run result', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2e')))
    apiClient.readProcessLogs.mockResolvedValue(
      createApiResponse({ frames: [], nextCursor: 'c0', truncatedHead: false, eof: true }),
    )

    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'exited', exitCode: 0 }))
    await expect(process.run({ shellCommand: 'true' })).resolves.toMatchObject({ timedOut: false })

    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out' }))
    await expect(process.run({ shellCommand: 'sleep 5', waitTimeoutMs: 10 })).resolves.toMatchObject({
      timedOut: true,
    })
  })

  it('separates a timed-out wait from a process killed by its own start timeout', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2e2')))
    apiClient.readProcessLogs.mockResolvedValue(
      createApiResponse({ frames: [], nextCursor: 'c0', truncatedHead: false, eof: true }),
    )

    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out', signal: 'SIGKILL' }))
    await expect(process.run({ shellCommand: 'sleep 60', timeoutMs: 50 })).resolves.toMatchObject({
      timedOut: false,
      signal: 'SIGKILL',
    })

    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out', exitCode: 137 }))
    await expect(process.run({ shellCommand: 'sleep 60', timeoutMs: 50 })).resolves.toMatchObject({
      timedOut: false,
      exitCode: 137,
    })

    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out' }))
    await expect(process.run({ shellCommand: 'sleep 60', waitTimeoutMs: 10 })).resolves.toMatchObject({
      timedOut: true,
    })
  })

  it('keeps timedOut false for a streamed run killed by its own start timeout', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2f2')))
    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out', signal: 'SIGKILL' }))
    fetchMock.mockResolvedValue(
      makeSseResponse([
        'event: log\ndata: {"channel":"stdout","cursor":"c_1","seq":1,"timestamp":"t","data":"tick","encoding":"text"}\n\n',
        'event: eof\ndata: {"cursor":"c_2"}\n\n',
      ]),
    )

    const result = await process.run({
      shellCommand: 'sleep 60',
      timeoutMs: 50,
      onStdout: () => undefined,
    })

    expect(result.timedOut).toBe(false)
    expect(result.signal).toBe('SIGKILL')
    expect(apiClient.waitForProcess).toHaveBeenCalledWith('prc-2f2', undefined)
  })

  it('reports timedOut when the streaming deadline closes the run early', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2f')))
    apiClient.waitForProcess.mockResolvedValue(createApiResponse({ reason: 'timed_out' }))
    fetchMock.mockResolvedValue(
      makeSseResponse(
        [
          'event: log\ndata: {"channel":"stdout","cursor":"c_1","seq":1,"timestamp":"t","data":"tick","encoding":"text"}\n\n',
        ],
        { chunks: ['event: eof\ndata: {"cursor":"c_2"}\n\n'], afterMs: 120 },
      ),
    )

    const chunks: string[] = []
    const result = await process.run({
      shellCommand: 'sleep 5',
      waitTimeoutMs: 25,
      onStdout: (data) => {
        chunks.push(data)
      },
    })

    expect(chunks).toEqual(['tick'])
    expect(result.timedOut).toBe(true)
    expect(apiClient.waitForProcess).toHaveBeenCalledWith('prc-2f', 1)
  })

  it('rejects stdin bytes that are not valid UTF-8 instead of corrupting them', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-2g')))
    apiClient.sendProcessStdin.mockResolvedValue(createApiResponse(undefined))

    const handle = await process.start({ argv: ['cat'], stdin: 'pipe' })

    await expect(handle.stdin(new Uint8Array([0xff, 0xfe, 0x00]))).rejects.toBeInstanceOf(DaytonaInvalidArgumentError)
    expect(apiClient.sendProcessStdin).not.toHaveBeenCalled()
  })

  it('rejects invalid client-side start arguments before any request is sent', async () => {
    const { process, apiClient } = await makeProcess()

    await expect(process.start({})).rejects.toBeInstanceOf(DaytonaInvalidArgumentError)
    await expect(process.start({ argv: ['echo'], shellCommand: 'echo hello' })).rejects.toBeInstanceOf(
      DaytonaInvalidArgumentError,
    )
    expect(apiClient.createProcess).not.toHaveBeenCalled()
  })

  it('lists processes with filters', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.listProcesses.mockResolvedValue(createApiResponse([makeProcessRecord('prc-3')]))

    await expect(
      process.list({ state: 'all', kind: 'exec', sessionId: 'sess-1', name: 'build', pid: 42 }),
    ).resolves.toEqual([makeProcessRecord('prc-3')])
    expect(apiClient.listProcesses).toHaveBeenCalledWith('all', 'exec', 'sess-1', 'build', 42)
  })

  it('streams SSE log, warning, state, and eof events in order', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-5')))
    fetchMock.mockResolvedValue(
      makeSseResponse([
        'event: log\ndata: {"channel":"stdout","cursor":"c_1","seq":1,"timestamp":"2026-07-29T00:00:00.000Z","data":"line 1","encoding":"text"}\n\n',
        'event: warning\ndata: {"cursor":"c_8","message":"frames before the first available cursor were evicted","firstAvailableCursor":"c_8"}\n\n',
        'event: state\ndata: {"id":"prc-5","createdAt":"2026-07-29T00:00:00.000Z","kind":"exec","state":"terminal","reason":"exited","cursor":"c_9"}\n\n',
        'event: eof\ndata: {"cursor":"c_9"}\n\n',
      ]),
    )

    const handle = await process.start({ argv: ['printf', 'line 1'] })
    const events: string[] = []

    for await (const event of handle.streamLogs({ cursor: 'c_0' })) {
      events.push(event.type)
      if (event.type === 'log') {
        expect(event.frame.data).toBe('line 1')
      }
      if (event.type === 'warning') {
        expect(event.firstAvailableCursor).toBe('c_8')
      }
      if (event.type === 'state') {
        expect(event.process.state).toBe('terminal')
        expect(event.cursor).toBe('c_9')
      }
    }

    expect(fetchMock).toHaveBeenCalledWith('http://sandbox/sb-1/processes/prc-5/logs?follow=true&cursor=c_0', {
      method: 'GET',
      headers: {
        Authorization: 'Bearer t',
        'X-Daytona-SDK-Version': '0.0.0-test',
        Accept: 'application/json,text/event-stream',
      },
    })
    expect(events).toEqual(['log', 'warning', 'state', 'eof'])
  })

  it('passes log pagination options through to the generated client', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-6')))
    apiClient.readProcessLogs.mockResolvedValue(createApiResponse({ frames: [], nextCursor: 'c_0', eof: false }))

    const handle = await process.start({ argv: ['true'] })

    await expect(handle.logs({ cursor: 'c_4', limit: 10, encoding: 'base64' })).resolves.toEqual({
      frames: [],
      nextCursor: 'c_0',
      eof: false,
    })
    expect(apiClient.readProcessLogs).toHaveBeenCalledWith('prc-6', 'c_4', 10, 'base64')
  })

  it('defaults kill to SIGTERM without forcing an escalation signal', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-7')))
    apiClient.signalProcess.mockResolvedValue(createApiResponse(undefined))

    const handle = await process.start({ argv: ['sleep', '60'] })
    await handle.kill({ escalateAfterMs: 2000 })

    expect(apiClient.signalProcess).toHaveBeenCalledWith('prc-7', {
      signal: 'SIGTERM',
      escalateAfterMs: 2000,
      escalateTo: undefined,
    })
  })

  it('sends stdin bytes and EOF to the process', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-8')))
    apiClient.sendProcessStdin.mockResolvedValue(createApiResponse(undefined))

    const handle = await process.start({ argv: ['cat'], stdin: 'pipe' })
    await handle.stdin(new TextEncoder().encode('hello'))
    await handle.stdinEof()

    expect(apiClient.sendProcessStdin).toHaveBeenNthCalledWith(1, 'prc-8', { data: 'hello' })
    expect(apiClient.sendProcessStdin).toHaveBeenNthCalledWith(2, 'prc-8', { eof: true })
  })

  it('attaches a terminal socket after verifying the process record', async () => {
    const { process, apiClient } = await makeProcess()
    const socket = { readyState: 1 }
    apiClient.getProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-9', { kind: 'pty' })))
    mockCreateSandboxWebSocket.mockResolvedValue(socket)

    const handle = await process.get('prc-9')
    await expect(handle.attachTerminal()).resolves.toBe(socket)
    expect(mockCreateSandboxWebSocket).toHaveBeenCalledWith(
      'ws://sandbox/sb-1/processes/prc-9/attach',
      {
        Authorization: 'Bearer t',
        'X-Daytona-SDK-Version': '0.0.0-test',
      },
      expect.any(Function),
    )
  })

  it('preserves typed CURSOR_EXPIRED errors from paged log reads', async () => {
    const { process, apiClient } = await makeProcess()
    apiClient.createProcess.mockResolvedValue(createApiResponse(makeProcessRecord('prc-10')))
    apiClient.readProcessLogs.mockRejectedValue(
      new DaytonaCursorExpiredError('cursor expired', 409, undefined, 'CURSOR_EXPIRED', 'DAYTONA_DAEMON'),
    )

    const handle = await process.start({ argv: ['true'] })

    await expect(handle.logs({ cursor: 'c_1' })).rejects.toMatchObject({
      code: 'CURSOR_EXPIRED',
    })
  })
})
