// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import { GOLDEN_TIMEOUT_MS, createGoldenSandbox, createGoldenDaytona, deleteGoldenSandbox } from './helpers'

jest.setTimeout(GOLDEN_TIMEOUT_MS)

if (!process.env.DAYTONA_API_KEY) {
  throw new Error('DAYTONA_API_KEY environment variable is required for golden contract tests')
}

describe('codeInterpreter golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'interpreter')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('createContext preserves the current shape', async () => {
    const context = await sandbox.codeInterpreter.createContext()

    try {
      expect(context).toEqual({
        id: expect.any(String),
        cwd: '/home/daytona',
        createdAt: expect.any(String),
        active: true,
        language: 'python',
      })
    } finally {
      await sandbox.codeInterpreter.deleteContext(context)
    }
  })

  test('runCode without an explicit context returns stdout/stderr/error in the current shape', async () => {
    const result = await sandbox.codeInterpreter.runCode('print("interpreter-hello")')

    expect(result).toEqual({
      stdout: 'interpreter-hello\n',
      stderr: '',
    })
  })

  test('runCode with a custom context preserves state across calls', async () => {
    const context = await sandbox.codeInterpreter.createContext()

    try {
      const first = await sandbox.codeInterpreter.runCode('x = 10', { context })
      const second = await sandbox.codeInterpreter.runCode('print(x)', { context })

      expect(first).toEqual({ stdout: '', stderr: '' })
      expect(second).toEqual({ stdout: '10\n', stderr: '' })
    } finally {
      await sandbox.codeInterpreter.deleteContext(context)
    }
  })

  test('listContexts includes created contexts and deleteContext removes them', async () => {
    const context = await sandbox.codeInterpreter.createContext()

    const listedBeforeDelete = await sandbox.codeInterpreter.listContexts()
    expect(listedBeforeDelete).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: context.id,
          cwd: '/home/daytona',
          active: true,
          language: 'python',
        }),
      ]),
    )

    await sandbox.codeInterpreter.deleteContext(context)

    const listedAfterDelete = await sandbox.codeInterpreter.listContexts()
    expect(listedAfterDelete.some((item) => item.id === context.id)).toBe(false)
  })

  test('captures stderr plus structured execution errors', async () => {
    const result = await sandbox.codeInterpreter.runCode(
      'import sys\nsys.stderr.write("ci-stderr\\n")\nraise ValueError("boom")',
    )

    expect(result).toEqual({
      stdout: '',
      stderr: 'ci-stderr\n',
      error: {
        name: 'ValueError',
        value: 'boom',
        traceback: expect.stringContaining('ValueError: boom'),
      },
    })
  })

  test('enforces short timeout values on interpreter runs, returning empty output', async () => {
    // Verified against production: a 1s timeout on a 2s sleep returns after ~1s
    // (measured 1462ms end to end, dominated by network round trip) with empty
    // output. The interrupted run must NOT be allowed to finish its sleep.
    const startedAt = Date.now()
    const result = await sandbox.codeInterpreter.runCode('import time\ntime.sleep(2)', { timeout: 1 })
    const elapsed = Date.now() - startedAt

    expect(elapsed).toBeLessThan(2000)
    expect(result).toEqual({ stdout: '', stderr: '' })
  })
})
