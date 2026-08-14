// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { collectOutputFromLogs, streamOutputWithCallbacks } from '../process-run-output'
import { base64ToUint8Array } from '../utils/Binary'
import { DaytonaError } from '../errors/DaytonaError'
import type { ProcessHandle } from '../ProcessHandle'
import type { ProcessLogEvent } from '../types/Process'

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

  it('decodes frames that omit their optional data field as empty output', async () => {
    const frames = [
      { seq: 1, cursor: 'c1', channel: 'stdout', timestamp: '', encoding: 'base64' },
      { seq: 2, cursor: 'c2', channel: 'stderr', timestamp: '', data: '', encoding: 'base64' },
      { seq: 3, cursor: 'c3', channel: 'stdout', timestamp: '', encoding: 'text' },
      { seq: 4, cursor: 'c4', channel: 'stdout', timestamp: '', data: b64([0x6f, 0x6b]), encoding: 'base64' },
    ]
    const handle = {
      logs: async () => ({ frames, eof: true, nextCursor: 'c4', truncatedHead: false }),
    } as unknown as ProcessHandle

    const collected = await collectOutputFromLogs(handle)
    expect(collected.stdout).toBe('ok')
    expect(collected.stderr).toBe('')
  })

  it('decodes absent base64 payloads to zero bytes', () => {
    expect(base64ToUint8Array(undefined)).toEqual(new Uint8Array(0))
    expect(base64ToUint8Array('')).toEqual(new Uint8Array(0))
  })
})

describe('run output collection limits', () => {
  it('throws instead of returning silently partial output when the page budget is exhausted', async () => {
    let pages = 0
    const handle = {
      id: 'prc_paged',
      logs: async () => {
        pages++
        return {
          frames: [
            {
              seq: pages,
              cursor: `c${pages}`,
              channel: 'stdout',
              timestamp: '',
              data: Buffer.from('x').toString('base64'),
              encoding: 'base64',
            },
          ],
          eof: false,
          nextCursor: `c${pages}`,
          truncatedHead: false,
        }
      },
    } as unknown as ProcessHandle

    await expect(collectOutputFromLogs(handle)).rejects.toBeInstanceOf(DaytonaError)
    expect(pages).toBe(10_000)
  })

  it('returns the collected output when the cursor stops advancing before eof', async () => {
    let pages = 0
    const handle = {
      id: 'prc_stalled',
      logs: async () => {
        pages++
        return {
          frames: [
            {
              seq: pages,
              cursor: 'c_stall',
              channel: 'stdout',
              timestamp: '',
              data: Buffer.from(`page${pages}\n`).toString('base64'),
              encoding: 'base64',
            },
          ],
          eof: false,
          nextCursor: 'c_stall',
          truncatedHead: false,
        }
      },
    } as unknown as ProcessHandle

    await expect(collectOutputFromLogs(handle)).resolves.toEqual({
      stdout: 'page1\npage2\n',
      stderr: '',
      timedOut: false,
    })
    expect(pages).toBe(2)
  })

  it('treats a page that reports no next cursor as drained', async () => {
    let pages = 0
    const handle = {
      id: 'prc_no_cursor',
      logs: async () => {
        pages++
        return {
          frames: [
            {
              seq: pages,
              cursor: `c${pages}`,
              channel: 'stderr',
              timestamp: '',
              data: Buffer.from('warn\n').toString('base64'),
              encoding: 'base64',
            },
          ],
          eof: false,
          truncatedHead: false,
        }
      },
    } as unknown as ProcessHandle

    await expect(collectOutputFromLogs(handle)).resolves.toMatchObject({ stdout: '', stderr: 'warn\n' })
    expect(pages).toBe(1)
  })

  it('cancels the deadline timer after every streaming race', async () => {
    const clearTimeoutSpy = jest.spyOn(global, 'clearTimeout')
    const events: ProcessLogEvent[] = [
      {
        type: 'log',
        cursor: 'c1',
        frame: { seq: 1, cursor: 'c1', channel: 'stdout', timestamp: '', data: 'hi', encoding: 'text' },
      },
      { type: 'eof', cursor: 'c1' },
    ]
    const handle = {
      id: 'prc_stream',
      streamLogs: async function* () {
        for (const event of events) {
          yield event
        }
      },
    } as unknown as ProcessHandle

    const chunks: string[] = []
    const collected = await streamOutputWithCallbacks(
      handle,
      (data) => {
        chunks.push(data)
      },
      undefined,
      60_000,
    )

    expect(chunks).toEqual(['hi'])
    expect(collected.timedOut).toBe(false)
    expect(clearTimeoutSpy).toHaveBeenCalledTimes(events.length)
    clearTimeoutSpy.mockRestore()
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
