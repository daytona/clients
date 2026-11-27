/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { WaitHub } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ConnectionError, RequestTimeoutError } from '../src/v2/errors/DaytonaError'
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
