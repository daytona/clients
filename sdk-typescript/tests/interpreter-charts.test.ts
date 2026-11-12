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
import { CursorExpiredError, createDaytonaError } from '../src/v2/errors/DaytonaError'
import { INTERNAL } from '../src/v2/internal'
import type { HttpTransport } from '../src/v2/http'
import type { LogFrame } from '../src/v2/types'

interface RunExpectation {
  readonly isMainResult: boolean
  readonly dataKeys: readonly string[]
  readonly chartType: string | null
  readonly bars?: number
  readonly text?: string
  readonly png?: string
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

function frame(cursor: string, channel: string, data: Record<string, unknown>) {
  return { cursor, channel, timestamp: 1, data: JSON.stringify(data) }
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
