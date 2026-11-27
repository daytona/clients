/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { WaitHub } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ConnectionError, NotFoundError, RequestTimeoutError, SandboxTerminalStateError } from '../src/v2/errors/DaytonaError'
import type { SandboxInfo } from '../src/v2/types'
import { createSandbox } from './helpers/sandbox'

const eventsSetting = process.env.DAYTONA_SANDBOX_EVENTS
beforeEach(() => { delete process.env.DAYTONA_SANDBOX_EVENTS })
afterEach(() => {
  if (eventsSetting === undefined) delete process.env.DAYTONA_SANDBOX_EVENTS
  else process.env.DAYTONA_SANDBOX_EVENTS = eventsSetting
  jest.restoreAllMocks()
  jest.useRealTimers()
})

// Only a macOS start or resume reads the sandbox after the events hub reports
// its target (the boot mode and the reservation change); the other waits read
// nothing after it (tests/wait-target-revalidation.test.ts).
const MACOS: SandboxInfo = { sandboxId: 'sbx', sandboxClass: 'macos', rawSandboxClass: 'macos' }

function macosDetail(status: string, extra: { readonly mac_boot_mode?: string } = {}) {
  return { data: { id: 'sbx', class: 'macos', status, name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], ...extra } }
}

it.each(['start', 'resume'] as const)('preserves observed started when the final macOS %s metadata refresh fails', async (method) => {
  // Given an event-backed target observation and an unavailable detail read.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const get = jest.spyOn(api, 'get')
  if (method === 'start') get.mockResolvedValueOnce(macosDetail('stopped'))
  get.mockRejectedValue(new ConnectionError('refresh unavailable'))
  const sandbox = createSandbox(MACOS, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the lifecycle wait observes its target.
  await sandbox[method]()
  // Then the observed success survives a failed refresh without retry.
  expect(sandbox.state).toBe('started')
  expect(get).toHaveBeenCalledTimes(method === 'start' ? 2 : 1)
})

it('keeps the state a successful macOS refresh reads after the hub observed the target', async () => {
  // Given an event-backed target observation and a detail read showing a later state.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
  jest.spyOn(api, 'get')
    .mockResolvedValueOnce(macosDetail('stopping'))
    .mockResolvedValue(macosDetail('running', { mac_boot_mode: 'warm' }))
  const sandbox = createSandbox(MACOS, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the resume's wait resolves and the refresh succeeds.
  await sandbox.resume()
  // Then the handle reports what the server said last, not the event-time
  // state; a read outside the targets kept the wait going until one showed a target.
  expect([sandbox.state, sandbox.rawState, sandbox.macBootMode]).toEqual(['started', 'running', 'warm'])
})

it.each([
  { path: 'same state', before: { state: 'error', rawState: 'failed', error: 'the guest crashed' }, kept: 'the guest crashed' },
  { path: 'other wire status', before: { state: 'error', rawState: 'lost', error: 'the host went away' }, kept: undefined },
  { path: 'other state', before: { state: 'started', rawState: 'running', error: 'an earlier failure' }, kept: undefined },
] as const)('a terminal report keeps only an error message read with its state ($path)', async ({ before, kept }) => {
  // Given a report of `error` with no message, and a read that cannot confirm it.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockRejectedValue(new SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
  jest.spyOn(api, 'get').mockRejectedValue(new ConnectionError('refresh unavailable'))
  const sandbox = createSandbox({ sandboxId: 'sbx', sandboxClass: 'linux', ...before }, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the wait ends on the report.
  await expect(sandbox.wait({ timeoutSeconds: 5 })).rejects.toBeInstanceOf(SandboxTerminalStateError)
  // Then a message read with another state never labels it.
  expect([sandbox.state, sandbox.rawState, sandbox.error]).toEqual(['error', 'failed', kept])
})

it('a 404 in a delete drops an error message read with another state', async () => {
  // Given a reported `error` whose confirming read finds the sandbox gone.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockRejectedValue(new SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
  jest.spyOn(api, 'delete').mockResolvedValue({ data: {}, headers: {} })
  jest.spyOn(api, 'get').mockRejectedValue(new NotFoundError('gone', { statusCode: 404, code: 'NOT_FOUND' }))
  const sandbox = createSandbox({ sandboxId: 'sbx', sandboxClass: 'linux', state: 'started', rawState: 'running', error: 'an earlier failure' }, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the delete completes.
  await sandbox.delete({ timeoutSeconds: 5 })
  // Then the handle is destroyed with no message.
  expect([sandbox.state, sandbox.rawState, sandbox.error]).toEqual(['destroyed', 'deleted', undefined])
})

it.each(['linux', 'macos'] as const)('a %s resume whose class a read during the wait learned reads after the target only for macOS', async (sandboxClass) => {
  // Given a handle built from an id alone, and an events wait whose probe reads the class.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockImplementation(async (_id, _predicate, options) => {
    await options!.sandboxPollFn!()
    const frame = { state: 'started', rawState: 'running' } as const
    options!.onObserved!(frame)
    return frame
  })
  jest.spyOn(api, 'post').mockResolvedValue({ data: {}, headers: {} })
  const get = jest.spyOn(api, 'get')
    .mockResolvedValueOnce({ data: { id: 'sbx', class: sandboxClass, status: 'booting', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
    .mockResolvedValue({ data: { id: 'sbx', class: sandboxClass, status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  expect(sandbox.sandboxClass).toBeUndefined()
  // When the resume's wait ends on the events report.
  await sandbox.resume({ timeoutSeconds: 30 })
  // Then the probe, and for macOS one read after the report.
  expect(sandbox.state).toBe('started')
  expect(get).toHaveBeenCalledTimes(sandboxClass === 'macos' ? 2 : 1)
})

it('does not refresh or time out when the hub observes its target at the deadline', async () => {
  // Given an event arriving exactly at the caller's deadline.
  jest.useFakeTimers({ now: 0 })
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockImplementation(async () => {
    jest.setSystemTime(1000)
    return { state: 'started', rawState: 'running' }
  })
  const get = jest.spyOn(api, 'get')
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When waiting for the observed target.
  const result = await sandbox.wait({ timeoutSeconds: 1 })
  // Then target observation wins over budget exhaustion.
  expect(result.state).toBe('started')
  expect(get).not.toHaveBeenCalled()
})

it('propagates programming errors from the post-observation macOS refresh', async () => {
  // Given a successful state observation followed by a bug in the refresh.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const error = new TypeError('bug')
  jest.spyOn(api, 'get').mockRejectedValue(error)
  const sandbox = createSandbox(MACOS, api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When refreshing observed metadata.
  const pending = sandbox.resume()
  // Then a programming error is not hidden as a transport failure.
  await expect(pending).rejects.toBe(error)
})

it('holds a sandbox wait for its budget, not the shorter request timeout', async () => {
  // Given a shorter request timeout than the sandbox wait budget.
  const api = createHttpClient('http://api.invalid', undefined, 20)
  const get = jest.spyOn(api, 'get').mockRejectedValue(new RequestTimeoutError('server timeout'))
  const sandbox = createSandbox('sbx', api)
  // When the wait sends a long-poll request.
  const pending = sandbox.wait({ timeoutSeconds: 2 })
  // Then the hold and the read are the wait budget, never the 20 ms request timeout.
  await expect(pending).rejects.toBeInstanceOf(RequestTimeoutError)
  const [, config] = get.mock.calls[0] ?? []
  const hold: unknown = (config?.params as { readonly timeout_ms?: unknown } | undefined)?.timeout_ms
  expect(hold).toBeGreaterThan(1_000)
  expect(hold).toBeLessThanOrEqual(2_000)
  expect(config?.timeout).toBeGreaterThan(1_000)
  expect(config?.timeout).toBeLessThanOrEqual(2_000)
})
