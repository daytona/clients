/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { strip } from './pyText.js'
import { BadRequestError, DaytonaError, NotFoundError, RequestTimeoutError, SandboxTerminalStateError } from './errors/DaytonaError.js'
import { targetPredicate } from './events.js'
import type { WaitHub, WaitOutcome } from './events.js'
import { sleep } from './http.js'
import { optionalSeconds, waitDeadline } from './options.js'
import type { SandboxState, SandboxWaitState } from './types.js'
import { WAIT_FOR_STATE_MIN_INTERVAL_MS } from './waitConstants.js'

export const DEFAULT_SANDBOX_TERMINAL_STATES = ['destroying', 'destroyed', 'error'] as const satisfies readonly SandboxState[]
export const PAUSE_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'started'] as const
export const RESUME_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'stopped'] as const
export const WAIT_FOR_STATE_SLICE_MS = 30_000
const MAX_REQUEST_TIMEOUT_MS = 2_147_483_647

export interface SandboxWaitContext {
  readonly id: string
  readonly region?: string
  readonly hub?: WaitHub
  readonly read: (timeoutMs?: number, wait?: SandboxWaitState, requestDeadline?: number) => Promise<WaitOutcome>
  readonly observe: (outcome: WaitOutcome) => void
}

export interface SandboxStateWaitOptions {
  readonly timeoutSeconds?: number
  readonly terminalStates?: readonly SandboxState[]
  readonly missingIsDestroyed?: boolean
}

function timeoutError(): RequestTimeoutError {
  return new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
}

export function waitQueryState(state: SandboxWaitState | undefined): string | undefined {
  switch (state) {
    case undefined: return undefined
    case 'queued': return 'queued'
    case 'starting': return 'booting'
    case 'started': return 'running'
    case 'pausing':
    case 'paused':
    case 'stopping':
    case 'stopped': return state
    case 'destroying': return 'deleting'
    case 'destroyed': return 'deleted'
    default: {
      const unhandled: never = state
      throw new BadRequestError(`invalid sandbox wait state: ${String(unhandled)}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
  }
}

/** Shared event/poll engine; callers decide whether timeout or terminal states raise. */
export async function waitForSandboxState(context: SandboxWaitContext, states: readonly SandboxWaitState[], options: SandboxStateWaitOptions): Promise<void> {
  if (states.length === 0) throw new BadRequestError('wait requires at least one target state', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  states.forEach(waitQueryState)
  const seconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
  // 0, or above the timer maximum (about 24.8 days), is no deadline.
  const deadline = waitDeadline(seconds)
  const timeout = deadline === undefined ? 0 : seconds * 1000
  const terminalStates = options.terminalStates ?? DEFAULT_SANDBOX_TERMINAL_STATES
  const effectiveTerminal = states.includes('destroyed') ? terminalStates.filter((s) => s !== 'destroying') : terminalStates
  const waitTarget = new Set(states).size === 1 ? states[0] : undefined
  const remaining = (): number | undefined => deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
  const read = async (timeoutMs?: number, wait?: SandboxWaitState): Promise<WaitOutcome> => {
    try {
      return await context.read(timeoutMs, wait, deadline)
    } catch (error) {
      if (options.missingIsDestroyed && error instanceof NotFoundError) {
        const destroyed = { state: 'destroyed', rawState: 'deleted' } as const
        context.observe(destroyed)
        return destroyed
      }
      throw error
    }
  }

  if (context.hub?.enabled) {
    let lastPollEnd: number | undefined
    let refreshedByPoll = false
    const sandboxPollFn = async (): Promise<WaitOutcome> => {
      if (lastPollEnd !== undefined) {
        const pause = Math.min(WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - lastPollEnd), remaining() ?? Infinity)
        if (pause > 0) await sleep(pause)
      }
      const budget = remaining()
      if (budget !== undefined && budget <= 0) throw timeoutError()
      const result = await read(Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(budget ?? WAIT_FOR_STATE_SLICE_MS)), waitTarget)
      lastPollEnd = Date.now()
      refreshedByPoll = true
      return result
    }
    try {
      const resolved = await context.hub.wait(context.id, targetPredicate(new Set<SandboxState>(states), new Set(effectiveTerminal)), {
        timeout,
        regionHint: context.region !== undefined && strip(context.region).toLowerCase() === 'local' ? undefined : context.region,
        sandboxPollFn,
        onObserved: (outcome) => { refreshedByPoll = false; context.observe(outcome) },
      })
      context.observe(resolved)
      if (!refreshedByPoll && resolved.state !== 'destroyed') {
        const budget = remaining()
        if (budget === undefined || budget > 0) {
          try {
            await read(budget === undefined ? undefined : Math.min(Math.ceil(budget), MAX_REQUEST_TIMEOUT_MS))
          } catch (error) {
            // The target was observed; a failed refresh keeps that observation.
            if (!(error instanceof DaytonaError)) throw error
          }
        }
      }
      return
    } catch (error) {
      if (options.missingIsDestroyed && error instanceof NotFoundError) {
        context.observe({ state: 'destroyed', rawState: 'deleted' })
        return
      }
      if (error instanceof SandboxTerminalStateError) {
        context.observe({ state: error.state, rawState: error.rawState ?? '' })
        const budget = remaining()
        if (!refreshedByPoll && error.state !== 'destroyed' && (budget === undefined || budget > 0)) {
          try {
            const outcome = await read(budget === undefined ? undefined : Math.min(Math.ceil(budget), MAX_REQUEST_TIMEOUT_MS))
            if (options.missingIsDestroyed && outcome.state === 'destroyed') return
          } catch (refreshError) {
            // The terminal outcome takes precedence over a failed metadata read.
            if (!(refreshError instanceof DaytonaError)) throw refreshError
          }
        }
      }
      throw error
    }
  }

  for (;;) {
    const budget = remaining()
    if (budget !== undefined && budget <= 0) throw timeoutError()
    const pollStarted = Date.now()
    const outcome = await read(Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(budget ?? WAIT_FOR_STATE_SLICE_MS)), waitTarget)
    if (states.some((target) => target === outcome.state)) return
    if (effectiveTerminal.includes(outcome.state)) throw new SandboxTerminalStateError(context.id, outcome.state, { rawState: outcome.rawState })
    const pause = Math.min(WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - pollStarted), remaining() ?? Infinity)
    if (pause > 0) await sleep(pause)
  }
}
