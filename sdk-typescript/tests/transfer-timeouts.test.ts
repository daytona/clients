/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// File transfers have no total deadline, through each public call. Every call
// below runs against a toolbox that takes longer than the client's request
// timeout (100 ms) to answer in full, but keeps bytes moving (a chunk every
// 50 ms, inside the transfer's idle bound), so each would fail with
// ClientTimeoutError if its transfer opt-out were dropped. An ordinary call on
// the same client is cut, which proves the timeout is armed.
// tests/transfer-bounds.test.ts pins the idle bound itself.
// sdk-python/tests/test_transfer_timeouts.py holds the Python transfers to the
// same rule.

import { createServer } from 'node:http'
import type { IncomingMessage, Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Daytona } from '../src/v2/Daytona'
import { ClientTimeoutError } from '../src/v2/errors/DaytonaError'
import { lazySandbox } from './lazySandbox'

const SLOW_MS = 350
const GAP_MS = 50
const BOUNDARY = 'transfer-boundary'

let server: Server
let base: string
let dir: string

/** Transfers trickle their response over SLOW_MS, a chunk every GAP_MS; anything else answers after SLOW_MS. */
function toolbox(request: IncomingMessage, response: ServerResponse): void {
  const url = new URL(request.url ?? '/', 'http://toolbox.invalid')
  request.resume()
  const json = (payload: unknown): void => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(payload))
  }
  const trickle = (chunks: readonly string[], contentType: string): void => {
    response.writeHead(200, { 'Content-Type': contentType })
    response.flushHeaders()
    let index = 0
    const timer = setInterval(() => {
      if (index === chunks.length) {
        clearInterval(timer)
        response.end()
        return
      }
      response.write(chunks[index++])
    }, GAP_MS)
    response.on('close', () => clearInterval(timer))
  }
  const later = (answer: () => void): void => {
    const timer = setTimeout(answer, SLOW_MS)
    response.on('close', () => clearTimeout(timer))
  }
  // A JSON answer trickled a byte at a time, padded to outlast SLOW_MS.
  const trickledJson = (payload: unknown): void => trickle([...JSON.stringify(payload).padEnd(SLOW_MS / GAP_MS, ' ')], 'application/json')
  switch (url.pathname) {
    case '/files/upload':
      return trickledJson({})
    case '/files/bulk-upload':
      return trickledJson({ files: [{ path: '/a', bytes: 1 }], errors: [] })
    case '/files/download':
      return trickle(['x', 'x', 'x', 'x', 'x', 'x', 'x'], 'application/octet-stream')
    case '/files/bulk-download': {
      const part = `--${BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\nContent-Type: application/octet-stream\r\n\r\nxyz\r\n--${BOUNDARY}--\r\n`
      const size = Math.ceil(part.length / 7)
      return trickle(Array.from({ length: 7 }, (_, i) => part.slice(i * size, (i + 1) * size)), `multipart/form-data; boundary=${BOUNDARY}`)
    }
    case '/computeruse/recordings/rec-1/download':
      return trickle(['r', 'r', 'r', 'r', 'r', 'r', 'r'], 'video/mp4')
    default:
      return later(() => json({ items: [] }))
  }
}

beforeAll(async () => {
  server = createServer(toolbox)
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  base = `http://127.0.0.1:${address.port}`
  dir = await mkdtemp(join(tmpdir(), 'daytona-transfer-'))
})

afterAll(async () => {
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
  await rm(dir, { recursive: true, force: true })
})

function slowToolboxSandbox() {
  const client = new Daytona({ apiKey: 'k', requestTimeoutSeconds: 0.1 })
  return { client, sandbox: lazySandbox(client, 'sbx', { toolboxUrl: base, accessToken: 'token' }) }
}

describe('file transfers outlast the request timeout', () => {
  it('uploadFile', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    const local = join(dir, 'up.bin')
    await writeFile(local, 'payload')
    await expect(sandbox.fs.uploadFile(local, '/remote.bin')).resolves.toBeUndefined()
    await client.close()
  })

  it('downloadFile', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    const local = join(dir, 'down.bin')
    await sandbox.fs.downloadFile('/remote.bin', local)
    expect(await readFile(local, 'utf8')).toBe('xxxxxxx')
    await client.close()
  })

  it('writeMany', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    const result = await sandbox.fs.writeMany({ '/a': new Uint8Array([1]) })
    expect(result.files).toEqual([{ path: '/a', bytes: 1 }])
    await client.close()
  })

  it('readMany', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    const result = await sandbox.fs.readMany(['/a'])
    expect(new TextDecoder().decode(result.files['/a'])).toBe('xyz')
    await client.close()
  })

  it('computerUse.recording.download', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    expect(new TextDecoder().decode(await sandbox.computerUse.recording.download('rec-1'))).toBe('rrrrrrr')
    await client.close()
  })

  it('while an ordinary call on the same client is cut', async () => {
    const { client, sandbox } = slowToolboxSandbox()
    await expect(sandbox.fs.list('/')).rejects.toBeInstanceOf(ClientTimeoutError)
    await client.close()
  })
})
