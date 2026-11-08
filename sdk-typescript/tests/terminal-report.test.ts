/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { WaitHub } from '../src/v2/events'
import { SandboxTerminalStateError } from '../src/v2/errors/DaytonaError'
import { createSandbox } from './helpers/sandbox'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'
import type { FakeApi, PlannedResponse } from './networkPolicyTestServer'

// A terminal state the events hub reports is confirmed with one read, and the
// read decides: a target state returns, a terminal state throws with the read
// state, and any other state means the report was stale, so the wait polls on.
// The hub can report a state it held before the new request's frames arrive:
// an `error` while a DELETE already moves the sandbox to `destroying`. The
// Python twin is tests/test_terminal_report.py; the real-hub sequence is in
// tests/events.test.ts.
const detail = (status: string) => ({ id: 'sbx', class: 'linux', name: 'sbx', status, cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a' })
const GONE: PlannedResponse = { status: 404, body: { error: 'sandbox not found', code: 'NOT_FOUND' } }

let savedEnv: string | undefined
beforeEach(() => {
  savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
  process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
})
afterEach(() => {
  jest.restoreAllMocks()
  if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
  else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
})

async function run(responses: PlannedResponse[], call: 'delete' | 'wait') {
  const api = await startFakeApi(responses)
  const http = createHttpClient(api.url, 'key')
  // Reports the `error` it holds, before any frame of the new request.
  const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, http)
  const wait = jest.spyOn(hub, 'wait').mockRejectedValue(new SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
  const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'failed' }, http, undefined, 'key', undefined, {}, undefined, undefined, hub)
  const done = call === 'delete' ? sandbox.delete({ timeoutSeconds: 10 }) : sandbox.wait({ until: 'started', timeoutSeconds: 10 })
  const outcome = await done.then(() => ({ ok: true as const }), (error: unknown) => ({ ok: false as const, error }))
  await api.close()
  return { api, sandbox, outcome, wait }
}

const paths = (api: FakeApi) => api.requests.map((request) => `${request.method} ${request.path.split('?')[0]}`)

it('ends a delete whose read after a held error is a 404', async () => {
  const { api, sandbox, outcome, wait } = await run([{ status: 200, body: {} }, GONE], 'delete')
  expect(outcome).toEqual({ ok: true })
  expect(wait).toHaveBeenCalledTimes(1)
  expect(paths(api)).toEqual(['DELETE /sandboxes/sbx', 'GET /sandboxes/sbx'])
  expect([sandbox.state, sandbox.rawState]).toEqual(['destroyed', 'deleted'])
})

it.each([
  ['deleted', { status: 200, body: detail('deleted') }],
  ['404', GONE],
] as const)('polls on after a held error read as destroying, until the sandbox is gone (%s)', async (_name, gone) => {
  const { api, sandbox, outcome } = await run([{ status: 200, body: {} }, { status: 200, body: detail('deleting') }, gone], 'delete')
  expect(outcome).toEqual({ ok: true })
  expect(paths(api)).toEqual(['DELETE /sandboxes/sbx', 'GET /sandboxes/sbx', 'GET /sandboxes/sbx'])
  expect(sandbox.state).toBe('destroyed')
})

it('throws with the read state when the read confirms the error', async () => {
  const { api, outcome } = await run([{ status: 200, body: {} }, { status: 200, body: detail('failed') }], 'delete')
  expect(sentRequest(api, 2, 1).method).toBe('GET')
  if (outcome.ok) throw new Error('expected the delete to throw')
  expect(outcome.error).toBeInstanceOf(SandboxTerminalStateError)
  expect(outcome.error).toMatchObject({ state: 'error', rawState: 'failed' })
})

it('polls on after a held terminal state read as starting, until started', async () => {
  const { api, sandbox, outcome } = await run([{ status: 200, body: detail('booting') }, { status: 200, body: detail('running') }], 'wait')
  expect(outcome).toEqual({ ok: true })
  expect(paths(api)).toEqual(['GET /sandboxes/sbx', 'GET /sandboxes/sbx'])
  expect(sandbox.state).toBe('started')
})

it('keeps the reported terminal state when the read fails', async () => {
  const { api, outcome } = await run([{ status: 200, body: {} }, { status: 500, body: { message: 'stop' } }], 'delete')
  expect(sentRequest(api, 2, 1).method).toBe('GET')
  if (outcome.ok) throw new Error('expected the delete to throw')
  expect(outcome.error).toMatchObject({ state: 'error' })
})

it('keeps the reported terminal state for a 404 outside a delete', async () => {
  const { api, outcome } = await run([GONE], 'wait')
  expect(sentRequest(api, 1, 0).method).toBe('GET')
  if (outcome.ok) throw new Error('expected the wait to throw')
  expect(outcome.error).toBeInstanceOf(SandboxTerminalStateError)
  expect(outcome.error).toMatchObject({ state: 'error' })
})
