/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Process waits are a series of bounded rounds, never one request held for the
// whole caller budget. sdk-python/tests/test_process_wait_rounds.py asserts
// the same behaviour.

import { INTERNAL } from '../src/v2/internal'
import { HttpTransport } from '../src/v2/http'
import { ClientTimeoutError, ConnectionError, ProcessService } from '../src/v2/index'
import { PROCESS_WAIT_ROUND_MS, WAIT_READ_SLACK_MS } from '../src/v2/ProcessWire'

const ROUND = 30_000

function processWire(state: 'running' | 'terminal', overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'proc', kind: 'exec', state, cwd: '/', stdin: 'closed', keepLogs: 'on_exit_ttl',
    createdAt: '2026-01-01T00:00:00Z', truncatedHead: false, ...overrides,
  }
}

const eofPage = (data = ''): Record<string, unknown> => ({
  frames: data === '' ? [] : [{ cursor: '1', channel: 'stdout', data, timestamp: 1 }],
  nextCursor: '1', truncatedHead: false, eof: true,
})

interface Call { readonly method: 'GET' | 'POST', readonly path: string, readonly timeout: number | undefined }

/**
 * Answers each request in order and moves a fake clock by the time the server
 * would hold it: a round's full `timeoutMs` / `waitMs` when it answers
 * `timed_out` / a running record; a failure at the request's own read bound.
 */
function holdingTransport(responses: unknown[]): { readonly transport: HttpTransport, readonly calls: Call[], readonly now: () => number } {
  let clock = 0
  const calls: Call[] = []
  const transport = new HttpTransport('http://toolbox.invalid')
  const answer = async (method: 'GET' | 'POST', path: string, config: unknown): Promise<unknown> => {
    const timeout = (config as { timeout?: number } | undefined)?.timeout
    calls.push({ method, path, timeout })
    const response = responses.shift()
    if (response instanceof Error) {
      clock += timeout ?? 0
      throw response
    }
    const hold = /[?&](?:timeoutMs|waitMs)=(\d+)/.exec(path)
    const held = typeof response === 'object' && response !== null &&
      ((response as { reason?: string }).reason === 'timed_out' || (response as { state?: string }).state === 'running')
    if (held && hold !== null) clock += Number(hold[1])
    return response
  }
  jest.spyOn(Date, 'now').mockImplementation(() => clock)
  jest.spyOn(transport, 'get').mockImplementation(async (path: string, config?: unknown) => answer('GET', path, config) as never)
  jest.spyOn(transport, 'post').mockImplementation(async (path: string, _body?: unknown, config?: unknown) => answer('POST', path, config) as never)
  return { transport, calls, now: () => clock }
}

const waits = (calls: Call[]): Call[] => calls.filter((call) => /\/wait\?/.test(call.path))

function expectBoundedRound(call: Call | undefined, holdMs: number): void {
  expect(call?.path).toMatch(new RegExp(`[?&](?:timeoutMs|waitMs)=${holdMs}(?:&|$)`))
  expect(call?.timeout).toBe(holdMs + WAIT_READ_SLACK_MS)
}

describe('process wait rounds', () => {
  afterEach(() => jest.restoreAllMocks())

  it('holds one round of 30 s', () => {
    expect(PROCESS_WAIT_ROUND_MS).toBe(ROUND)
  })

  it('waits out a long run() budget in bounded rounds', async () => {
    // run({ timeoutSeconds: 100 }) on a command that runs ~70 s: the start
    // request holds one 30 s round, then /wait rounds of at most 30 s.
    const { transport, calls } = holdingTransport([
      processWire('running'),
      { reason: 'timed_out' },
      processWire('running'),
      { reason: 'exited', exitCode: 0 },
      eofPage('done'),
    ])
    const result = await new ProcessService(INTERNAL, transport, 'sbx').run('sleep 70; printf done', { timeoutSeconds: 100 })
    expect(result).toMatchObject({ reason: 'exited', exitCode: 0, stdout: 'done' })
    expectBoundedRound(calls[0], ROUND)
    expect(waits(calls)).toHaveLength(2)
    for (const call of waits(calls)) expectBoundedRound(call, ROUND)
  })

  it('keeps the caller deadline across rounds', async () => {
    // A 70 s budget: 30 s on the start request, a 30 s round, then a last
    // round of only the 10 s that are left; then the call times out.
    const { transport, calls, now } = holdingTransport([
      processWire('running'),
      { reason: 'timed_out' },
      processWire('running'),
      { reason: 'timed_out' },
      processWire('running'),
      eofPage(),
    ])
    const result = await new ProcessService(INTERNAL, transport, 'sbx').run('sleep 600', { timeoutSeconds: 70 })
    expect(result.reason).toBe('timed_out')
    expect(waits(calls).map((call) => call.path)).toEqual(['/processes/proc/wait?timeoutMs=30000', '/processes/proc/wait?timeoutMs=10000'])
    expect(now()).toBe(70_000)
  })

  it.each([undefined, 0, 10 ** 9])('waits with no deadline (timeoutSeconds %p) as a series of bounded rounds', async (timeoutSeconds) => {
    const { transport, calls } = holdingTransport([
      { reason: 'timed_out' },
      processWire('running'),
      { reason: 'timed_out' },
      processWire('running'),
      { reason: 'exited', exitCode: 0 },
    ])
    const handle = new ProcessService(INTERNAL, transport, 'sbx').connect('proc')
    await expect(handle.wait(timeoutSeconds === undefined ? {} : { timeoutSeconds })).resolves.toMatchObject({ reason: 'exited', exitCode: 0 })
    expect(waits(calls)).toHaveLength(3)
    for (const call of waits(calls)) expectBoundedRound(call, ROUND)
  })

  it('retries a round the network dropped or stalled, within the budget', async () => {
    const { transport, calls } = holdingTransport([
      new ConnectionError('connection reset'),
      new ClientTimeoutError(),
      { reason: 'exited', exitCode: 0 },
    ])
    const handle = new ProcessService(INTERNAL, transport, 'sbx').connect('proc')
    await expect(handle.wait()).resolves.toMatchObject({ reason: 'exited', exitCode: 0 })
    expect(waits(calls)).toHaveLength(3)
  })

  it.each([undefined, 600])('bounds a stalled record read like a round and retries it (timeoutSeconds %p)', async (timeoutSeconds) => {
    // After a `timed_out` round the wait reads the record; a dropped read
    // costs one round (35 s), never the rest of the budget, and the wait goes
    // on to the exit.
    const { transport, calls, now } = holdingTransport([
      { reason: 'timed_out' },
      new ClientTimeoutError(),
      { reason: 'exited', exitCode: 0 },
    ])
    const handle = new ProcessService(INTERNAL, transport, 'sbx').connect('proc')
    await expect(handle.wait(timeoutSeconds === undefined ? {} : { timeoutSeconds })).resolves.toMatchObject({ reason: 'exited', exitCode: 0 })
    expect(calls[1]).toEqual({ method: 'GET', path: '/processes/proc', timeout: ROUND + WAIT_READ_SLACK_MS })
    expect(now()).toBe(ROUND + ROUND + WAIT_READ_SLACK_MS)
  })

  it('reports a process that ended with the round that spends the budget', async () => {
    // The last round answers `timed_out`; the record read after it finds the
    // process ended (here by its own execution deadline), and that result is
    // returned rather than the wait's `timed_out`.
    const { transport, calls } = holdingTransport([
      { reason: 'timed_out' },
      processWire('terminal', { reason: 'timed_out', exitCode: 124, signal: 'SIGTERM' }),
    ])
    const handle = new ProcessService(INTERNAL, transport, 'sbx').connect('proc')
    await expect(handle.wait({ timeoutSeconds: 10 })).resolves.toMatchObject({ reason: 'timed_out', exitCode: 124, signal: 'TERM' })
    expect(calls[1]).toEqual({ method: 'GET', path: '/processes/proc', timeout: WAIT_READ_SLACK_MS })
  })

  it.each([new ConnectionError('connection reset'), new ClientTimeoutError()])('never retries the start request of run() (%p)', async (failure) => {
    // The process may have started before the start request failed, so a
    // retry could run the command twice: the failure is raised as is.
    const { transport, calls } = holdingTransport([failure])
    await expect(new ProcessService(INTERNAL, transport, 'sbx').run('make deploy', { timeoutSeconds: 600 })).rejects.toBe(failure)
    expect(calls.map((call) => `${call.method} ${call.path.split('?')[0]}`)).toEqual(['POST /processes'])
    expectBoundedRound(calls[0], ROUND)
  })

  it('holds only a budget shorter than one round', async () => {
    const { transport, calls } = holdingTransport([{ reason: 'exited', exitCode: 0 }])
    await new ProcessService(INTERNAL, transport, 'sbx').connect('proc').wait({ timeoutSeconds: 1.5 })
    expectBoundedRound(calls[0], 1_500)
  })

  it('bounds the start request of a session run the same way', async () => {
    const { transport, calls } = holdingTransport([
      processWire('terminal', { reason: 'exited', exitCode: 0, logs: eofPage('ok') }),
    ])
    const session = new ProcessService(INTERNAL, transport, 'sbx').connectSession('shell-1')
    await expect(session.run('true', { timeoutSeconds: 600 })).resolves.toMatchObject({ stdout: 'ok' })
    expectBoundedRound(calls[0], ROUND)
  })
})
