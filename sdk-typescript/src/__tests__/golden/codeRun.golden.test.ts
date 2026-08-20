// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import { DaytonaBadRequestError } from '../../errors/DaytonaError'
import {
  GOLDEN_TIMEOUT_MS,
  collectAsyncError,
  createGoldenSandbox,
  createGoldenDaytona,
  deleteGoldenSandbox,
  expectDaemonErrorShape,
  withProcessLanguage,
} from './helpers'

jest.setTimeout(GOLDEN_TIMEOUT_MS)

if (!process.env.DAYTONA_API_KEY) {
  throw new Error('DAYTONA_API_KEY environment variable is required for golden contract tests')
}

describe('process.codeRun golden contract', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    daytona = createGoldenDaytona()
    sandbox = await createGoldenSandbox(daytona, 'coderun')
  })

  afterAll(async () => {
    await deleteGoldenSandbox(daytona, sandbox)
  })

  test('runs python code and preserves the exact result shape', async () => {
    const result = await withProcessLanguage(sandbox, 'python', async () => await sandbox.process.codeRun('print(1+1)'))

    expect(result).toEqual({
      exitCode: 0,
      result: '2\n',
      artifacts: {
        stdout: '2\n',
        charts: [],
      },
    })
  })

  test('runs javascript code', async () => {
    const result = await withProcessLanguage(
      sandbox,
      'javascript',
      async () => await sandbox.process.codeRun('console.log(41 + 1)'),
    )

    expect(result.exitCode).toBe(0)
    expect(result.result).toBe('42\n')
    expect(result.artifacts.stdout).toBe('42\n')
  })

  test('runs typescript code and preserves npm notices in the combined result', async () => {
    const result = await withProcessLanguage(
      sandbox,
      'typescript',
      async () => await sandbox.process.codeRun('const x: number = 5; console.log(x)'),
    )

    expect(result.exitCode).toBe(0)
    expect(result.result).toMatch(/^5\n(?:npm notice[\s\S]*)?$/)
    expect(result.artifacts.stdout).toBe(result.result)
  })

  test('passes argv and envs through to code-run', async () => {
    const result = await withProcessLanguage(
      sandbox,
      'python',
      async () =>
        await sandbox.process.codeRun('import sys, os; print(sys.argv[1:], os.environ.get("Z"))', {
          argv: ['a', 'b'],
          env: { Z: 'z' },
        }),
    )

    expect(result.exitCode).toBe(0)
    expect(result.result).toBe("['a', 'b'] z\n")
  })

  test('surfaces SystemExit codes and stderr in result', async () => {
    const result = await withProcessLanguage(
      sandbox,
      'python',
      async () => await sandbox.process.codeRun('import sys; sys.stderr.write("E\\n"); raise SystemExit(4)'),
    )

    expect(result.exitCode).toBe(4)
    expect(result.result).toBe('E\n')
    expect(result.artifacts.stdout).toBe('E\n')
  })

  test('returns matplotlib chart artifacts with the current parsed shape', async () => {
    const result = await withProcessLanguage(
      sandbox,
      'python',
      async () =>
        await sandbox.process.codeRun(
          'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()',
        ),
    )

    expect(result.exitCode).toBe(0)
    expect(result.result).toBe('')
    expect(result.artifacts.stdout).toBe('')
    expect(result.artifacts.charts).toHaveLength(1)
    expect(result.artifacts.charts[0]).toEqual(
      expect.objectContaining({
        type: 'line',
        png: expect.stringMatching(/^iVBORw0KGgo/),
        x_ticks: expect.any(Array),
        elements: expect.any(Array),
      }),
    )
  })

  test('surfaces daemon BAD_REQUEST for unsupported languages', async () => {
    const error = await collectAsyncError(async () => {
      await withProcessLanguage(sandbox, 'cobol', async () => await sandbox.process.codeRun('print(1)'))
    })

    expect(error).toBeInstanceOf(DaytonaBadRequestError)
    expectDaemonErrorShape(error, {
      message: 'bad request: unsupported language: cobol',
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'DAYTONA_DAEMON',
    })
  })
})
