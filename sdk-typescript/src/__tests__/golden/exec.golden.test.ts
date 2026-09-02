// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { ExecuteResponse } from '../../types/ExecuteResponse'
import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import { DaytonaInvalidArgumentError, DaytonaProcessExecutionTimeoutError } from '../../errors/DaytonaError'
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

describe('process.executeCommand golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'exec')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('combines stdout and stderr in emission order and preserves exit code', async () => {
    const result = await sandbox.process.executeCommand('echo hello; echo err >&2; exit 3')

    expect(result).toEqual({
      exitCode: 3,
      result: 'hello\nerr\n',
      artifacts: {
        stdout: 'hello\nerr\n',
      },
    })
  })

  test('honors cwd and envs', async () => {
    const result = await sandbox.process.executeCommand('pwd; echo $FOO', '/tmp', { FOO: 'bar' })

    expect(result.exitCode).toBe(0)
    expect(result.result).toBe('/tmp\nbar\n')
  })

  test('runs inside the legacy-resolved shell (zsh, then bash, then sh) and expands HOME', async () => {
    // The daemon's legacy discovery order is zsh -> bash -> sh. Which one wins is a
    // property of the image, so the expected shell is derived from the same sandbox
    // instead of hardcoded; what is pinned is that executeCommand runs inside THAT
    // shell (so $HOME expands rather than staying literal).
    const discovery = await sandbox.process.executeCommand('command -v zsh || command -v bash || command -v sh')
    const expectedShell = discovery.result.trim()
    expect(expectedShell).toMatch(/\/(zsh|bash|sh)$/)

    const shellExe = await sandbox.process.executeCommand('readlink /proc/$$/exe')
    const shellArg0 = await sandbox.process.executeCommand('echo $0')
    const home = await sandbox.process.executeCommand('echo $HOME')
    const homeDir = await sandbox.process.executeCommand('cd ~ && pwd')

    // /proc/$$/exe is the real interpreter and is exact. $0 is shell-specific
    // (zsh reports the full path, bash reports the bare name), so it is compared
    // by basename.
    expect(shellExe.result).toBe(`${expectedShell}\n`)
    expect(shellArg0.result.trim().replace(/^.*\//, '')).toBe(expectedShell.replace(/^.*\//, ''))
    expect(home.result).toBe(homeDir.result)
    expect(home.result.trim()).toMatch(/^\//)
  })

  test('returns shell command-not-found output and exit code 127 for a missing binary', async () => {
    const result = await sandbox.process.executeCommand('definitely-not-a-binary-xyz')

    // Exit code 127 is the contract; the exact wording is the shell's, and the
    // resolved shell depends on the image (zsh on the full image, bash on slim).
    expect(result.exitCode).toBe(127)
    expect(result.result).toContain('definitely-not-a-binary-xyz')
    expect(result.result).toMatch(/(command not found|not found)/)
  })

  test('currently does not enforce a default SDK timeout, while short commands still pass', async () => {
    const shortCommand = await sandbox.process.executeCommand('echo short-command')
    const longCommand = await sandbox.process.executeCommand('echo start; sleep 15; echo end')

    expect(shortCommand.exitCode).toBe(0)
    expect(shortCommand.result).toBe('short-command\n')
    expect(longCommand.exitCode).toBe(0)
    expect(longCommand.result).toBe('start\nend\n')
  })

  test('honors an explicit timeout override', async () => {
    const timeoutError = await collectAsyncError(async () => {
      await sandbox.process.executeCommand('echo start; sleep 10; echo end', undefined, undefined, 2)
    })

    expect(timeoutError).toBeInstanceOf(DaytonaProcessExecutionTimeoutError)
    expectDaemonErrorShape(timeoutError, {
      message: 'command execution timeout',
      statusCode: 408,
      code: 'PROCESS_EXECUTION_TIMEOUT',
      source: 'DAYTONA_DAEMON',
    })
  })

  test('rejects an empty command with the current live error contract', async () => {
    const error = await collectAsyncError(async () => {
      await sandbox.process.executeCommand('')
    })

    expectDaemonErrorShape(error, {
      message: /invalid request body/i,
      statusCode: 400,
      code: 'INVALID_REQUEST_BODY',
      source: 'DAYTONA_DAEMON',
    })
  })

  test('rejects a missing command with the current live error contract', async () => {
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
      source: 'DAYTONA_DAEMON',
    })
  })
})
