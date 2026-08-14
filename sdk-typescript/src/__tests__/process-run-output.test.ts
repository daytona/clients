// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { collectOutputFromLogs } from '../process-run-output'
import type { ProcessHandle } from '../ProcessHandle'

function b64(bytes: number[]): string {
  return Buffer.from(bytes).toString('base64')
}

describe('run output UTF-8 reassembly', () => {
  it('reassembles a multibyte codepoint split across base64 frames', async () => {
    const frames = [
      { seq: 1, cursor: 'c1', channel: 'stdout', timestamp: '', data: b64([0xf0, 0x9f]), encoding: 'base64' },
      {
        seq: 2,
        cursor: 'c2',
        channel: 'stdout',
        timestamp: '',
        data: b64([0x98, 0x80, 0x20, 0x6f, 0x6b, 0x0a]),
        encoding: 'base64',
      },
      { seq: 3, cursor: 'c3', channel: 'stderr', timestamp: '', data: b64([0xe2, 0x9c]), encoding: 'base64' },
      { seq: 4, cursor: 'c4', channel: 'stderr', timestamp: '', data: b64([0x85, 0x0a]), encoding: 'base64' },
    ]
    const handle = {
      logs: async () => ({ frames, eof: true, nextCursor: 'c4', truncatedHead: false }),
    } as unknown as ProcessHandle

    const collected = await collectOutputFromLogs(handle)
    expect(collected.stdout).toBe('\u{1F600} ok\n')
    expect(collected.stderr).toBe('\u2705\n')
    expect(collected.stdout).not.toContain('\uFFFD')
  })

  it('flushes dangling trailing bytes as replacement at eof', async () => {
    const frames = [
      { seq: 1, cursor: 'c1', channel: 'stdout', timestamp: '', data: b64([0x6f, 0x6b, 0xf0]), encoding: 'base64' },
    ]
    const handle = {
      logs: async () => ({ frames, eof: true, nextCursor: 'c1', truncatedHead: false }),
    } as unknown as ProcessHandle

    const collected = await collectOutputFromLogs(handle)
    expect(collected.stdout).toBe('ok\uFFFD')
  })
})

describe('ProcessHandle.output()', () => {
  it('combines collected logs with exit metadata from the record', async () => {
    const { ProcessHandle } = await import('../ProcessHandle')
    const frames = [
      {
        seq: 1,
        cursor: 'c1',
        channel: 'stdout',
        timestamp: '',
        data: Buffer.from('ok\n').toString('base64'),
        encoding: 'base64',
      },
    ]
    const operations = {
      getProcess: async () => ({ id: 'prc_x', exitCode: 7, signal: undefined, reason: 'exited' }),
      getLogs: async () => ({ frames, eof: true, nextCursor: 'c1', truncatedHead: false }),
    }
    const handle = new ProcessHandle('prc_x', operations as never)
    const output = await handle.output()
    expect(output).toEqual({ stdout: 'ok\n', stderr: '', exitCode: 7, signal: undefined, reason: 'exited' })
  })
})

describe('Process.connect()', () => {
  it('delegates to get() and returns the same handle', async () => {
    const { Process } = await import('../Process')
    const fake = Object.create(Process.prototype)
    const handle = { id: 'prc_x' }
    fake.get = jest.fn(async () => handle)
    await expect(fake.connect('prc_x')).resolves.toBe(handle)
    expect(fake.get).toHaveBeenCalledWith('prc_x')
  })
})
