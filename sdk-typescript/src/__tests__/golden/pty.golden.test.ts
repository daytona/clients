// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from 'node:crypto'

import { Daytona } from '../../Daytona'
import { PtyHandle } from '../../PtyHandle'
import { Sandbox } from '../../Sandbox'
import { DaytonaProcessNotFoundError } from '../../errors/DaytonaError'
import {
  GOLDEN_TIMEOUT_MS,
  collectAsyncError,
  createGoldenSandbox,
  createGoldenDaytona,
  deleteGoldenSandbox,
  expectDaemonErrorShape,
} from './helpers'

jest.setTimeout(GOLDEN_TIMEOUT_MS)

if (!process.env.DAYTONA_API_KEY) {
  throw new Error('DAYTONA_API_KEY environment variable is required for golden contract tests')
}

function createPtyId(prefix: string): string {
  return `golden-${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}`
}

describe('legacy PTY golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'pty')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('createPty, listPtySessions, and getPtySessionInfo preserve the current shape', async () => {
    const sessionId = createPtyId('shape')
    const chunks: string[] = []
    const handle = await sandbox.process.createPty({
      id: sessionId,
      cols: 80,
      rows: 24,
      onData: async (data) => {
        chunks.push(new TextDecoder().decode(data))
      },
    })

    try {
      expect(handle).toBeInstanceOf(PtyHandle)
      expect(handle.sessionId).toBe(sessionId)
      expect(handle.isConnected()).toBe(true)

      const sessions = await sandbox.process.listPtySessions()
      expect(sessions).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: sessionId,
            cwd: '/home/daytona',
            cols: 80,
            rows: 24,
            active: true,
            lazyStart: false,
            envs: expect.objectContaining({ TERM: 'xterm-256color' }),
          }),
        ]),
      )

      const info = await sandbox.process.getPtySessionInfo(sessionId)
      expect(info).toEqual({
        id: sessionId,
        cwd: '/home/daytona',
        envs: expect.objectContaining({ TERM: 'xterm-256color' }),
        cols: 80,
        rows: 24,
        createdAt: expect.any(String),
        active: true,
        lazyStart: false,
      })
      expect(chunks.join('')).toBe('')
    } finally {
      await handle.disconnect()
    }
  })

  test('connectPty, sendInput, and resize preserve the current behavior', async () => {
    const sessionId = createPtyId('interactive')
    const createSideChunks: string[] = []
    const createHandle = await sandbox.process.createPty({
      id: sessionId,
      cols: 80,
      rows: 24,
      onData: async (data) => {
        createSideChunks.push(new TextDecoder().decode(data))
      },
    })

    const connectSideChunks: string[] = []
    const connectedHandle = await sandbox.process.connectPty(sessionId, {
      onData: async (data) => {
        connectSideChunks.push(new TextDecoder().decode(data))
      },
    })

    try {
      await connectedHandle.sendInput('printf "pty-output\\n"\n')
      await new Promise((resolve) => setTimeout(resolve, 1500))

      expect(connectSideChunks.join('')).toContain('pty-output')

      const resized = await connectedHandle.resize(100, 30)
      expect(resized.cols).toBe(100)
      expect(resized.rows).toBe(30)

      const info = await sandbox.process.getPtySessionInfo(sessionId)
      expect(info.cols).toBe(100)
      expect(info.rows).toBe(30)
      expect(createSideChunks.join('')).toContain('printf "pty-output\\n"')
    } finally {
      await connectedHandle.disconnect()
      await createHandle.kill().catch(() => undefined)
      await createHandle.disconnect()
    }
  })

  test('kill terminates the PTY and wait returns the current exit semantics', async () => {
    const sessionId = createPtyId('kill')
    const handle = await sandbox.process.createPty({
      id: sessionId,
      onData: async () => undefined,
    })

    try {
      await handle.sendInput('sleep 30\n')
      await new Promise((resolve) => setTimeout(resolve, 1000))
      await handle.kill()

      const result = await handle.wait()
      expect(result).toEqual({
        exitCode: expect.any(Number),
        error: expect.any(String),
      })
    } finally {
      await handle.disconnect()
    }
  })

  test('getting an unknown PTY id returns PROCESS_NOT_FOUND', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.process.getPtySessionInfo('nope')
    })

    expect(error).toBeInstanceOf(DaytonaProcessNotFoundError)
    expectDaemonErrorShape(error, {
      message: 'PTY session not found',
      statusCode: 404,
      code: 'PROCESS_NOT_FOUND',
      source: 'DAYTONA_DAEMON',
    })
  })
})
