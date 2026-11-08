/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Every SDK-owned data value the toolbox namespaces return, or hand to a
// callback, is frozen at run time at every depth, as the Python SDK's frozen
// dataclasses are; live handles, byte buffers and streams are not. Served over
// real HTTP so each value goes through the transport and its mapper.

import { createServer } from 'node:http'
import type { IncomingMessage, Server } from 'node:http'
import { once } from 'node:events'
import { ComputerUseService } from '../src/v2/ComputerUse'
import { FileSystemService } from '../src/v2/FileSystem'
import { GitService } from '../src/v2/Git'
import { InterpreterService } from '../src/v2/Interpreter'
import { LspService } from '../src/v2/Lsp'
import { ProcessService } from '../src/v2/Process'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'
import { envelopeList, fileMatchFromWire, gitCommitFromWire, gitRemoteFromWire, logFrameFromWire, replaceResultFromWire, systemMetricsFromWire } from '../src/v2/toolboxModels'
import { mapComputerUseProcessStatus } from '../src/v2/ComputerUseMappers'
import { deepFrozen } from '../src/v2/frozen'
import type * as toolbox from '../src/v2/gen/toolbox-types'

const CREATED_AT = '2026-01-01T00:00:00Z'
const FRAME = { cursor: 'c1', channel: 'stdout', data: 'hi', timestamp: 1 }
const RESULT_FRAME = { cursor: 'c2', channel: 'execute_result', data: '{"data":{"text/plain":"2","application/json":{"n":[1]}}}', timestamp: 2 }
const ERROR_FRAME = { cursor: 'c3', channel: 'error', data: '{"ename":"E","evalue":"v","traceback":["t"]}', timestamp: 3 }
const PAGE = { frames: [FRAME, RESULT_FRAME, ERROR_FRAME], nextCursor: 'c3', truncatedHead: false, eof: true }
const RECORD = {
  id: 'p', kind: 'exec', state: 'terminal', reason: 'exited', exitCode: 0, cwd: '/', stdin: 'closed',
  keepLogs: 'on_exit_ttl', createdAt: CREATED_AT, truncatedHead: false, argv: ['a', 'b'], terminal: { cols: 80, rows: 24 },
}
const FILE_INFO = {
  name: 'a', path: '/d/a', size: 3, mode: '-rw-r--r--', modTime: 'x', modifiedAt: CREATED_AT,
  isDir: false, owner: 'u', group: 'g', permissions: '0644',
}
const RANGE = { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }
const SYMBOLS = [{ name: 's', kind: 1, location: { uri: 'u', range: RANGE } }]
const PROCESS_STATUS = { Running: true, Priority: 1, AutoRestart: true, Pid: 5, Failed: false, RestartAttempts: 0, RestartBackoffMs: 1500, Status: 'RUNNING', Extra: { a: [1] } }
const NODE = {
  id: 'n', role: 'r', name: 'nm', bounds: { x: 0, y: 0, width: 1, height: 1 }, states: ['s'],
  children: [{ id: 'c', role: 'r', name: 'c', bounds: { x: 0, y: 0, width: 1, height: 1 }, states: [] }],
}
const RECORDING = { id: 'r', fileName: 'f', filePath: '/f', startTime: CREATED_AT, status: 'completed', durationSeconds: 1.5, sizeBytes: 10 }
const WINDOW = { id: 1, title: 't', x: 0, y: 0, width: 1, height: 1, isActive: true }

/**
 * The JSON body for a request, keyed by `METHOD path` (query dropped), in the
 * contract's shape. Under `/envelope` a route the contract answers with a bare
 * array answers `{ items }` instead, the other list shape the mappers accept.
 */
function respond(request: IncomingMessage): unknown {
  const raw = (request.url ?? '').split('?')[0] ?? ''
  const prefix = /^\/(envelope|ready|starting)(?=\/)/.exec(raw)?.[1]
  const envelope = prefix === 'envelope'
  const path = prefix === undefined ? raw : raw.slice(prefix.length + 1)
  // The contract's three desktop states (`/ready`, `/starting`, else failed).
  if (path === '/computeruse/status' && prefix === 'ready') return { status: 'active', desktop: 'ready', extra: { a: [1] } }
  if (path === '/computeruse/status' && prefix === 'starting') return { status: 'inactive', desktop: 'starting', cause: null, extra: { a: [1] } }
  const routes: Record<string, unknown> = {
    'POST /processes': { ...RECORD, logs: PAGE },
    'GET /processes': { items: [RECORD] },
    'GET /processes/p': RECORD,
    'GET /processes/s': { ...RECORD, id: 's', kind: 'session', state: 'running', reason: undefined, exitCode: undefined },
    'GET /processes/p/wait': { reason: 'exited', exitCode: 0 },
    'GET /processes/p/logs': PAGE,
    'GET /files': [FILE_INFO],
    'GET /files/info': FILE_INFO,
    'GET /files/find': [{ file: '/a', line: 1, content: 'x' }],
    'GET /files/search': { files: ['/a'] },
    'POST /files/replace': [{ file: '/a', success: true }],
    'POST /files/bulk-upload': { files: [{ path: '/a', bytes: 1 }], errors: [] },
    'GET /git/status': { currentBranch: 'main', ahead: 0, behind: 0, branchPublished: true, fileStatus: [{ name: 'a', staging: 'M', worktree: ' ', extra: '' }] },
    'GET /git/remotes': { remotes: [{ name: 'origin', url: 'u' }] },
    'POST /git/commit': { hash: 'h' },
    'GET /git/branches': { branches: ['main'] },
    'GET /git/history': [{ hash: 'h', message: 'm', author: 'a', email: 'e', timestamp: CREATED_AT }],
    'POST /lsp/completions': { isIncomplete: false, items: [{ label: 'l', kind: 1, documentation: { kind: 'markdown', value: 'v' } }] },
    'GET /lsp/document-symbols': SYMBOLS,
    'GET /lsp/workspacesymbols': SYMBOLS,
    'GET /computeruse/status': { status: 'inactive', desktop: 'failed', cause: { code: 'X', message: 'm', detail: { a: [1] } } },
    'POST /computeruse/start': { message: 'm', status: { xvfb: PROCESS_STATUS } },
    'POST /computeruse/stop': { message: 'm', status: { xvfb: PROCESS_STATUS } },
    'POST /computeruse/restart': { message: 'm', status: 'active', desktop: 'ready', extra: { a: [1] } },
    'GET /computeruse/process-status': { xvfb: PROCESS_STATUS },
    'GET /computeruse/process/xvfb/status': { processName: 'xvfb', running: true },
    'POST /computeruse/process/xvfb/restart': { message: 'm', processName: 'xvfb' },
    'GET /computeruse/process/xvfb/logs': { processName: 'xvfb', logs: 'l' },
    'GET /computeruse/process/xvfb/errors': { processName: 'xvfb', errors: 'e' },
    'GET /computeruse/mouse/position': { x: 1, y: 2 },
    'POST /computeruse/mouse/move': { x: 1, y: 2 },
    'POST /computeruse/mouse/click': { x: 1, y: 2 },
    'POST /computeruse/mouse/drag': { x: 1, y: 2 },
    'POST /computeruse/mouse/scroll': { success: true },
    'GET /computeruse/screenshot': { screenshot: 'AAA=', sizeBytes: 3, cursorPosition: { x: 1, y: 2 } },
    'GET /computeruse/display/info': { displays: [{ id: 0, x: 0, y: 0, width: 1, height: 1, isActive: true }] },
    'GET /computeruse/display/windows': { windows: [WINDOW] },
    'GET /computeruse/a11y/tree': { root: NODE, truncated: false },
    'POST /computeruse/a11y/find': { matches: [NODE], truncated: false },
    'POST /computeruse/recordings/start': RECORDING,
    'POST /computeruse/recordings/stop': RECORDING,
    'GET /computeruse/recordings': { recordings: [RECORDING] },
    'GET /computeruse/recordings/r': RECORDING,
  }
  const body = routes[`${request.method ?? 'GET'} ${path}`] ?? {}
  return envelope && Array.isArray(body) ? { items: body } : body
}

/** How long the timed-out routes hold, past every budget the rows use. */
const HOLD_MS = 300
const BUDGET_SECONDS = 0.1

/** Asserts every plain object and array reachable from `value` is frozen, and a write to it throws. */
function expectDeeplyFrozen(value: unknown): void {
  const pending: unknown[] = [value]
  let checked = 0
  while (pending.length > 0) {
    const item = pending.pop()
    if (typeof item !== 'object' || item === null) continue
    const proto: unknown = Object.getPrototypeOf(item)
    const plain = Array.isArray(item) || proto === null || Object.getPrototypeOf(proto) === null
    if (!plain) continue
    expect(Object.isFrozen(item)).toBe(true)
    checked++
    for (const key of Reflect.ownKeys(item)) pending.push(Reflect.get(item, key))
  }
  expect(checked).toBeGreaterThan(0)
  expect(() => {
    // SAFETY: the write is the assertion; a frozen value refuses it in strict mode.
    (value as Record<string, unknown>).probe = 1
  }).toThrow(TypeError)
}

describe('toolbox results are frozen at run time', () => {
  let server: Server
  let transport: HttpTransport
  let base: string

  beforeAll(async () => {
    server = createServer((request, response) => {
      request.resume()
      const path = (request.url ?? '').split('?')[0]
      if (timedOut(path, request, response)) return
      if (path === '/processes/p/logs' && request.headers.accept === 'text/event-stream') {
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.end(`event: log\ndata: ${JSON.stringify(FRAME)}\n\nevent: warning\ndata: {"cursor":"c2","message":"trimmed","firstAvailableCursor":"c0"}\n\nevent: state\ndata: ${JSON.stringify({ ...RECORD, cursor: 'c3' })}\n\nevent: eof\ndata: {"cursor":"c4"}\n\n`)
        return
      }
      if (path === '/files/bulk-download') {
        response.writeHead(200, { 'Content-Type': 'multipart/form-data; boundary=B' })
        response.end('--B\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\nContent-Type: application/octet-stream\r\n\r\nhi\r\n'
          + '--B\r\nContent-Disposition: form-data; name="error"\r\nContent-Type: application/json\r\n\r\n{"path":"/b","message":"gone"}\r\n--B--\r\n')
        return
      }
      if (path === '/files/watch') {
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.write('event: start\ndata: {}\n\nevent: fs\ndata: {"type":"create","name":"a","isDir":false,"timestamp":1}\n\n')
        return
      }
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(respond(request)))
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address()
    if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
    base = `http://127.0.0.1:${address.port}`
    transport = new HttpTransport(base, 'k', 5_000)
  })

  // Under `/slow`, a started process ('rp') stays running and its `/wait`
  // holds past the caller budget before it answers `timed_out` (`exited` once
  // it was signalled). `w1` / `w2` / `w3` reach the three `timed_out` returns
  // of `wait()`: the record read after a `timed_out` long-poll outlasts the
  // budget; the long-poll itself does; the connection drops after the budget.
  let signalled = false
  function timedOut(path: string | undefined, request: IncomingMessage, response: import('node:http').ServerResponse): boolean {
    const json = (body: unknown) => {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(body))
    }
    const later = (action: () => void) => void setTimeout(action, HOLD_MS)
    const running = (id: string) => ({ ...RECORD, id, state: 'running', reason: undefined, exitCode: undefined })
    switch (path) {
      case '/processes/w1/wait': json({ reason: 'timed_out' }); return true
      case '/processes/w1': later(() => json(running('w1'))); return true
      case '/processes/w2/wait': later(() => json({ reason: 'timed_out' })); return true
      case '/processes/w3/wait': later(() => response.socket?.destroy()); return true
      case '/slow/processes':
        if (request.method !== 'POST') return false
        json(running('rp'))
        return true
      case '/slow/processes/rp': json(running('rp')); return true
      case '/slow/processes/rp/wait':
        if (signalled) {
          signalled = false
          json({ reason: 'exited', exitCode: 130 })
        } else later(() => json({ reason: 'timed_out' }))
        return true
      case '/slow/processes/rp/signals':
        signalled = true
        response.writeHead(204)
        response.end()
        return true
      case '/slow/processes/rp/logs': json({ frames: [], nextCursor: 'c0', truncatedHead: false, eof: true }); return true
      default: return false
    }
  }

  afterAll(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  const fs = () => new FileSystemService(INTERNAL, transport)
  const git = () => new GitService(INTERNAL, transport)
  const process = () => new ProcessService(INTERNAL, transport, 'sbx')
  const cu = () => new ComputerUseService(INTERNAL, transport)
  const lsp = async () => new LspService(INTERNAL, transport).start('python', '/p')
  const at = (prefix: string) => new HttpTransport(`${base}${prefix}`, 'k', 5_000)

  const rows: ReadonlyArray<readonly [string, () => Promise<unknown>]> = [
    ['process.run', () => process().run('true')],
    ['process.get', () => process().get('p')],
    ['process.list', () => process().list()],
    ['handle.get', () => process().connect('p').get()],
    ['handle.logs', () => process().connect('p').logs()],
    ['handle.wait', () => process().connect('p').wait()],
    ['handle.streamLogs events', async () => {
      const events: unknown[] = []
      for await (const event of process().connect('p').streamLogs()) events.push(event)
      expect(events.map((event) => (event as { type: string }).type)).toEqual(['log', 'warning', 'state', 'eof'])
      for (const event of events) expectDeeplyFrozen(event)
      return events[0]
    }],
    ['session.run', () => process().connectSession('s').run('true')],
    ['session.get', () => process().connectSession('s').get()],
    ['pty.list', () => new ProcessService(INTERNAL, transport, 'sbx').list()],
    ['interpreter.run', () => new InterpreterService(INTERNAL, transport, 'sbx').run('1 + 1')],
    ['fs.list', () => fs().list('/d')],
    ['fs.list ({ items })', () => new FileSystemService(INTERNAL, at('/envelope')).list('/d')],
    ['fs.stat', () => fs().stat('/d/a')],
    ['fs.grep', () => fs().grep('/d', 'x')],
    ['fs.grep ({ items })', () => new FileSystemService(INTERNAL, at('/envelope')).grep('/d', 'x')],
    ['fs.glob', () => fs().glob('/d', '*')],
    ['fs.replace', () => fs().replace(['/a'], 'x', 'y')],
    ['fs.replace ({ items })', () => new FileSystemService(INTERNAL, at('/envelope')).replace(['/a'], 'x', 'y')],
    ['fs.writeMany', () => fs().writeMany({ '/a': new Uint8Array([1]) })],
    ['fs.readMany', () => fs().readMany(['/a', '/b'])],
    ['git.status', () => git().status('/r')],
    ['git.listRemotes', () => git().listRemotes('/r')],
    ['git.commit', () => git().commit('/r', 'm', { author: 'a', email: 'e' })],
    ['git.listBranches', () => git().listBranches('/r')],
    ['git.history', () => git().history('/r')],
    ['git.history ({ items })', () => new GitService(INTERNAL, at('/envelope')).history('/r')],
    ['lsp.documentSymbols ({ items } is refused, so bare only)', async () => (await lsp()).documentSymbols('u')],
    ['lsp.completions', async () => (await lsp()).completions('u', { line: 0, character: 0 })],
    ['lsp.documentSymbols', async () => (await lsp()).documentSymbols('u')],
    ['lsp.workspaceSymbols', async () => (await lsp()).workspaceSymbols('s')],
    ['computerUse.status (failed, with its cause)', () => cu().status()],
    ['computerUse.status (ready)', () => new ComputerUseService(INTERNAL, at('/ready')).status()],
    ['computerUse.status (starting)', () => new ComputerUseService(INTERNAL, at('/starting')).status()],
    ['computerUse.start', () => cu().start()],
    ['computerUse.stop', () => cu().stop()],
    ['computerUse.restart', () => cu().restart()],
    ['service.status()', () => cu().service.status()],
    ['service.status(name)', () => cu().service.status('xvfb')],
    ['service.restart', () => cu().service.restart('xvfb')],
    ['service.logs', () => cu().service.logs('xvfb')],
    ['service.errors', () => cu().service.errors('xvfb')],
    ['mouse.position', () => cu().mouse.position()],
    ['mouse.move', () => cu().mouse.move({ x: 1, y: 2 })],
    ['mouse.click', () => cu().mouse.click({ x: 1, y: 2 })],
    ['mouse.drag', () => cu().mouse.drag({ startX: 0, startY: 0, endX: 1, endY: 2 })],
    ['mouse.scroll', () => cu().mouse.scroll({ x: 1, y: 2, direction: 'down' })],
    ['screen.screenshot', () => cu().screen.screenshot({ showCursor: true })],
    ['display.info', () => cu().display.info()],
    ['display.windows', () => cu().display.windows()],
    ['a11y.tree', () => cu().a11y.tree({ scope: 'focused' })],
    ['a11y.find', () => cu().a11y.find({ scope: 'all' })],
    ['recording.start', () => cu().recording.start()],
    ['recording.stop', () => cu().recording.stop({ id: 'r' })],
    ['recording.list', () => cu().recording.list()],
    ['recording.get', () => cu().recording.get('r')],
    // Mappers whose values usually arrive inside a frozen envelope: each
    // freezes its own value too, so one used on its own is frozen as well.
    ['envelopeList', async () => envelopeList({ items: [{ a: [1] }] }, 'items', 'list', (item: { a: number[] }) => ({ ...item }))],
    ['logFrameFromWire', async () => logFrameFromWire(FRAME)],
    ['fileMatchFromWire', async () => fileMatchFromWire({ file: '/a', line: 1, content: 'x' })],
    ['replaceResultFromWire', async () => replaceResultFromWire({ file: '/a', success: false, error: 'e' })],
    ['gitCommitFromWire', async () => gitCommitFromWire({ hash: 'h', message: 'm', author: 'a', email: 'e', timestamp: CREATED_AT })],
    ['gitRemoteFromWire', async () => gitRemoteFromWire({ name: 'origin', url: 'u' })],
    ['mapComputerUseProcessStatus', async () => mapComputerUseProcessStatus(PROCESS_STATUS)],
    ['sandbox.metrics (its mapper)', async () => systemMetricsFromWire({
      timestamp: CREATED_AT, cpuCount: 1, cpuUtilization: 0.5, memoryUsageBytes: 1, memoryTotalBytes: 2, diskUsageBytes: 1, diskTotalBytes: 2,
    } satisfies toolbox.Metrics)],
  ]

  it.each(rows)('%s', async (_name, call) => {
    expectDeeplyFrozen(await call())
  })

  // Each row's budget runs out; the interrupted interpreter run returns the
  // result the interrupt produced, as documented.
  const timedOutRows: ReadonlyArray<readonly [string, () => Promise<{ readonly reason?: string; readonly process?: { readonly reason: string } }>, string]> = [
    ['wait(): record read outlasts the budget', () => process().connect('w1').wait({ timeoutSeconds: BUDGET_SECONDS }), 'timed_out'],
    ['wait(): long-poll outlasts the budget', () => process().connect('w2').wait({ timeoutSeconds: BUDGET_SECONDS }), 'timed_out'],
    ['wait(): connection drops after the budget', () => process().connect('w3').wait({ timeoutSeconds: BUDGET_SECONDS }), 'timed_out'],
    ['process.run', () => new ProcessService(INTERNAL, at('/slow'), 'sbx').run('sleep', { timeoutSeconds: BUDGET_SECONDS }), 'timed_out'],
    ['interpreter.run (interrupted)', () => new InterpreterService(INTERNAL, at('/slow'), 'sbx').run('loop()', { timeoutSeconds: BUDGET_SECONDS }), 'exited'],
    ['interpreter.run (queued past its budget)', async () => {
      const interpreter = new InterpreterService(INTERNAL, at('/slow'), 'sbx')
      const first = interpreter.run('loop()', { timeoutSeconds: BUDGET_SECONDS })
      const queued = await interpreter.run('2', { timeoutSeconds: BUDGET_SECONDS / 2 })
      await first
      return queued
    }, 'timed_out'],
  ]

  it.each(timedOutRows)('timed out: %s', async (_name, call, reason) => {
    const result = await call()
    expect(result.reason ?? result.process?.reason).toBe(reason)
    expectDeeplyFrozen(result)
  })

  it('freezes the children of an already frozen object, ends on a cycle, and leaves class instances alone', () => {
    class Handle { public state = 'live' }
    const leaf: Record<string, unknown> = {}
    const inner = { leaf }
    const bytes = new Uint8Array([1])
    const handle = new Handle()
    const cyclic: Record<string, unknown> = { bytes, handle }
    cyclic.self = cyclic
    const value = deepFrozen({ outer: Object.freeze({ inner }), cyclic })
    expect(Object.isFrozen(value)).toBe(true)
    expect(Object.isFrozen(inner)).toBe(true)
    expect(Object.isFrozen(leaf)).toBe(true)
    expect(Object.isFrozen(cyclic)).toBe(true)
    expect(Object.isFrozen(bytes)).toBe(false)
    expect(Object.isFrozen(handle)).toBe(false)
  })

  it('leaves the bytes of a bulk read live while freezing the result around them', async () => {
    const result = await fs().readMany(['/a', '/b'])
    expect(result.files['/a']).toBeInstanceOf(Uint8Array)
    expect(Object.isFrozen(result.files['/a'])).toBe(false)
  })

  it('hands a watch callback a frozen event, which the callback cannot change', async () => {
    const received: { name: string }[] = []
    const writes: unknown[] = []
    let delivered!: () => void
    const done = new Promise<void>((resolve) => { delivered = resolve })
    const handle = await fs().watchDir('/', (event) => {
      try {
        // SAFETY: the write is the assertion; the event is frozen.
        (event as { name: string }).name = 'changed'
      } catch (error) {
        writes.push(error)
      }
      received.push(event)
      delivered()
    })
    await done
    await handle.stop()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toBeInstanceOf(TypeError)
    expect(received[0]?.name).toBe('a')
    expectDeeplyFrozen(received[0])
  })
})
