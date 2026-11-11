/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from '../src/v2/errors/DaytonaError'
import { SandboxStatus } from '../src/v2/gen/api-enums'
import { waitForSandboxState, waitQueryState } from '../src/v2/sandboxWait'
import type { SandboxWaitState } from '../src/v2/types'

// Every wait state and the status it is sent as, checked for completeness at
// compile time.
const EXPECTED = {
  queued: 'queued',
  starting: 'booting',
  started: 'running',
  pausing: 'pausing',
  paused: 'paused',
  stopping: 'stopping',
  stopped: 'stopped',
  destroying: 'deleting',
  destroyed: 'deleted',
} as const satisfies Record<SandboxWaitState, string>
const WAIT_STATES = Object.keys(EXPECTED) as SandboxWaitState[]

// The API answers a `wait` / `wait_exact` query naming anything but a status of
// the contract's SandboxStatus with 400, so the mapping must stay inside it.
describe('sandbox wait targets', () => {
  it('declares the thirteen contract statuses', () => {
    expect(SandboxStatus).toHaveLength(13)
  })

  it.each(WAIT_STATES)('sends %s as a contract status', (state) => {
    expect(waitQueryState(state)).toBe(EXPECTED[state])
    expect(SandboxStatus).toContain(EXPECTED[state])
  })

  it.each(['running', 'deleted', 'creating', 'error', 'unknown', 'STARTED'])('refuses %s before any request', async (state) => {
    const read = jest.fn()
    const error = await waitForSandboxState({ read } as never, [state as SandboxWaitState], { timeoutSeconds: 1 }).catch((failure: unknown) => failure)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    expect(read).not.toHaveBeenCalled()
  })
})
