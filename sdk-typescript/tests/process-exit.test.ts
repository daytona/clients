/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// A script that waits on sandboxes and never calls `daytona.close()` must
// still exit, and waits spaced apart must reuse one socket and one ticket.
// An open WebSocket keeps Node's event loop alive; the hub keeps its idle
// socket open but releases it from the loop.
// A real child process runs the package — compiled fresh from THIS source
// tree in beforeAll, so it never tests a stale dist —
// against a local api stand-in (ticket mint + WebSocket upgrade + snapshot).
// It needs the runtime's global WebSocket (Node >= 22, the SDK's `engines`
// floor); on an older Node the hub never opens a socket, so there is nothing
// to hold the loop and nothing to test. CI's Node 22 job runs it.
import { execFileSync, spawn } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(__dirname, '..')
// Inside the package so the compiled code resolves its dependencies
// (axios, …) from this node_modules.
const outDir = mkdtempSync(path.join(root, '.exit-test-'))
// The v2 client implementation, accessed through the root client's v2 namespace.
const dist = path.join(outDir, 'v2', 'Daytona.js')

beforeAll(() => {
  if (typeof (globalThis as { WebSocket?: unknown }).WebSocket !== 'function') return
  execFileSync(
    process.execPath,
    [require.resolve('typescript/bin/tsc'), '-p', path.join(root, 'tsconfig.cjs.json'), '--outDir', outDir],
    { cwd: root, stdio: 'pipe' },
  )
  writeFileSync(path.join(outDir, 'package.json'), JSON.stringify({ type: 'commonjs' }))
}, 120_000)

afterAll(() => rmSync(outDir, { recursive: true, force: true }))

function runChild(script: string): Promise<{ code: number | null; out: string }> {
  const child = spawn(process.execPath, ['-e', script], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, DAYTONA_SANDBOX_EVENTS: '1' },
  })
  let out = ''
  child.stdout.on('data', (b) => { out += String(b) })
  child.stderr.on('data', (b) => { out += String(b) })
  return new Promise((resolve) => {
    const kill = setTimeout(() => { child.kill('SIGKILL'); resolve({ code: -1, out }) }, 15_000)
    child.on('close', (code) => { clearTimeout(kill); resolve({ code, out }) })
  })
}

function decodeMaskedClientTextFramesForTest(
  state: { buffer: Buffer },
  chunk: Buffer,
): Array<Record<string, unknown>> {
  state.buffer = Buffer.concat([state.buffer, chunk])
  const frames: Array<Record<string, unknown>> = []
  for (;;) {
    if (state.buffer.length < 2) return frames
    const opcode = state.buffer[0] & 0x0f
    const masked = (state.buffer[1] & 0x80) !== 0
    const length = state.buffer[1] & 0x7f
    if (length === 126 || length === 127 || !masked) return frames
    const frameLength = 2 + 4 + length
    if (state.buffer.length < frameLength) return frames
    const frame = state.buffer.subarray(0, frameLength)
    state.buffer = state.buffer.subarray(frameLength)
    if (opcode !== 0x1) continue
    const mask = frame.subarray(2, 6)
    const payload = frame.subarray(6)
    const decoded = Buffer.alloc(payload.length)
    for (let i = 0; i < payload.length; i++) decoded[i] = payload[i] ^ mask[i % 4]
    try {
      frames.push(JSON.parse(decoded.toString('utf8')) as Record<string, unknown>)
    } catch {}
  }
}

function encodeMaskedClientTextFrameForTest(payload: string): Buffer {
  const body = Buffer.from(payload, 'utf8')
  const mask = Buffer.from([0x11, 0x22, 0x33, 0x44])
  const masked = Buffer.alloc(body.length)
  for (let i = 0; i < body.length; i++) masked[i] = body[i] ^ mask[i % 4]
  return Buffer.concat([Buffer.from([0x81, 0x80 | body.length]), mask, masked])
}

function encodeMaskedClientTextFrame(payload: string): string {
  return String.raw`
function encodeMaskedClientTextFrame(payload) {
  const body = Buffer.from(payload, 'utf8')
  if (body.length >= 126) throw new Error('test frame too large')
  const mask = Buffer.from([0x11, 0x22, 0x33, 0x44])
  const masked = Buffer.alloc(body.length)
  for (let i = 0; i < body.length; i++) masked[i] = body[i] ^ mask[i % 4]
  return Buffer.concat([Buffer.from([0x81, 0x80 | body.length]), mask, masked])
}
`
}

const steadyScript = String.raw`
const http = require('node:http')
const { createHash } = require('node:crypto')
const { Daytona } = require(${JSON.stringify(dist)})
const ID = 'abc-regiona'
const SNAPSHOTS = [
  { status: 'running', version: 1, wait: ['started'] },
  { status: 'stopped', version: 2, wait: ['stopped'] },
  { status: 'running', version: 3, wait: ['started'] },
]
let mints = 0
let upgrades = 0
let subscribes = 0
let refreshes = 0
let lastState = 'pending'
function decodeClientTextFrames(state, chunk) {
  state.buffer = Buffer.concat([state.buffer, chunk])
  const frames = []
  for (;;) {
    if (state.buffer.length < 2) return frames
    const opcode = state.buffer[0] & 0x0f
    const masked = (state.buffer[1] & 0x80) !== 0
    const length = state.buffer[1] & 0x7f
    if (length === 126 || length === 127 || !masked) return frames
    const frameLength = 2 + 4 + length
    if (state.buffer.length < frameLength) return frames
    const frame = state.buffer.subarray(0, frameLength)
    state.buffer = state.buffer.subarray(frameLength)
    if (opcode !== 0x1) continue
    const mask = frame.subarray(2, 6)
    const payload = frame.subarray(6)
    const decoded = Buffer.alloc(payload.length)
    for (let i = 0; i < payload.length; i++) decoded[i] = payload[i] ^ mask[i % 4]
    try { frames.push(JSON.parse(decoded.toString('utf8'))) } catch {}
  }
}
const srv = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/events/regiona/ticket') {
    mints++
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ ticket: 't', expires_in_ms: 30000, subprotocol: 'daytona.events.v1' }))
    return
  }
  // The refresh a socket-decided wait does to fill in the handle.
  if (req.method === 'GET' && req.url.startsWith('/sandboxes/' + ID)) {
    refreshes++
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ id: ID, name: 'sb', status: lastState, region_id: 'regiona' }))
    return
  }
  res.writeHead(404); res.end()
})
srv.on('upgrade', (req, sock) => {
  upgrades++
  // The stand-in's own socket must not hold the loop: only the SDK's may.
  sock.unref()
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Protocol: daytona.events.v1\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
  const parserState = { buffer: Buffer.alloc(0) }
  sock.on('data', (buf) => {
    // A close frame from the client: answer it and end, as a real server does.
    if ((buf[0] & 0x0f) === 0x8) { if (process.env.EXIT_PROBE) console.log('server: got close frame'); sock.end(Buffer.from([0x88, 0x00])); return }
    for (const clientFrame of decodeClientTextFrames(parserState, buf)) {
      if (clientFrame?.type !== 'subscribe') continue
      const snapshot = SNAPSHOTS[subscribes++] ?? SNAPSHOTS[SNAPSHOTS.length - 1]
      lastState = snapshot.status
      const payload = Buffer.from(JSON.stringify({ type: 'snapshot', id: ID, status: snapshot.status, version: snapshot.version, error: null }))
      sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
    }
  })
})
srv.listen(0, '127.0.0.1', async () => {
  srv.unref()
  process.env.DAYTONA_V2_API_URL = 'http://127.0.0.1:' + srv.address().port
  const d = new Daytona({ apiKey: 'k' })
  const sb = d['buildSandbox'](ID)
  // Three waits, each further apart than any idle close the hub might use
  // for a socket it could not release. The pause between them is a REF'd
  // timer: the script itself keeps running, the socket is what must not.
  for (let i = 0; i < SNAPSHOTS.length; i++) {
    if (i > 0) await new Promise((r) => setTimeout(r, 1200))
    await sb.waitForState(SNAPSHOTS[i].wait, { timeout: 5000 })
  }
  console.log('MINTS=' + mints + ' UPGRADES=' + upgrades + ' REFRESHES=' + refreshes)
  console.log('DONE')
  // deliberately NO d.close()
})
`

const drainScript = String.raw`
const http = require('node:http')
const { createHash } = require('node:crypto')
const { Daytona } = require(${JSON.stringify(dist)})
const ID = 'abc-regiona'
let upgrades = 0
const upgradePaths = []
const srv = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/events/regiona/ticket') {
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ ticket: 't', expires_in_ms: 30000, subprotocol: 'daytona.events.v1' }))
    return
  }
  if (req.method === 'GET' && req.url.startsWith('/sandboxes/' + ID)) {
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ id: ID, name: 'sb', status: 'running', region_id: 'regiona' }))
    return
  }
  res.writeHead(404); res.end()
})
srv.on('upgrade', (req, sock) => {
  sock.unref()
  upgradePaths.push(req.url)
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Protocol: daytona.events.v1\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
  upgrades++
  if (upgrades === 1) {
    setTimeout(() => { sock.write(Buffer.from([0x88, 0x02, 0x03, 0xe9])); sock.end() }, 25)
    return
  }
  setTimeout(() => {
    const payload = Buffer.from(JSON.stringify({ type: 'snapshot', id: ID, status: 'running', version: 1, error: null }))
    sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
  }, 25)
})
srv.listen(0, '127.0.0.1', async () => {
  srv.unref()
  process.env.DAYTONA_V2_API_URL = 'http://127.0.0.1:' + srv.address().port
  const d = new Daytona({ apiKey: 'k' })
  await d['buildSandbox'](ID).waitForState(['started'], { timeout: 5_000 })
  console.log('UPGRADES=' + upgrades + ' PATHS=' + upgradePaths.join(','))
  console.log('DONE')
})
`

const ticketRetryScript = String.raw`
const http = require('node:http')
const { createHash } = require('node:crypto')
const { Daytona } = require(${JSON.stringify(dist)})
const ID = 'abc-regiona'
let tickets = 0
let upgrades = 0
const srv = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/events/regiona/ticket') {
    tickets++
    res.socket.unref()
    if (tickets === 1) {
      res.writeHead(503, { 'content-type': 'application/json', connection: 'close' })
      res.end(JSON.stringify({ code: 'SERVICE_UNAVAILABLE', message: 'later', source: 'DAYTONA_API' }))
      return
    }
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ ticket: 't', expires_in_ms: 30000, subprotocol: 'daytona.events.v1' }))
    return
  }
  if (req.method === 'GET' && req.url.startsWith('/sandboxes/' + ID)) {
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ id: ID, name: 'sb', status: 'running', region_id: 'regiona' }))
    return
  }
  res.writeHead(404); res.end()
})
srv.on('upgrade', (req, sock) => {
  upgrades++
  sock.unref()
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Protocol: daytona.events.v1\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
  setTimeout(() => {
    const payload = Buffer.from(JSON.stringify({ type: 'snapshot', id: ID, status: 'running', version: 1, error: null }))
    sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
  }, 25)
})
srv.listen(0, '127.0.0.1', async () => {
  srv.unref()
  process.env.DAYTONA_V2_API_URL = 'http://127.0.0.1:' + srv.address().port
  const d = new Daytona({ apiKey: 'k' })
  await d['buildSandbox'](ID).waitForState(['started'], { timeout: 5_000 })
  console.log('TICKETS=' + tickets + ' UPGRADES=' + upgrades)
  console.log('DONE')
})
`

const staleRouteScript = String.raw`
const http = require('node:http')
const { createHash } = require('node:crypto')
const { Daytona } = require(${JSON.stringify(dist)})
const ID = 'abc-regiona'
let upgrades = 0
const upgradePaths = []
const srv = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/events/regiona/ticket') {
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ ticket: 't', expires_in_ms: 30000, subprotocol: 'daytona.events.v1' }))
    return
  }
  if (req.method === 'GET' && req.url.startsWith('/sandboxes/' + ID)) {
    res.socket.unref()
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ id: ID, name: 'sb', status: 'running', region_id: 'regiona' }))
    return
  }
  res.writeHead(404); res.end()
})
srv.on('upgrade', (req, sock) => {
  sock.unref()
  upgrades++
  upgradePaths.push(req.url)
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Protocol: daytona.events.v1\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
  if (upgrades === 1) {
    setTimeout(() => {
      const payload = Buffer.from(JSON.stringify({ type: 'error', id: ID, code: 'NOT_FOUND', was_known: true }))
      sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
    }, 25)
    return
  }
  setTimeout(() => {
    const payload = Buffer.from(JSON.stringify({ type: 'snapshot', id: ID, status: 'running', version: 1, error: null }))
    sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
  }, 25)
})
srv.listen(0, '127.0.0.1', async () => {
  srv.unref()
  process.env.DAYTONA_V2_API_URL = 'http://127.0.0.1:' + srv.address().port
  const d = new Daytona({ apiKey: 'k' })
  const sb = d['buildSandbox'](ID)
  sb.learnEventDestination('route-1')
  await sb.waitForState(['started'], { timeout: 5_000 })
  console.log('UPGRADES=' + upgrades + ' PATHS=' + upgradePaths.join(','))
  console.log('DONE')
})
`

// An upgrade the server accepts at TCP and never answers: the open timeout
// must give up without crashing the process (closing a connecting `ws`
// socket emits 'error'), and three such failures must reach the polling
// fallback. The hub is built directly to shorten the 30 s open timeout.
const blackHoleScript = String.raw`
const http = require('node:http')
const { WaitHub, defaultWsFactory, targetPredicate } = require(${JSON.stringify(path.join(outDir, 'v2', 'events.js'))})
const { createHttpClient } = require(${JSON.stringify(path.join(outDir, 'v2', 'http.js'))})
const ID = 'abc-regiona'
let upgrades = 0
const srv = http.createServer((req, res) => {
  res.socket.unref()
  if (req.method === 'POST' && req.url === '/events/regiona/ticket') {
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ ticket: 't', expires_in_ms: 30000, subprotocol: 'daytona.events.v1' }))
    return
  }
  if (req.method === 'GET' && req.url.startsWith('/sandboxes/' + ID)) {
    res.writeHead(200, { 'content-type': 'application/json', connection: 'close' })
    res.end(JSON.stringify({ id: ID, name: 'sb', status: 'running', region_id: 'regiona' }))
    return
  }
  res.writeHead(404); res.end()
})
srv.on('upgrade', (req, sock) => { upgrades++; sock.unref() })
srv.listen(0, '127.0.0.1', async () => {
  srv.unref()
  const url = 'http://127.0.0.1:' + srv.address().port
  const hub = new WaitHub(url, 'k', { enabled: true, reconcileIntervalMs: 0 }, defaultWsFactory, createHttpClient(url, 'k'), { openTimeoutMs: 300 })
  const poll = async () => ({ state: 'started', rawState: 'running' })
  const out = await hub.wait(ID, targetPredicate(new Set(['started']), new Set(['error', 'destroyed', 'destroying'])), { timeout: 20000, sandboxPollFn: poll })
  console.log('UPGRADES=' + upgrades + ' STATE=' + out.state)
  console.log('DONE')
  await hub.detach()
})
`

const splitSubscribeScript = String.raw`
const http = require('node:http')
const net = require('node:net')
const { createHash, randomBytes } = require('node:crypto')
const ID = 'abc-regiona'
${encodeMaskedClientTextFrame('')}
function decodeClientTextFrames(state, chunk) {
  state.buffer = Buffer.concat([state.buffer, chunk])
  const frames = []
  for (;;) {
    if (state.buffer.length < 2) return frames
    const opcode = state.buffer[0] & 0x0f
    const masked = (state.buffer[1] & 0x80) !== 0
    const length = state.buffer[1] & 0x7f
    if (length === 126 || length === 127 || !masked) return frames
    const frameLength = 2 + 4 + length
    if (state.buffer.length < frameLength) return frames
    const frame = state.buffer.subarray(0, frameLength)
    state.buffer = state.buffer.subarray(frameLength)
    if (opcode !== 0x1) continue
    const mask = frame.subarray(2, 6)
    const payload = frame.subarray(6)
    const decoded = Buffer.alloc(payload.length)
    for (let i = 0; i < payload.length; i++) decoded[i] = payload[i] ^ mask[i % 4]
    try { frames.push(JSON.parse(decoded.toString('utf8'))) } catch {}
  }
}
function decodeServerTextFrames(state, chunk) {
  state.buffer = Buffer.concat([state.buffer, chunk])
  const frames = []
  for (;;) {
    if (state.buffer.length < 2) return frames
    const opcode = state.buffer[0] & 0x0f
    const length = state.buffer[1] & 0x7f
    if ((state.buffer[1] & 0x80) !== 0 || length === 126 || length === 127) return frames
    const frameLength = 2 + length
    if (state.buffer.length < frameLength) return frames
    const frame = state.buffer.subarray(0, frameLength)
    state.buffer = state.buffer.subarray(frameLength)
    if (opcode !== 0x1) continue
    try { frames.push(JSON.parse(frame.subarray(2).toString('utf8'))) } catch {}
  }
}
const srv = http.createServer((req, res) => { res.writeHead(404); res.end() })
srv.on('upgrade', (req, sock) => {
  sock.unref()
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
  const parserState = { buffer: Buffer.alloc(0) }
  sock.on('data', (buf) => {
    for (const clientFrame of decodeClientTextFrames(parserState, buf)) {
      if (clientFrame?.type !== 'subscribe') continue
      const payload = Buffer.from(JSON.stringify({ type: 'snapshot', id: ID, status: 'running', version: 1, error: null }))
      sock.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
    }
  })
})
srv.listen(0, '127.0.0.1', () => {
  srv.unref()
  const sock = net.createConnection({ host: '127.0.0.1', port: srv.address().port })
  sock.unref()
  const key = randomBytes(16).toString('base64')
  const subscribe = encodeMaskedClientTextFrame(JSON.stringify({ type: 'subscribe', ids: [{ id: ID, known: false }] }))
  const recvState = { phase: 'handshake', buffer: Buffer.alloc(0) }
  const timer = setTimeout(() => { console.log('TIMEOUT'); process.exit(2) }, 2_000)
  sock.on('connect', () => {
    sock.write('GET /events/regiona HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n')
  })
  sock.on('data', (chunk) => {
    if (recvState.phase === 'handshake') {
      recvState.buffer = Buffer.concat([recvState.buffer, chunk])
      const split = recvState.buffer.indexOf('\r\n\r\n')
      if (split === -1) return
      const rest = recvState.buffer.subarray(split + 4)
      recvState.phase = 'frames'
      recvState.buffer = Buffer.alloc(0)
      sock.write(subscribe.subarray(0, 5))
      setTimeout(() => { sock.write(subscribe.subarray(5)) }, 10)
      if (rest.length === 0) return
      chunk = rest
    }
    for (const frame of decodeServerTextFrames(recvState, chunk)) {
      if (frame?.type === 'snapshot' && frame.id === ID && frame.status === 'running') {
        clearTimeout(timer)
        console.log('DONE')
        sock.end()
        srv.close(() => process.exit(0))
      }
    }
  })
})
`

const withWebSocket = typeof (globalThis as { WebSocket?: unknown }).WebSocket === 'function' ? test : test.skip

test('the child-server frame parser buffers a subscribe frame split across chunks', () => {
  const frame = encodeMaskedClientTextFrameForTest(JSON.stringify({ type: 'subscribe', ids: [{ id: 'abc-regiona', known: false }] }))
  const state = { buffer: Buffer.alloc(0) }
  expect(decodeMaskedClientTextFramesForTest(state, frame.subarray(0, 5))).toEqual([])
  expect(decodeMaskedClientTextFramesForTest(state, frame.subarray(5))).toEqual([
    { type: 'subscribe', ids: [{ id: 'abc-regiona', known: false }] },
  ])
})

withWebSocket('a script that never calls close() exits after its last wait', async () => {
  const started = Date.now()
  const { code, out } = await runChild(steadyScript)
  expect(out).toContain('DONE'); expect(code).toBe(0)
  // One ticket and one socket for all three waits; each socket-decided wait
  // refreshes the handle once.
  expect(out).toContain('MINTS=1 UPGRADES=1 REFRESHES=3')
  // ~2.4 s of deliberate pauses; the exit itself must follow at once, not
  // after an idle close.
  expect(Date.now() - started).toBeLessThan(6_000)
}, 20_000)

withWebSocket.each([
  ['a drain close does not let the process exit before the retry settles', drainScript],
  ['a stale-route retry releases the old socket so the process exits without close()', staleRouteScript],
])('%s', async (_name, script) => {
  const { code, out } = await runChild(script)
  expect(out).toContain('DONE'); expect(code).toBe(0)
  if (script === drainScript) {
    expect(out).toContain('UPGRADES=2 PATHS=/events/regiona,/events/regiona')
  } else {
    expect(out).toContain('UPGRADES=2 PATHS=/events/regiona,/events/regiona')
  }
}, 20_000)

withWebSocket('a black-holed upgrade times out without crashing and falls back to polling', async () => {
  const { code, out } = await runChild(blackHoleScript)
  expect(out).not.toContain('WebSocket was closed before the connection was established')
  expect(out).toContain('DONE'); expect(code).toBe(0)
  expect(out).toMatch(/UPGRADES=[3-9]/)
}, 20_000)

withWebSocket('the child server parser accepts a subscribe frame split across TCP chunks', async () => {
  const { code, out } = await runChild(splitSubscribeScript)
  expect(out).toContain('DONE'); expect(code).toBe(0)
}, 20_000)

withWebSocket('a ticket refusal does not let the process exit before retrying', async () => {
  const { code, out } = await runChild(ticketRetryScript)
  expect(out).toContain('DONE'); expect(code).toBe(0)
  expect(out).toContain('TICKETS=2 UPGRADES=1')
}, 20_000)
