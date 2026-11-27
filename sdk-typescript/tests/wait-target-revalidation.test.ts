/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Scripted state sequences for the sandbox waits. A target state the events
// hub reports ends the wait with no read, except for a macOS start or resume:
// one read follows it to fill in the handle, and that read decides like any
// other: a target returns, a terminal state throws, and any other state keeps
// the wait going under the same deadline. `start()` that joins a start or
// resume another caller sent ends when that operation fails.
// The Python twin is tests/test_wait_target_revalidation.py.

import { WaitHub } from '../src/v2/events'
import type { WaitOutcome } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ConnectionError, RequestTimeoutError, SandboxTerminalStateError } from '../src/v2/errors/DaytonaError'
import type { SandboxInfo, SandboxState } from '../src/v2/types'
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

type Class = 'linux' | 'macos'

function detail(status: string, sandboxClass: Class): Record<string, unknown> {
  return {
    id: 'sbx', class: sandboxClass, status, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [],
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
 * Without `frames` the wait polls. A macOS handle knows its class before any
 * read; a Linux one learns it from its first read.
 */
function fixture(statuses: ReadonlyArray<string | Error>, frames?: readonly string[], onRead?: () => void, sandboxClass: Class = 'linux'): Fixture {
  const api = createHttpClient('http://api.invalid')
  const queue = [...statuses]
  const get = jest.spyOn(api, 'get').mockImplementation(async () => {
    onRead?.()
    const status = queue.length > 1 ? queue.shift()! : queue[0]!
    if (status instanceof Error) throw status
    return { data: detail(status, sandboxClass) }
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
  const source: SandboxInfo | string = sandboxClass === 'macos' ? { sandboxId: 'sbx', sandboxClass, rawSandboxClass: sandboxClass } : 'sbx'
  const sandbox = createSandbox(source, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  return { sandbox, get, post, terminalStates: () => terminalStates }
}

function readParams(get: jest.SpyInstance): unknown[] {
  return get.mock.calls.map((call: unknown[]) => (call[1] as { params?: unknown } | undefined)?.params)
}

it.each([
  { method: 'wait', statuses: [], frame: 'running', target: 'started' },
  { method: 'start', statuses: ['stopped'], frame: 'running', target: 'started' },
  { method: 'resume', statuses: [], frame: 'running', target: 'started' },
  { method: 'stop', statuses: [], frame: 'stopped', target: 'stopped' },
  { method: 'pause', statuses: [], frame: 'paused', target: 'paused' },
] as const)('$method ends at the target the events hub reports, with no read after it', async ({ method, statuses, frame, target }) => {
  // Every later read would show the wait's own target lagging: none is made.
  const { sandbox, get } = fixture([...statuses, 'deleting'], [frame])

  await (method === 'wait' ? sandbox.wait({ until: target, timeoutSeconds: 30 }) : sandbox[method]({ timeoutSeconds: 30 }))

  expect([sandbox.state, sandbox.rawState]).toEqual([target, frame])
  // Only start() reads the sandbox, before it sends anything.
  expect(get).toHaveBeenCalledTimes(statuses.length)
})

it('a macOS resume waits for the target when the read after a target event disagrees', async () => {
  const { sandbox, get } = fixture(['booting', 'booting', 'running'], ['running'], undefined, 'macos')

  await sandbox.resume({ timeoutSeconds: 30 })

  expect([sandbox.state, sandbox.rawState]).toEqual(['started', 'running'])
  // The first read fills in the handle; the ones after it are long polls for the target.
  const params = readParams(get)
  expect(params).toHaveLength(3)
  expect(params[0]).toBeUndefined()
  expect(params.slice(1)).toEqual([expect.objectContaining({ wait: 'running' }), expect.objectContaining({ wait: 'running' })])
})

it('a macOS resume throws the terminal state the read after a target event shows', async () => {
  const { sandbox, get } = fixture(['failed'], ['running'], undefined, 'macos')

  const error = await sandbox.resume({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(SandboxTerminalStateError)
  const terminal = error as SandboxTerminalStateError
  expect([terminal.state, terminal.rawState, terminal.reason]).toEqual(['error', 'failed', 'the guest crashed'])
  expect(get).toHaveBeenCalledTimes(1)
})

it('a macOS resume times out on the same deadline when the read after a target event never converges', async () => {
  const realNow = Date.now.bind(Date)
  let offset = 0
  jest.spyOn(Date, 'now').mockImplementation(() => realNow() + offset)
  // The clock moves one second on every read of a 5-second budget.
  const { sandbox, get } = fixture(['booting'], ['running'], () => { offset += 1000 }, 'macos')

  const error = await sandbox.resume({ timeoutSeconds: 5 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(RequestTimeoutError)
  expect((error as RequestTimeoutError).source).toBe('SDK')
  expect(sandbox.state).toBe('starting')
  // The budget is not restarted after the event.
  expect(get).toHaveBeenCalledTimes(5)
})

it('a macOS resume keeps the reported target when the read after it fails', async () => {
  const { sandbox, get } = fixture([new ConnectionError('refresh unavailable')], ['running'], undefined, 'macos')

  await sandbox.resume({ timeoutSeconds: 30 })

  expect([sandbox.state, sandbox.rawState]).toEqual(['started', 'running'])
  expect(get).toHaveBeenCalledTimes(1)
})

it.each([
  // start() reads the sandbox first; the read after the event lags behind it.
  { method: 'start', state: 'stopped', statuses: ['stopped', 'booting', 'running'], frame: 'running', target: 'started' },
  { method: 'resume', state: 'paused', statuses: ['booting', 'running'], frame: 'running', target: 'started' },
] as const)('a macOS $method revalidates the target the events hub reports', async ({ method, statuses, frame, target }) => {
  const { sandbox, get } = fixture(statuses, [frame], undefined, 'macos')

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

// Only a macOS start reads after a target event.
describe.each(PRE_OPERATION.filter((row) => row.method === 'start'))('macOS $method with a read after a target event showing $stale, the state before it', ({ method, before, frame, stale, target }) => {
  it('waits for the target', async () => {
    const { sandbox, get } = fixture([...before, stale, frame], [frame], undefined, 'macos')

    await sandbox[method]({ timeoutSeconds: 30 })

    expect(sandbox.state).toBe(target)
    const params = readParams(get).slice(before.length)
    expect(params).toHaveLength(2)
    expect(params[0]).toBeUndefined()
    expect(params[1]).toEqual(expect.objectContaining({ wait: frame }))
  })

  it('throws when the long poll shows the operation failed', async () => {
    const { sandbox } = fixture([...before, stale], [frame], undefined, 'macos')

    const error = await sandbox[method]({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

    expect(error).toBeInstanceOf(SandboxTerminalStateError)
    expect((error as SandboxTerminalStateError).rawState).toBe(stale)
  })
})

describe.each(PRE_OPERATION)('$method with a read showing $stale, the state before it', ({ method, before, frame, stale, target }) => {
  it('lets long polls decide when the hub still holds that state', async () => {
    const { sandbox, get } = fixture([...before, stale, frame], [stale])

    await sandbox[method]({ timeoutSeconds: 30 })

    expect(sandbox.state).toBe(target)
    expect(readParams(get).at(-1)).toEqual(expect.objectContaining({ wait: frame }))
  })

  it('throws through the long poll when the hub holds that state and every read confirms it', async () => {
    // The operation failed: the hub reports the state before it (for a pause,
    // `started`), and every read agrees. The confirming plain read does not
    // decide it; the long poll for the target does.
    const { sandbox, get } = fixture([...before, stale], [stale])

    const error = await sandbox[method]({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

    expect(error).toBeInstanceOf(SandboxTerminalStateError)
    expect((error as SandboxTerminalStateError).rawState).toBe(stale)
    const params = readParams(get).slice(before.length)
    expect(params).toEqual([undefined, expect.objectContaining({ wait: frame })])
  })
})

it('a macOS resume throws at once when the read after a target event shows a state no lag can show', async () => {
  const { sandbox, get } = fixture(['deleting'], ['running'], undefined, 'macos')
  // `deleting` reads as `destroying`.
  const error = await sandbox.resume({ timeoutSeconds: 30 }).catch((failure: unknown) => failure)

  expect(error).toBeInstanceOf(SandboxTerminalStateError)
  expect([(error as SandboxTerminalStateError).state, (error as SandboxTerminalStateError).rawState]).toEqual(['destroying', 'deleting'])
  expect(get).toHaveBeenCalledTimes(1)
})

it.each([
  // A pause whose events report still holds `started`, its failure state.
  { path: 'terminal report', method: 'pause', sandboxClass: 'linux', frame: 'running', statuses: ['running', 'paused'], target: 'paused' },
  // A macOS resume whose read after the target report still shows `paused`.
  { path: 'target report', method: 'resume', sandboxClass: 'macos', frame: 'running', statuses: ['paused', 'running'], target: 'started' },
] as const)('keeps the minimum spacing before the first long poll after a confirming read ($path)', async ({ method, sandboxClass, frame, statuses, target }) => {
  // The read that confirms an events report shows the state before the
  // operation, and polling takes over; its first long poll waits out the
  // minimum spacing.
  const stamps: number[] = []
  const { sandbox, get } = fixture(statuses, [frame], () => { stamps.push(performance.now()) }, sandboxClass)

  await sandbox[method]({ timeoutSeconds: 30 })

  expect(sandbox.state).toBe(target)
  expect(readParams(get)).toEqual([undefined, expect.objectContaining({ wait: statuses[1] })])
  expect(stamps[1]! - stamps[0]!).toBeGreaterThanOrEqual(190)
})
