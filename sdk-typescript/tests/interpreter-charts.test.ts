/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Interpreter charts: a matplotlib figure's data next to its image. The cases
// are shared with the Python SDK's tests/test_interpreter_charts.py
// (sdk-python/tests/fixtures/interpreter-charts.json): the same display frames
// must give the same charts and results in both SDKs.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { chartFromWire } from '../src/v2/charts'
import type { BarChart, Chart } from '../src/v2/charts'
import { InterpreterService, interpreterOutputs } from '../src/v2/Interpreter'
import { ProcessService } from '../src/v2/Process'
import type { ProcessHandle } from '../src/v2/ProcessHandle'
import { TIMED_OUT_LOG_PAGE_LIMIT, collectAllLogs } from '../src/v2/ProcessRun'
import { CursorExpiredError, createDaytonaError } from '../src/v2/errors/DaytonaError'
import { INTERNAL } from '../src/v2/internal'
import type { HttpTransport } from '../src/v2/http'
import type { LogFrame, LogPage } from '../src/v2/types'

interface RunExpectation {
  readonly isMainResult: boolean
  readonly dataKeys: readonly string[]
  readonly chartType: string | null
  readonly bars?: number
  readonly text?: string
  readonly png?: string
  readonly rawChart?: unknown
}

const fixture = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'interpreter-charts.json'), 'utf8')) as {
  charts: { name: string; wire: unknown; expected: unknown }[]
  runs: {
    name: string
    frames: { channel: string; data: string }[]
    expected: { results: RunExpectation[]; charts: string[] }
  }[]
}
const CHART = 'application/vnd.daytona.chart.v1+json'
const DISPLAY = 'application/vnd.daytona.display.v1+json'

describe('interpreter charts', () => {
  it.each(fixture.charts.map((entry) => [entry.name, entry] as const))('%s', (_name, entry) => {
    const chart = chartFromWire(entry.wire)
    if (entry.expected === null) expect(chart).toBeUndefined()
    else expect(chart).toStrictEqual(entry.expected)
  })

  it.each(fixture.runs.map((entry) => [entry.name, entry] as const))('run: %s', (_name, entry) => {
    const frames: LogFrame[] = entry.frames.map((frame, index) => ({ ...frame, cursor: String(index), timestampMs: index }))
    const results = interpreterOutputs(frames)
    expect(results).toHaveLength(entry.expected.results.length)
    results.forEach((output, index) => {
      const want = entry.expected.results[index]!
      expect(output.isMainResult).toBe(want.isMainResult)
      expect(Object.keys(output.data).sort()).toEqual(want.dataKeys)
      expect(output.chart?.type ?? null).toBe(want.chartType)
      if (want.bars !== undefined) expect((output.chart as BarChart).elements).toHaveLength(want.bars)
      if (want.text !== undefined) expect(output.text).toBe(want.text)
      if (want.png !== undefined) expect(output.png).toBe(want.png)
      if (want.rawChart !== undefined) expect(output.data[CHART]).toStrictEqual(want.rawChart)
    })
    const charts = results.flatMap((output) => (output.chart === undefined ? [] : [output.chart]))
    expect(charts.map((chart) => chart.type)).toEqual(entry.expected.charts)
  })

  it('treats a chart type this release does not know as an unknown chart, never an error', () => {
    const chart: Chart | undefined = chartFromWire({ type: 'sankey', flows: [] })
    expect(chart).toStrictEqual({ type: 'unknown', rawType: 'sankey', truncated: false })
  })
})

function transportStub(responses: unknown[]) {
  const requests: string[] = []
  const answer = (method: string) => async (path: string, config?: { params?: Record<string, unknown> }) => {
    const cursor = config?.params?.cursor
    requests.push(`${method} ${path}${typeof cursor === 'string' ? `?cursor=${cursor}` : ''}`)
    const response = responses.shift()
    if (response instanceof Error) throw response
    return response
  }
  const object = { get: answer('GET'), post: answer('POST'), delete: answer('DELETE'), maxResponseBytes: 0 }
  // SAFETY: the interpreter's run path calls only get/post/delete, which the stub provides.
  return { transport: object as unknown as HttpTransport, requests }
}

const PROCESS = {
  id: 'p1', kind: 'code', state: 'running', cwd: '/', stdin: 'closed', keepLogs: 'on_exit_ttl',
  createdAt: '2026-01-01T00:00:00Z', truncatedHead: false, language: 'python',
}

function frame(cursor: string, channel: string, data: Record<string, unknown> | string) {
  return { cursor, channel, timestamp: 1, data: typeof data === 'string' ? data : JSON.stringify(data) }
}

describe('interpreter runs with charts', () => {
  it('returns its charts and the final state of each display, frozen', async () => {
    const one = { type: 'bar', orientation: 'vertical', elements: [{ label: 'a', value: 1 }] }
    const two = { type: 'bar', orientation: 'vertical', elements: [{ label: 'a', value: 1 }, { label: 'b', value: 2 }] }
    const { transport } = transportStub([
      PROCESS,
      { exitCode: 0, reason: 'exited' },
      {
        frames: [
          frame('1', 'execute_result', { 'text/plain': '<Axes: >' }),
          frame('2', 'display', { 'image/png': 'A', [CHART]: one, [DISPLAY]: { displayId: 'd', update: false } }),
          frame('3', 'display', { 'image/png': 'B', [CHART]: two, [DISPLAY]: { displayId: 'd', update: true } }),
        ],
        nextCursor: '3', truncatedHead: false, eof: true,
      },
    ])

    const result = await new InterpreterService(INTERNAL, transport, 'sbx').run('df.plot()')

    expect(result.results.map((output) => output.text)).toEqual(['<Axes: >', undefined])
    const figure = result.results[1]!
    expect(figure.png).toBe('B')
    expect(DISPLAY in figure.data).toBe(false)
    expect((figure.chart as BarChart).elements.map((bar) => bar.label)).toEqual(['a', 'b'])
    expect(result.charts).toEqual([figure.chart])
    expect(result.charts![0]).toBe(figure.chart)
    expect(Object.isFrozen((figure.chart as BarChart).elements[1])).toBe(true)
    expect(Object.isFrozen(result.charts)).toBe(true)
  })

  it('returns what the sandbox kept when the head of its log was evicted', async () => {
    const expired = createDaytonaError('the requested position was evicted by log retention', 409, 'CURSOR_EXPIRED', 'DAYTONA_DAEMON', {
      firstAvailableCursor: 'c9',
    })
    expect(expired).toBeInstanceOf(CursorExpiredError)
    const { transport, requests } = transportStub([
      PROCESS,
      { exitCode: 0, reason: 'exited' },
      expired,
      { frames: [frame('c10', 'display', { 'text/plain': '<Figure>', [CHART]: { type: 'unknown' } })], nextCursor: 'c10', truncatedHead: false, eof: true },
    ])

    const result = await new InterpreterService(INTERNAL, transport, 'sbx').run('plot()')

    expect(result.charts?.map((chart) => chart.type)).toEqual(['unknown'])
    expect(requests.slice(-2)).toEqual(['GET /processes/p1/logs', 'GET /processes/p1/logs?cursor=c9'])
  })

  it('still fails when an expired read names no resume point', async () => {
    const expired = createDaytonaError('evicted', 409, 'CURSOR_EXPIRED', 'DAYTONA_DAEMON')
    const { transport } = transportStub([PROCESS, { exitCode: 0, reason: 'exited' }, expired])
    await expect(new InterpreterService(INTERNAL, transport, 'sbx').run('plot()')).rejects.toBeInstanceOf(CursorExpiredError)
  })
})

describe('log reads after retention', () => {
  function expired(resume?: string) {
    return createDaytonaError('the requested position was evicted by log retention', 409, 'CURSOR_EXPIRED', 'DAYTONA_DAEMON', {
      ...(resume === undefined ? {} : { firstAvailableCursor: resume }),
    })
  }

  /** A process handle whose log reads answer from a queue: an error throws, anything else is the page. */
  function expiringLogs(outcomes: (Error | LogPage)[]) {
    const cursors: (string | undefined)[] = []
    const logs = async (options: { cursor?: string } = {}): Promise<LogPage> => {
      cursors.push(options.cursor)
      const outcome = outcomes.shift()
      if (outcome === undefined) throw new Error('no more pages')
      if (outcome instanceof Error) throw outcome
      return outcome
    }
    // SAFETY: collectAllLogs calls only `logs` on the handle, which this provides.
    return { handle: { logs } as unknown as ProcessHandle, cursors }
  }

  it('stops a bounded read whose pages keep expiring at its page budget', async () => {
    // A timed-out run reads at most TIMED_OUT_LOG_PAGE_LIMIT pages; an expired
    // read is one of them, so retention racing a live process cannot loop.
    const { handle, cursors } = expiringLogs(Array.from({ length: 49 }, (_, i) => expired(`c${i + 1}`)))
    await expect(collectAllLogs(handle, { maxPages: TIMED_OUT_LOG_PAGE_LIMIT })).resolves.toEqual([])
    expect(cursors).toEqual([undefined, ...Array.from({ length: TIMED_OUT_LOG_PAGE_LIMIT - 1 }, (_, i) => `c${i + 1}`)])
  })

  it('fails when an expiry names the cursor just read', async () => {
    const { handle, cursors } = expiringLogs([expired('c5'), expired('c5')])
    await expect(collectAllLogs(handle)).rejects.toBeInstanceOf(CursorExpiredError)
    expect(cursors).toEqual([undefined, 'c5'])
  })

  it('fails when an expiry names a resume point already read from', async () => {
    // A sandbox answering A with "resume at B" and B with "resume at A" must
    // not keep a read going forever: retention only moves forward.
    const { handle, cursors } = expiringLogs([expired('cB'), expired('cA'), expired('cB'), expired('cA')])
    await expect(collectAllLogs(handle)).rejects.toBeInstanceOf(CursorExpiredError)
    expect(cursors).toEqual([undefined, 'cB', 'cA'])
  })

  it('returns what the sandbox kept from a process run whose log head was evicted', async () => {
    // The inline page is not the end, so run() reads on from it; retention
    // evicted what followed, and the inline frames are kept.
    const { transport, requests } = transportStub([
      {
        ...PROCESS,
        kind: 'exec',
        language: undefined,
        state: 'terminal',
        exitCode: 0,
        reason: 'exited',
        logs: { frames: [frame('c1', 'stdout', 'early')], nextCursor: 'c1', truncatedHead: false, eof: false },
      },
      expired('c7'),
      { frames: [frame('c8', 'stdout', 'late')], nextCursor: 'c8', truncatedHead: false, eof: true },
    ])

    const result = await new ProcessService(INTERNAL, transport, 'sbx').run('echo')

    expect(result.stdout).toBe('earlylate')
    expect(requests.slice(-2)).toEqual(['GET /processes/p1/logs?cursor=c1', 'GET /processes/p1/logs?cursor=c7'])
  })

  it('keeps a page in hand and counts it as a page', async () => {
    const page = (i: number, eof = false): LogPage => ({
      items: [{ cursor: `c${i}`, channel: 'stdout', data: `p${i}`, timestampMs: i }],
      nextCursor: `c${i}`,
      truncatedHead: false,
      eof,
    })
    let { handle, cursors } = expiringLogs(Array.from({ length: 9 }, (_, i) => page(i + 1)))
    let logs = await collectAllLogs(handle, { maxPages: 3, first: page(0) })
    expect(logs.map((frame) => frame.data)).toEqual(['p0', 'p1', 'p2'])
    expect(cursors).toEqual(['c0', 'c1'])
    // An empty page in hand carries nothing to keep: the read starts over.
    ;({ handle, cursors } = expiringLogs([page(0, true)]))
    logs = await collectAllLogs(handle, { first: { items: [], nextCursor: 'c9', truncatedHead: false, eof: false } })
    expect(logs.map((frame) => frame.data)).toEqual(['p0'])
    expect(cursors).toEqual([undefined])
    // A page in hand costs no request: a budget that allows none keeps it.
    ;({ handle, cursors } = expiringLogs([]))
    logs = await collectAllLogs(handle, { maxPages: 0, first: page(0) })
    expect(logs.map((frame) => frame.data)).toEqual(['p0'])
    expect(cursors).toEqual([])
    // A page in hand without a cursor to go on from: the read starts over,
    // and each frame comes back once.
    ;({ handle, cursors } = expiringLogs([page(0, true)]))
    logs = await collectAllLogs(handle, { first: { ...page(0), nextCursor: '' } })
    expect(logs.map((frame) => frame.data)).toEqual(['p0'])
    expect(cursors).toEqual([undefined])
  })
})
