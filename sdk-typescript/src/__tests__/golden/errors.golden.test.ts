// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from 'node:crypto'

import type { ExecuteResponse } from '../../types/ExecuteResponse'
import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import {
  DaytonaBadRequestError,
  DaytonaGoneError,
  DaytonaInvalidArgumentError,
  DaytonaNotFoundError,
  DaytonaProcessExecutionTimeoutError,
  DaytonaProcessNotFoundError,
  DaytonaSessionEndedError,
  DaytonaTimeoutError,
  SOURCE_DAEMON,
} from '../../errors/DaytonaError'
import {
  GOLDEN_TIMEOUT_MS,
  callWithUntypedArgs,
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

function createSessionId(prefix: string): string {
  return `golden-${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}`
}

describe('legacy process error golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'errors')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('process execution timeout errors stay typed and keep daemon metadata', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.process.executeCommand('sleep 10', undefined, undefined, 2)
    })

    expect(error).toBeInstanceOf(DaytonaProcessExecutionTimeoutError)
    expect(error).toBeInstanceOf(DaytonaTimeoutError)
    expectDaemonErrorShape(error, {
      message: 'command execution timeout',
      statusCode: 408,
      code: 'PROCESS_EXECUTION_TIMEOUT',
      source: SOURCE_DAEMON,
    })
  })

  test('session-not-found errors stay typed and keep daemon metadata', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.process.getSession(`missing-${randomUUID()}`)
    })

    expect(error).toBeInstanceOf(DaytonaProcessNotFoundError)
    expect(error).toBeInstanceOf(DaytonaNotFoundError)
    expectDaemonErrorShape(error, {
      message: 'session not found',
      statusCode: 404,
      code: 'PROCESS_NOT_FOUND',
      source: SOURCE_DAEMON,
    })
  })

  test('session-ended errors stay typed and keep daemon metadata', async () => {
    const sessionId = createSessionId('session-ended-error')
    await sandbox.process.createSession(sessionId)
    const first = await sandbox.process.executeSessionCommand(sessionId, { command: 'exit 7', runAsync: true })
    await new Promise((resolve) => setTimeout(resolve, 1500))
    expect(first.exitCode).toBeNull()

    const error = await collectAsyncError(async () => {
      await sandbox.process.executeSessionCommand(sessionId, { command: 'echo nope' })
    })

    expect(error).toBeInstanceOf(DaytonaSessionEndedError)
    expect(error).toBeInstanceOf(DaytonaGoneError)
    expectDaemonErrorShape(error, {
      message: 'session process has exited',
      statusCode: 410,
      code: 'SESSION_ENDED',
      source: SOURCE_DAEMON,
    })
  })

  test('server-side validation errors stay typed and keep daemon metadata', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.process.executeCommand('')
    })

    expect(error).toBeInstanceOf(DaytonaBadRequestError)
    expectDaemonErrorShape(error, {
      message: /invalid request body/i,
      statusCode: 400,
      code: 'INVALID_REQUEST_BODY',
      source: SOURCE_DAEMON,
    })
  })

  test('client-side interpreter validation errors have no server metadata', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.codeInterpreter.runCode('   ')
    })

    expect(error).toBeInstanceOf(DaytonaInvalidArgumentError)
    const invalidArgumentError = error as DaytonaInvalidArgumentError
    expect(invalidArgumentError.message).toBe('Code is required for execution')
    expect(invalidArgumentError.statusCode).toBeUndefined()
    expect(invalidArgumentError.code).toBeUndefined()
    expect(invalidArgumentError.source).toBeUndefined()
  })

  test('missing executeCommand arguments preserve the current client-vs-server split', async () => {
    const error = await collectAsyncError(async () => {
      await callWithUntypedArgs<ExecuteResponse>(sandbox.process.executeCommand, sandbox.process, [])
    })

    if (error instanceof DaytonaInvalidArgumentError) {
      expect(error.statusCode).toBeUndefined()
      expect(error.code).toBeUndefined()
      expect(error.source).toBeUndefined()
      return
    }

    expectDaemonErrorShape(error, {
      message: /invalid request body/i,
      statusCode: 400,
      code: 'INVALID_REQUEST_BODY',
      source: SOURCE_DAEMON,
    })
  })
})
