/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Scripted state sequences for the sandbox waits. A target state the events
// hub reports is followed by one read that fills in the handle, and that read
// decides like any other: a target returns, a terminal state throws, and any
// other state keeps the wait going under the same deadline. `start()` that
// joins a start or resume another caller sent ends when that operation fails.
// The Python twin is tests/test_wait_target_revalidation.py.

import { WaitHub } from '../src/v2/events'
import type { WaitOutcome } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ConnectionError, RequestTimeoutError, SandboxTerminalStateError } from '../src/v2/errors/DaytonaError'
import type { SandboxState } from '../src/v2/types'
import { createSandbox } from './helpers/sandbox'

const PUBLIC: Record<string, SandboxState> = {
  pending: 'creating',
  booting: 'starting',
  running: 'started',
  stopping: 'stopping',
  stopped: 'stopped',
  pausing: 'pausing',
  paused: 'paused',
  failed: 'error',
  deleting: 'destroying',
}

const eventsSetting = process.env.DAYTONA_SANDBOX_EVENTS
beforeEach(() => { delete process.env.DAYTONA_SANDBOX_EVENTS })
afterEach(() => {
  if (eventsSetting === undefined) delete process.env.DAYTONA_SANDBOX_EVENTS
  else process.env.DAYTONA_SANDBOX_EVENTS = eventsSetting
  jest.restoreAllMocks()
})

function detail(status: string): Record<string, unknown> {
  return {
    id: 'sbx', class: 'linux', status, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [],
    ...(status === 'failed' ? { error: 'the guest crashed' } : {}),
  }
}

interface Fixture {
  readonly sandbox: ReturnType<typeof createSandbox>
  readonly get: jest.SpyInstance
  readonly post: jest.SpyInstance
  /** The terminal states the hub was asked to reject on. */
  readonly terminalStates: () => ReadonlySet<SandboxState>
}

/**
 * A sandbox whose reads answer the scripted statuses (the last one repeats)
 * and whose events hub delivers `frames` in order, deciding as the real hub
 * does; a script that decides nothing ends the hub's share of the budget.
 * Without `frames` the wait polls.
 */
function fixture(statuses: ReadonlyArray<string | Error>, frames?: readonly string[], onRead?: () => void): Fixture {
  const api = createHttpClient('http://api.invalid')
  const queue = [...statuses]
  const get = jest.spyOn(api, 'get').mockImplementation(async () => {
    onRead?.()
    const status = queue.length > 1 ? queue.shift()! : queue[0]!
    if (status instanceof Error) throw status
    return { data: detail(status) }
  })
  const post = jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
  let terminalStates: ReadonlySet<SandboxState> = new Set()
  let hub: WaitHub | undefined
  if (frames !== undefined) {
    hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
    jest.spyOn(hub, 'wait').mockImplementation(async (id, predicate, options) => {
      terminalStates = predicate.terminalStates
      for (const raw of frames) {
        const frame: WaitOutcome = { state: PUBLIC[raw]!, rawState: raw }
        if (predicate.states.has(frame.state)) return frame
        if (predicate.terminalStates.has(frame.state)) throw new SandboxTerminalStateError(id, frame.state, { rawState: raw })
        options?.onObserved?.(frame)
      }
      throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    })
  }
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  return { sandbox, get, post, terminalStates: () => terminalStates }
}

function readParams(get: jest.SpyInstance): unknown[] {
  return get.mock.calls.map((call: unknown[]) => (call[1] as { params?: unknown } | undefined)?.params)
}

it('waits for the target when the read after a target event disagrees', async () => {
  const { sandbox, get } = fixture(['pending', 'pending', 'running'], ['running'])

  await sandbox.wait({ until: 'started', timeoutSeconds: 30 })

  expect([sandbox.state, sandbox.rawState]).toEqual(['started', 'running'])
  // The first read fills in the handle; the ones after it are long polls for the target.
  const params = readParams(get)
  expect(params).toHaveLength(3)
  expect(params[0]).toBeUndefined()
  expect(params.slice(1)).toEqual([expect.objectContaining({ wait: 'running' }), expect.objectContaining({ wait: 'running' })])
})

it('throws the terminal state the read after a target event shows', async () => {
  const { sandbox, get } = fixture(['failed'], ['running'])

  const error = await sandbox.wait({ until: 'started', timeoutSeconds: 30 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(SandboxTerminalStateError)
  const terminal = error as SandboxTerminalStateError
  expect([terminal.state, terminal.rawState, terminal.reason]).toEqual(['error', 'failed', 'the guest crashed'])
  expect(get).toHaveBeenCalledTimes(1)
})

it('times out on the same deadline when the read after a target event never converges', async () => {
  const realNow = Date.now.bind(Date)
  let offset = 0
  jest.spyOn(Date, 'now').mockImplementation(() => realNow() + offset)
  // The clock moves one second on every read of a 5-second budget.
  const { sandbox, get } = fixture(['pending'], ['running'], () => { offset += 1000 })

  const error = await sandbox.wait({ until: 'started', timeoutSeconds: 5 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(RequestTimeoutError)
  expect((error as RequestTimeoutError).source).toBe('SDK')
  expect(sandbox.state).toBe('creating')
  // The budget is not restarted after the event.
  expect(get).toHaveBeenCalledTimes(5)
})

it('keeps the reported target when the read after it fails', async () => {
  const { sandbox, get } = fixture([new ConnectionError('refresh unavailable')], ['running'])

  await sandbox.wait({ until: 'started', timeoutSeconds: 30 })

  expect([sandbox.state, sandbox.rawState]).toEqual(['started', 'running'])
  expect(get).toHaveBeenCalledTimes(1)
})

it.each([
  // start() reads the sandbox first; the read after the event lags behind it.
  { method: 'start', state: 'stopped', statuses: ['stopped', 'booting', 'running'], frame: 'running', target: 'started' },
  { method: 'resume', state: 'paused', statuses: ['booting', 'running'], frame: 'running', target: 'started' },
  { method: 'stop', state: 'started', statuses: ['stopping', 'stopped'], frame: 'stopped', target: 'stopped' },
  { method: 'pause', state: 'started', statuses: ['pausing', 'paused'], frame: 'paused', target: 'paused' },
] as const)('$method revalidates the target the events hub reports', async ({ method, statuses, frame, target }) => {
  const { sandbox, get } = fixture(statuses, [frame])

  await sandbox[method]({ timeoutSeconds: 30 })

  expect(sandbox.state).toBe(target)
  // Every scripted read was served: the lagging read did not end the wait.
  expect(get).toHaveBeenCalledTimes(statuses.length)
})

describe.each([
  { path: 'events', events: true },
  { path: 'polling', events: false },
])('start() that joins a start ($path)', ({ events }) => {
  it.each([
    { failed: 'stopped', state: 'stopped' },
    { failed: 'paused', state: 'paused' },
  ] as const)('throws when the joined operation ends $state', async ({ failed, state }) => {
    // The first read finds the sandbox starting: another caller sent the start
    // or resume. It fails, and the sandbox goes back to where it was.
    const { sandbox, post, terminalStates } = fixture(['booting', 'booting', failed], events ? ['booting', failed] : undefined)

    const error = await sandbox.start({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

    expect(error).toBeInstanceOf(SandboxTerminalStateError)
    expect([(error as SandboxTerminalStateError).state, (error as SandboxTerminalStateError).rawState]).toEqual([state, failed])
    // Nothing is sent: the start is joined, never repeated.
    expect(post).not.toHaveBeenCalled()
    if (events) expect([...terminalStates()]).toEqual(expect.arrayContaining(['stopped', 'paused']))
  })

  it('returns when the joined operation succeeds', async () => {
    const { sandbox, post } = fixture(['booting', 'booting', 'running'], events ? ['booting', 'running'] : undefined)

    await sandbox.start({ timeoutSeconds: 30 })

    expect(sandbox.state).toBe('started')
    expect(post).not.toHaveBeenCalled()
  })
})

it('start() of a stopped sandbox waits through a read that still shows it stopped', async () => {
  // The start is accepted, but the first read still shows the sandbox
  // stopped: that is no failure of the start this call sent.
  const { sandbox, post } = fixture(['stopped', 'stopped', 'booting', 'running'])

  await sandbox.start({ timeoutSeconds: 30 })

  expect(sandbox.state).toBe('started')
  expect(post).toHaveBeenCalledWith('/sandboxes/sbx/start')
})

// A read can lag back to the state the sandbox was in before the operation,
// and for a pause or a joined start that state is also the operation's own
// failure state. Only long-poll reads decide it.
const PRE_OPERATION = [
  { method: 'pause', before: [], frame: 'paused', stale: 'running', target: 'paused' },
  { method: 'start', before: ['booting'], frame: 'running', stale: 'stopped', target: 'started' },
  { method: 'start', before: ['booting'], frame: 'running', stale: 'paused', target: 'started' },
] as const

describe.each(PRE_OPERATION)('$method with a read showing $stale, the state before it', ({ method, before, frame, stale, target }) => {
  it('waits for the target when that read follows a target event', async () => {
    const { sandbox, get } = fixture([...before, stale, frame], [frame])

    await sandbox[method]({ timeoutSeconds: 30 })

    expect(sandbox.state).toBe(target)
    const params = readParams(get).slice(before.length)
    expect(params).toHaveLength(2)
    expect(params[0]).toBeUndefined()
    expect(params[1]).toEqual(expect.objectContaining({ wait: frame }))
  })

  it('lets long polls decide when the hub still holds that state', async () => {
    const { sandbox, get } = fixture([...before, stale, frame], [stale])

    await sandbox[method]({ timeoutSeconds: 30 })

    expect(sandbox.state).toBe(target)
    expect(readParams(get).at(-1)).toEqual(expect.objectContaining({ wait: frame }))
  })

  it('throws when the long poll shows the operation failed', async () => {
    const { sandbox, get } = fixture([...before, stale], [frame])

    const error = await sandbox[method]({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

    expect(error).toBeInstanceOf(SandboxTerminalStateError)
    expect((error as SandboxTerminalStateError).rawState).toBe(stale)
    expect(readParams(get).at(-1)).toEqual(expect.objectContaining({ wait: frame }))
  })
})

it('throws at once when the read after a target event shows a state no lag can show', async () => {
  const { sandbox, get } = fixture(['deleting'], ['paused'])
  // `deleting` reads as `destroying`.
  const error = await sandbox.pause({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(SandboxTerminalStateError)
  expect([(error as SandboxTerminalStateError).state, (error as SandboxTerminalStateError).rawState]).toEqual(['destroying', 'deleting'])
  expect(get).toHaveBeenCalledTimes(1)
})
