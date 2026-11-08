/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { WaitHub } from '../src/v2/events'
import { createHttpClient } from '../src/v2/http'
import { ConnectionError, RequestTimeoutError } from '../src/v2/errors/DaytonaError'
import { createSandbox } from './helpers/sandbox'

const eventsSetting = process.env.DAYTONA_SANDBOX_EVENTS
beforeEach(() => { delete process.env.DAYTONA_SANDBOX_EVENTS })
afterEach(() => {
  if (eventsSetting === undefined) delete process.env.DAYTONA_SANDBOX_EVENTS
  else process.env.DAYTONA_SANDBOX_EVENTS = eventsSetting
  jest.restoreAllMocks()
  jest.useRealTimers()
})

it.each([
  { method: 'start', state: 'started', rawState: 'running' },
  { method: 'stop', state: 'stopped', rawState: 'stopped' },
  { method: 'pause', state: 'paused', rawState: 'paused' },
  { method: 'resume', state: 'started', rawState: 'running' },
  { method: 'wait', state: 'started', rawState: 'running' },
] as const)('preserves observed $state when the final $method metadata refresh fails', async ({ method, state, rawState }) => {
  // Given an event-backed target observation and an unavailable detail read.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state, rawState })
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const get = jest.spyOn(api, 'get')
  if (method === 'start') get.mockResolvedValueOnce({ data: { id: 'sbx', status: 'stopped' } })
  get.mockRejectedValue(new ConnectionError('refresh unavailable'))
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the lifecycle wait observes its target.
  await sandbox[method]()
  // Then the observed success survives a failed refresh without retry.
  expect(sandbox.state).toBe(state)
  expect(get).toHaveBeenCalledTimes(method === 'start' ? 2 : 1)
})

it('keeps the state a successful refresh reads after the hub observed the target', async () => {
  // Given an event-backed target observation and a detail read showing a later state.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
  jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', status: 'stopping' } })
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When the wait resolves and the refresh succeeds.
  await sandbox.wait()
  // Then the handle reports what the server said last, not the event-time state.
  expect([sandbox.state, sandbox.rawState]).toEqual(['stopping', 'stopping'])
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

it('propagates programming errors from the post-observation refresh', async () => {
  // Given a successful state observation followed by a bug in the refresh.
  const api = createHttpClient('http://api.invalid')
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true }, async () => { throw new Error('unexpected socket') }, api)
  jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
  const error = new TypeError('bug')
  jest.spyOn(api, 'get').mockRejectedValue(error)
  const sandbox = createSandbox('sbx', api, undefined, undefined, undefined, {}, undefined, undefined, hub)
  // When refreshing observed metadata.
  const pending = sandbox.wait()
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
