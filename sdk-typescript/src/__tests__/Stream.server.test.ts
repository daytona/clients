// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import type { AddressInfo } from 'net'
import WebSocket from 'isomorphic-ws'
import { WebSocketServer } from 'ws'

import { STDERR_PREFIX_BYTES, STDOUT_PREFIX_BYTES } from '../Process'
import { stdDemuxStream } from '../utils/Stream'

const STDOUT = Buffer.from(STDOUT_PREFIX_BYTES)
const STDERR = Buffer.from(STDERR_PREFIX_BYTES)
const frame = (...parts: Array<Buffer | string>) => Buffer.concat(parts.map((p) => Buffer.from(p)))

/**
 * Real-socket regression for log demultiplexing: the daemon forwards log-file reads
 * as-is, so a WebSocket message can end right after a complete stream marker.
 */
function startLogServer(frames: Buffer[]): Promise<{ url: string; close: () => Promise<void> }> {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  server.on('connection', (socket: WebSocket) => {
    for (const f of frames) socket.send(f)
    socket.close(1000)
  })
  return new Promise((resolve) => {
    server.on('listening', () => {
      const { port } = server.address() as AddressInfo
      resolve({
        url: `ws://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(() => done())),
      })
    })
  })
}

async function demux(frames: Buffer[]): Promise<[string, string]> {
  const server = await startLogServer(frames)
  try {
    let stdout = ''
    let stderr = ''
    await stdDemuxStream(
      new WebSocket(server.url),
      (chunk) => (stdout += chunk),
      (chunk) => (stderr += chunk),
    )
    return [stdout, stderr]
  } finally {
    await server.close()
  }
}

describe('stdDemuxStream against a real WebSocket', () => {
  it.each<[string, Buffer[], [string, string]]>([
    ['single message', [frame(STDOUT, 'out\n', STDERR, 'err\n')], ['out\n', 'err\n']],
    [
      'marker split 2+1',
      [frame(STDOUT, 'out\n', STDERR.subarray(0, 2)), frame(STDERR.subarray(2), 'err\n')],
      ['out\n', 'err\n'],
    ],
    [
      'marker split 1+2',
      [frame(STDOUT, 'out\n', STDERR.subarray(0, 1)), frame(STDERR.subarray(1), 'err\n')],
      ['out\n', 'err\n'],
    ],
    ['message is only a marker', [STDOUT, frame('out\n'), STDERR, frame('err\n')], ['out\n', 'err\n']],
    ['message ends with a stderr marker', [frame(STDOUT, 'out\n', STDERR), frame('err\n')], ['out\n', 'err\n']],
    ['message ends with a stdout marker', [frame(STDOUT, 'a\n', STDOUT), frame('b\n')], ['a\nb\n', '']],
  ])('%s', async (_name, frames, expected) => {
    expect(await demux(frames)).toEqual(expected)
  })
})

describe('stdDemuxStream buffering (#357)', () => {
  it.each([128 * 1024, 1024 * 1024, 4 * 1024 * 1024])('accepts a %d byte message', async (size) => {
    const [stdout, stderr] = await demux([frame(STDOUT, Buffer.alloc(size, 0x61))])
    expect(stdout.length).toBe(size)
    expect(stderr).toBe('')
  })

  it('demuxes marker-heavy output split into large messages', async () => {
    const records = 50_000
    let expectedOut = ''
    let expectedErr = ''
    const parts: Buffer[] = []
    for (let i = 0; i < records; i++) {
      const line = `line ${i}\n`
      if (i % 3 === 0) {
        expectedErr += line
        parts.push(STDERR, Buffer.from(line))
      } else {
        expectedOut += line
        parts.push(STDOUT, Buffer.from(line))
      }
    }
    const stream = Buffer.concat(parts)
    const frames: Buffer[] = []
    for (let offset = 0; offset < stream.length; offset += 64 * 1024) {
      frames.push(stream.subarray(offset, offset + 64 * 1024))
    }
    expect(await demux(frames)).toEqual([expectedOut, expectedErr])
  })

  it('gives the same output for every split of a stream into two messages', async () => {
    const stream = frame(STDOUT, 'hello\n', STDERR, 'oops\n', STDOUT, 'bye\n')
    for (let offset = 1; offset < stream.length; offset++) {
      expect(await demux([stream.subarray(0, offset), stream.subarray(offset)])).toEqual(['hello\nbye\n', 'oops\n'])
    }
  })
})
