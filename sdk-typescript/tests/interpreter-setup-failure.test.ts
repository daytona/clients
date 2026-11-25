/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// A code run whose kernel could not be set up throws; it never looks like a run.

import { InterpreterService } from '../src/v2/Interpreter'
import { INTERNAL } from '../src/v2/internal'
import { KernelUnavailableError, ServiceUnavailableError } from '../src/v2/errors/DaytonaError'

const INSTALL_FAILURE = 'cannot install ipykernel (is PyPI reachable from this sandbox?): uv pip install: exit 1: network unreachable'

function processWire(id = 'proc-1'): Record<string, unknown> {
  return {
    id, kind: 'code', state: 'running', cwd: '/', stdin: 'closed', keepLogs: 'on_exit_ttl',
    createdAt: '2026-01-01T00:00:00Z', truncatedHead: false, language: 'python',
  }
}

function frame(cursor: string, channel: string, data: string): Record<string, unknown> {
  return { cursor, channel, data, timestamp: 1 }
}

function page(frames: Record<string, unknown>[]): Record<string, unknown> {
  return { frames, nextCursor: String(frames.length), truncatedHead: false, eof: true }
}

const SETUP_FAILED_FRAMES = [
  frame('1', 'setup', 'Preparing the python kernel (first use): installing ipykernel\n'),
  frame('2', 'setup', `code run failed: ${INSTALL_FAILURE}\n`),
]

function service(wait: Record<string, unknown>, frames: Record<string, unknown>[]) {
  const transport = {
    get: jest.fn().mockResolvedValueOnce(wait).mockResolvedValueOnce(page(frames)),
    post: jest.fn().mockResolvedValue(processWire()),
    delete: jest.fn().mockResolvedValue({}),
  }
  return { transport, interpreter: new InterpreterService(INTERNAL, transport as never, 'sbx') }
}

async function rejection(run: Promise<unknown>): Promise<unknown> {
  try {
    await run
  } catch (error) {
    return error
  }
  throw new Error('the run did not throw')
}

describe('interpreter runs whose kernel setup failed', () => {
  it('throw KernelUnavailableError carrying the setup diagnostic', async () => {
    const { interpreter } = service({ reason: 'failed' }, SETUP_FAILED_FRAMES)

    const error = await rejection(interpreter.run('print(1)'))

    expect(error).toBeInstanceOf(KernelUnavailableError)
    expect(error).toBeInstanceOf(ServiceUnavailableError)
    expect(error).toMatchObject({ statusCode: 503, code: 'KERNEL_UNAVAILABLE', source: 'SDK' })
    const message = (error as Error).message
    expect(message).toContain(INSTALL_FAILURE)
    expect(message).toContain('proc-1')
    expect(message).toContain('Preparing the python kernel')
  })

  it('throw without a diagnostic too', async () => {
    const { interpreter } = service({ reason: 'failed' }, [])

    const error = await rejection(interpreter.run('console.log(1)', { language: 'typescript' }))

    expect(error).toBeInstanceOf(KernelUnavailableError)
    expect((error as Error).message).toContain('no setup diagnostic')
  })

  it('throw from an explicit context as well', async () => {
    const { interpreter } = service({ reason: 'failed' }, SETUP_FAILED_FRAMES)

    await expect(interpreter.connectContext('ctx-1', 'python').run('print(1)')).rejects.toBeInstanceOf(KernelUnavailableError)
  })

  it('keep setup narration of a successful run out of the result', async () => {
    const { interpreter } = service({ reason: 'exited', exitCode: 0 }, [
      frame('1', 'setup', 'kernel is gone; restarting it (session state is lost)\n'),
      frame('2', 'stdout', '1\n'),
    ])

    const result = await interpreter.run('print(1)')

    expect(result.process.reason).toBe('exited')
    expect(result.logs.stdout).toBe('1\n')
    expect(result.error).toBeUndefined()
  })

  it('return the partial result of a kernel lost mid-run', async () => {
    const { interpreter } = service({ reason: 'lost' }, [
      frame('1', 'stdout', 'partial\n'),
      frame('2', 'setup', 'kernel died mid-execution: connection closed\n'),
    ])

    const result = await interpreter.run('print(1)')

    expect(result.process.reason).toBe('lost')
    expect(result.logs.stdout).toBe('partial\n')
  })
})
