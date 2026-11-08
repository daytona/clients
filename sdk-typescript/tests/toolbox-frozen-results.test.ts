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
import { systemMetricsFromWire } from '../src/v2/toolboxModels'
import type * as toolbox from '../src/v2/gen/toolbox-types'

const CREATED_AT = '2026-01-01T00:00:00Z'
const FRAME = { cursor: 'c1', channel: 'stdout', data: 'hi', timestamp: 1 }
const RESULT_FRAME = { cursor: 'c2', channel: 'execute_result', data: '{"data":{"text/plain":"2","application/json":{"n":[1]}}}', timestamp: 2 }
const PAGE = { frames: [FRAME, RESULT_FRAME], nextCursor: 'c2', truncatedHead: false, eof: true }
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

/** The JSON body for a request, keyed by `METHOD path` (query dropped). */
function respond(request: IncomingMessage): unknown {
  const path = (request.url ?? '').split('?')[0]
  const routes: Record<string, unknown> = {
    'POST /processes': { ...RECORD, logs: PAGE },
    'GET /processes': { items: [RECORD] },
    'GET /processes/p': RECORD,
    'GET /processes/s': { ...RECORD, id: 's', kind: 'session', state: 'running', reason: undefined, exitCode: undefined },
    'GET /processes/p/wait': { reason: 'exited', exitCode: 0 },
    'GET /processes/p/logs': PAGE,
    'GET /files': { items: [FILE_INFO] },
    'GET /files/info': FILE_INFO,
    'GET /files/find': { items: [{ file: '/a', line: 1, content: 'x' }] },
    'GET /files/search': { files: ['/a'] },
    'POST /files/replace': { items: [{ file: '/a', success: true }] },
    'POST /files/bulk-upload': { files: [{ path: '/a', bytes: 1 }], errors: [] },
    'GET /git/status': { currentBranch: 'main', ahead: 0, behind: 0, branchPublished: true, fileStatus: [{ name: 'a', staging: 'M', worktree: ' ', extra: '' }] },
    'GET /git/remotes': { remotes: [{ name: 'origin', url: 'u' }] },
    'POST /git/commit': { hash: 'h' },
    'GET /git/branches': { branches: ['main'] },
    'GET /git/history': { items: [{ hash: 'h', message: 'm', author: 'a', email: 'e', timestamp: CREATED_AT }] },
    'POST /lsp/completions': { isIncomplete: false, items: [{ label: 'l', kind: 1, documentation: { kind: 'markdown', value: 'v' } }] },
    'GET /lsp/document-symbols': SYMBOLS,
    'GET /lsp/workspacesymbols': SYMBOLS,
    'GET /computeruse/status': { status: 'failed', desktop: 'failed', cause: { code: 'X', message: 'm', detail: { a: [1] } } },
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
  return routes[`${request.method ?? 'GET'} ${path}`] ?? {}
}

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

  beforeAll(async () => {
    server = createServer((request, response) => {
      request.resume()
      const path = (request.url ?? '').split('?')[0]
      if (path === '/processes/p/logs' && request.headers.accept === 'text/event-stream') {
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.end(`event: log\ndata: ${JSON.stringify(FRAME)}\n\nevent: state\ndata: ${JSON.stringify({ ...RECORD, cursor: 'c3' })}\n\nevent: eof\ndata: {"cursor":"c4"}\n\n`)
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
    transport = new HttpTransport(`http://127.0.0.1:${address.port}`, 'k', 5_000)
  })

  afterAll(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  const fs = () => new FileSystemService(INTERNAL, transport)
  const git = () => new GitService(INTERNAL, transport)
  const process = () => new ProcessService(INTERNAL, transport, 'sbx')
  const cu = () => new ComputerUseService(INTERNAL, transport)
  const lsp = async () => new LspService(INTERNAL, transport).start('python', '/p')

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
      for (const event of events) expectDeeplyFrozen(event)
      return events[0]
    }],
    ['session.run', () => process().connectSession('s').run('true')],
    ['session.get', () => process().connectSession('s').get()],
    ['pty.list', () => new ProcessService(INTERNAL, transport, 'sbx').list()],
    ['interpreter.run', () => new InterpreterService(INTERNAL, transport, 'sbx').run('1 + 1')],
    ['fs.list', () => fs().list('/d')],
    ['fs.stat', () => fs().stat('/d/a')],
    ['fs.grep', () => fs().grep('/d', 'x')],
    ['fs.glob', () => fs().glob('/d', '*')],
    ['fs.replace', () => fs().replace(['/a'], 'x', 'y')],
    ['fs.writeMany', () => fs().writeMany({ '/a': new Uint8Array([1]) })],
    ['fs.readMany', () => fs().readMany(['/a', '/b'])],
    ['git.status', () => git().status('/r')],
    ['git.listRemotes', () => git().listRemotes('/r')],
    ['git.commit', () => git().commit('/r', 'm', { author: 'a', email: 'e' })],
    ['git.listBranches', () => git().listBranches('/r')],
    ['git.history', () => git().history('/r')],
    ['lsp.completions', async () => (await lsp()).completions('u', { line: 0, character: 0 })],
    ['lsp.documentSymbols', async () => (await lsp()).documentSymbols('u')],
    ['lsp.workspaceSymbols', async () => (await lsp()).workspaceSymbols('s')],
    ['computerUse.status', () => cu().status()],
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
    ['sandbox.metrics (its mapper)', async () => systemMetricsFromWire({
      timestamp: CREATED_AT, cpuCount: 1, cpuUtilization: 0.5, memoryUsageBytes: 1, memoryTotalBytes: 2, diskUsageBytes: 1, diskTotalBytes: 2,
    } satisfies toolbox.Metrics)],
  ]

  it.each(rows)('%s', async (_name, call) => {
    expectDeeplyFrozen(await call())
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
