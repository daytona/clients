// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

/*
 * The daemon prefix-muxes session logs with literal control bytes
 * (\x01\x01\x01 = stdout, \x02\x02\x02 = stderr). Pinning that wire format
 * means matching those bytes, so no-control-regex does not apply here.
 */
/* eslint-disable no-control-regex */

import { randomUUID } from 'node:crypto'

import type { Command } from '@daytona/toolbox-api-client'
import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import { DaytonaProcessNotFoundError, DaytonaSessionEndedError } from '../../errors/DaytonaError'
import {
  GOLDEN_TIMEOUT_MS,
  collectAsyncError,
  createGoldenSandbox,
  createGoldenDaytona,
  deleteGoldenSandbox,
  expectDaemonErrorShape,
  waitForValue,
} from './helpers'

jest.setTimeout(GOLDEN_TIMEOUT_MS)

if (!process.env.DAYTONA_API_KEY) {
  throw new Error('DAYTONA_API_KEY environment variable is required for golden contract tests')
}

function createSessionId(prefix: string): string {
  return `golden-${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}`
}

describe('legacy session golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'session')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('createSession, getSession, listSessions, and deleteSession preserve the current shapes', async () => {
    const sessionId = createSessionId('crud')

    await sandbox.process.createSession(sessionId)
    const created = await sandbox.process.getSession(sessionId)
    const listed = await sandbox.process.listSessions()

    expect(created).toEqual({
      sessionId,
      commands: [],
    })
    expect(listed).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          sessionId,
          commands: expect.any(Array),
        }),
      ]),
    )

    await sandbox.process.deleteSession(sessionId)

    const deletedError = await collectAsyncError(async () => {
      await sandbox.process.getSession(sessionId)
    })

    expect(deletedError).toBeInstanceOf(DaytonaProcessNotFoundError)
    expectDaemonErrorShape(deletedError, {
      message: 'session not found',
      statusCode: 404,
      code: 'PROCESS_NOT_FOUND',
      source: 'DAYTONA_DAEMON',
    })
  })

  test('async execution returns null output fields, reports null exitCode while running, and sets exitCode after completion', async () => {
    const sessionId = createSessionId('async')
    await sandbox.process.createSession(sessionId)

    const commandText = 'sleep 2; echo done'
    const execution = await sandbox.process.executeSessionCommand(sessionId, {
      command: commandText,
      runAsync: true,
    })

    expect(execution).toEqual({
      cmdId: expect.any(String),
      output: null,
      stdout: '',
      stderr: '',
      exitCode: null,
    })

    const whileRunning = await sandbox.process.getSessionCommand(sessionId, execution.cmdId)
    expect(whileRunning).toEqual({
      id: execution.cmdId,
      command: commandText,
    })

    const afterCompletion = await waitForValue<Command>(
      async () => await sandbox.process.getSessionCommand(sessionId, execution.cmdId),
      (command) => typeof command.exitCode === 'number',
    )

    expect(afterCompletion).toEqual({
      id: execution.cmdId,
      command: commandText,
      exitCode: 0,
    })

    const session = await sandbox.process.getSession(sessionId)
    expect(session).toEqual({
      sessionId,
      commands: [
        {
          id: execution.cmdId,
          command: commandText,
          exitCode: 0,
        },
      ],
    })
  })

  test('sync execution returns combined output, exit code, and empty-string stdout/stderr in the SDK wrapper', async () => {
    const sessionId = createSessionId('sync')
    await sandbox.process.createSession(sessionId)

    const result = await sandbox.process.executeSessionCommand(sessionId, {
      command: 'echo sync1; echo synce >&2',
    })

    // Per-channel content, the prefix-mux format and the exit code are exact.
    // The stdout-vs-stderr INTERLEAVING is not asserted: the daemon labels the
    // two streams from independent readers, so back-to-back writes race (proved
    // by running this command 10x against an unmodified daemon: 4 differed).
    expect(result.cmdId).toEqual(expect.any(String))
    expect(result.exitCode).toBe(0)
    expect(result.stdout).toBe('sync1\n')
    expect(result.stderr).toBe('synce\n')
    expect(result.output).toContain('\u0001\u0001\u0001sync1\n')
    expect(result.output).toContain('\u0002\u0002\u0002synce\n')
    expect(result.output).toMatch(/^(\u0001\u0001\u0001sync1\n|\u0002\u0002\u0002synce\n){2}$/)
  })

  test('cwd and env request fields are currently ignored by the daemon for session commands', async () => {
    const sessionId = createSessionId('ignored-request-fields')
    await sandbox.process.createSession(sessionId)

    const request = {
      command: 'pwd; echo $A',
      cwd: '/tmp',
      env: { A: '1' },
    }

    const result = await sandbox.process.executeSessionCommand(sessionId, request)

    expect(result).toEqual({
      cmdId: expect.any(String),
      output: '\u0001\u0001\u0001/home/daytona\n\u0001\u0001\u0001\n',
      stdout: '/home/daytona\n\n',
      stderr: '',
      exitCode: 0,
    })
  })

  test('plain log retrieval preserves the daemon split form and prefix-muxed combined output', async () => {
    const sessionId = createSessionId('logs')
    await sandbox.process.createSession(sessionId)

    const commandText = 'for i in 1 2 3; do echo out$i; echo err$i >&2; sleep 0.3; done'
    const execution = await sandbox.process.executeSessionCommand(sessionId, {
      command: commandText,
      runAsync: true,
    })

    await waitForValue<Command>(
      async () => await sandbox.process.getSessionCommand(sessionId, execution.cmdId),
      (command) => typeof command.exitCode === 'number',
    )

    const logs = await sandbox.process.getSessionCommandLogs(sessionId, execution.cmdId)

    // Per-channel content and the prefix-muxed wire format are deterministic and
    // pinned exactly. The INTERLEAVING of stdout against stderr is not: the daemon
    // labels the two streams from independent readers, and running this exact
    // command 10 times against an unmodified daemon produced a different
    // out/err order in 4 of them. Asserting a fixed interleaving would pin a race.
    expect(logs.stdout).toBe('out1\nout2\nout3\n')
    expect(logs.stderr).toBe('err1\nerr2\nerr3\n')

    expect(logs.output).toMatch(/^(\u0001\u0001\u0001out[123]\n|\u0002\u0002\u0002err[123]\n)+$/)
    for (const line of ['out1', 'out2', 'out3']) {
      expect(logs.output).toContain(`\u0001\u0001\u0001${line}\n`)
    }
    for (const line of ['err1', 'err2', 'err3']) {
      expect(logs.output).toContain(`\u0002\u0002\u0002${line}\n`)
    }

    const stdoutOrder = [...logs.output.matchAll(/\u0001\u0001\u0001(out\d)/g)].map((m) => m[1])
    const stderrOrder = [...logs.output.matchAll(/\u0002\u0002\u0002(err\d)/g)].map((m) => m[1])
    expect(stdoutOrder).toEqual(['out1', 'out2', 'out3'])
    expect(stderrOrder).toEqual(['err1', 'err2', 'err3'])
  })

  test('follow-mode log streaming demuxes stdout and stderr without losing order', async () => {
    const sessionId = createSessionId('follow')
    await sandbox.process.createSession(sessionId)

    const execution = await sandbox.process.executeSessionCommand(sessionId, {
      command: 'for i in 1 2 3; do echo out$i; echo err$i >&2; sleep 0.3; done',
      runAsync: true,
    })

    const stdoutChunks: string[] = []
    const stderrChunks: string[] = []

    await sandbox.process.getSessionCommandLogs(
      sessionId,
      execution.cmdId,
      (chunk) => {
        stdoutChunks.push(chunk)
      },
      (chunk) => {
        stderrChunks.push(chunk)
      },
    )

    expect(stdoutChunks).toEqual(['out1\n', 'out2\n', 'out3\n'])
    expect(stderrChunks).toEqual(['err1\n', 'err2\n', 'err3\n'])
  })

  test('sendSessionCommandInput uses the data field and cat echoes the input back', async () => {
    const sessionId = createSessionId('stdin')
    await sandbox.process.createSession(sessionId)

    const execution = await sandbox.process.executeSessionCommand(sessionId, {
      command: 'cat',
      runAsync: true,
    })

    await new Promise((resolve) => setTimeout(resolve, 1000))
    await sandbox.process.sendSessionCommandInput(sessionId, execution.cmdId, 'hello-stdin\n')
    await new Promise((resolve) => setTimeout(resolve, 1000))

    const logs = await sandbox.process.getSessionCommandLogs(sessionId, execution.cmdId)
    expect(logs.stdout).toContain('hello-stdin\n')
  })

  test('a command that exits the shell ends the session and the next command fails with SESSION_ENDED', async () => {
    const sessionId = createSessionId('ended')
    await sandbox.process.createSession(sessionId)

    const firstResult = await sandbox.process.executeSessionCommand(sessionId, {
      command: 'echo goodbye; exit 7',
      runAsync: true,
    })

    expect(firstResult.output).toBeNull()
    expect(firstResult.exitCode).toBeNull()

    await new Promise((resolve) => setTimeout(resolve, 1500))

    const nextCommandError = await collectAsyncError(async () => {
      await sandbox.process.executeSessionCommand(sessionId, { command: 'echo should-not-run' })
    })

    expect(nextCommandError).toBeInstanceOf(DaytonaSessionEndedError)
    expectDaemonErrorShape(nextCommandError, {
      message: 'session process has exited',
      statusCode: 410,
      code: 'SESSION_ENDED',
      source: 'DAYTONA_DAEMON',
    })
  })

  test('getEntrypointSession and getEntrypointLogs preserve their current response shapes', async () => {
    const session = await sandbox.process.getEntrypointSession()
    const logs = await sandbox.process.getEntrypointLogs()

    expect(session).toEqual({
      sessionId: 'entrypoint',
      commands: [
        {
          id: 'entrypoint_command',
          command: "'sleep' 'infinity'",
        },
      ],
    })
    expect(logs).toEqual({
      output: expect.any(String),
      stdout: expect.any(String),
      stderr: expect.any(String),
    })
  })
})
