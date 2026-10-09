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
