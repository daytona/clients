/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { strip } from './pyText.js'
import { AuthenticationError, BadRequestError, DaytonaError, ForbiddenError, NotFoundError, RequestTimeoutError, SandboxTerminalStateError } from './errors/DaytonaError.js'
import { targetPredicate, waitQueryKey } from './events.js'
import type { WaitHub, WaitOutcome } from './events.js'
import { sleep } from './http.js'
import { MAX_TIMER_DELAY_MS, optionalSeconds, waitDeadline } from './options.js'
import type { SandboxState, SandboxWaitState } from './types.js'
import { WAIT_FOR_STATE_MIN_INTERVAL_MS } from './waitConstants.js'

export const DEFAULT_SANDBOX_TERMINAL_STATES = ['destroying', 'destroyed', 'error'] as const satisfies readonly SandboxState[]
export const PAUSE_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'started'] as const
export const RESUME_TERMINAL_STATES = [...DEFAULT_SANDBOX_TERMINAL_STATES, 'stopped'] as const
export const WAIT_FOR_STATE_SLICE_MS = 30_000
// The end of a sandbox wait's budget kept for a read of the server when the
// events stream has decided nothing: at most 5 s, at most a quarter of it.
export const WAIT_FINAL_READ_RESERVE_MS = 5_000

/** What a read of the sandbox found: its state, wire status and the server's reason. */
export interface SandboxReadOutcome extends WaitOutcome {
  readonly reason?: string
}

/** @internal */
export interface SandboxWaitContext {
  readonly id: string
  readonly region?: string
  readonly hub?: WaitHub
  /** One read; with `wait`, a long-poll slice held with `wait_exact` when `exact`. */
  readonly read: (timeoutMs?: number, wait?: SandboxWaitState, requestDeadline?: number, exact?: boolean) => Promise<SandboxReadOutcome>
  /** What the handle last observed, its reason included. */
  readonly current: () => SandboxReadOutcome
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

/**
 * The events hub's share of a wait ending at `deadline`: all but the
 * final-read reserve. The hub reads `0` as no deadline, so a spent budget is a
 * timeout here.
 */
function hubWaitBudget(deadline: number | undefined): number {
  if (deadline === undefined) return 0
  const remaining = deadline - Date.now()
  if (remaining <= 0) throw timeoutError()
  return remaining - Math.min(WAIT_FINAL_READ_RESERVE_MS, remaining / 4)
}

/** Shared event/poll engine; callers decide whether timeout or terminal states raise. */
export async function waitForSandboxState(context: SandboxWaitContext, states: readonly SandboxWaitState[], options: SandboxStateWaitOptions): Promise<void> {
  if (states.length === 0) throw new BadRequestError('wait requires at least one target state', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  states.forEach(waitQueryState)
  const seconds = optionalSeconds('timeoutSeconds', options.timeoutSeconds) ?? 60
  // 0, or above the timer maximum (about 24.8 days), is no deadline.
  const deadline = waitDeadline(seconds)
  const terminalStates = options.terminalStates ?? DEFAULT_SANDBOX_TERMINAL_STATES
  const effectiveTerminal = states.includes('destroyed') ? terminalStates.filter((s) => s !== 'destroying') : terminalStates
  const waitTarget = new Set(states).size === 1 ? states[0] : undefined
  // A wait for `destroyed` alone holds its long polls with `wait_exact=deleted`.
  const waitValue = waitQueryState(waitTarget)
  const exact = waitValue !== undefined && waitQueryKey(waitValue, effectiveTerminal) === 'wait_exact'
  const remaining = (): number | undefined => deadline === undefined ? undefined : Math.max(0, deadline - Date.now())
  const read = async (timeoutMs?: number, wait?: SandboxWaitState, readDeadline = deadline): Promise<SandboxReadOutcome> => {
    try {
      return await context.read(timeoutMs, wait, readDeadline, wait !== undefined && exact)
    } catch (error) {
      if (options.missingIsDestroyed && error instanceof NotFoundError) {
        const destroyed = { state: 'destroyed', rawState: 'deleted' } as const
        context.observe(destroyed)
        return destroyed
      }
      throw error
    }
  }

  // A terminal state a read found: its wire status and the server's reason.
  const terminalError = (outcome: SandboxReadOutcome): SandboxTerminalStateError =>
    new SandboxTerminalStateError(context.id, outcome.state, { rawState: outcome.rawState || undefined, reason: outcome.reason })
  // A hub report no read confirms, whose state came from the full read
  // `seen`: when that read shows the reported state and wire status, name the
  // server's explanation. `failed` and `lost` share the public `error`;
  // matching the wire status too keeps one's reason from labelling the other.
  const withReason = (error: SandboxTerminalStateError, seen: SandboxReadOutcome): SandboxTerminalStateError => {
    if (error.reason !== undefined || !seen.reason || seen.state !== error.state) return error
    if (error.rawState !== undefined && seen.rawState !== error.rawState) return error
    return new SandboxTerminalStateError(context.id, error.state, { rawState: error.rawState ?? (seen.rawState || undefined), reason: seen.reason })
  }

  // Long-poll reads until a target or terminal state. `lastReadEnd` is when
  // the wait's previous read ended; the first read keeps the minimum spacing.
  const pollForState = async (lastReadEnd?: number): Promise<void> => {
    if (lastReadEnd !== undefined) {
      const pause = WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - lastReadEnd)
      // The spacing never spends what is left: with less than it, the one
      // read happens at once.
      if (pause > 0 && (remaining() ?? Infinity) > pause) await sleep(pause)
    }
    for (;;) {
      const budget = remaining()
      if (budget !== undefined && budget <= 0) throw timeoutError()
      const pollStarted = Date.now()
      const outcome = await read(Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(budget ?? WAIT_FOR_STATE_SLICE_MS)), waitTarget)
      if (states.some((target) => target === outcome.state)) return
      if (effectiveTerminal.includes(outcome.state)) throw terminalError(outcome)
      const pause = Math.min(WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - pollStarted), remaining() ?? Infinity)
      if (pause > 0) await sleep(pause)
    }
  }

  if (context.hub?.enabled) {
    let lastPollEnd: number | undefined
    let refreshedByPoll = false
    const hubBudget = hubWaitBudget(deadline)
    // Where the hub's share ends: its probes hold no long poll past it, so
    // none still runs when the reserve starts reading.
    const hubDeadline = deadline === undefined ? undefined : Date.now() + hubBudget
    const sandboxPollFn = async (): Promise<WaitOutcome> => {
      if (lastPollEnd !== undefined) {
        const pause = Math.min(WAIT_FOR_STATE_MIN_INTERVAL_MS - (Date.now() - lastPollEnd), remaining() ?? Infinity)
        if (pause > 0) await sleep(pause)
      }
      const budget = remaining()
      if (budget !== undefined && budget <= 0) throw timeoutError()
      const held = budget === undefined || hubDeadline === undefined ? budget : Math.max(1, Math.min(budget, hubDeadline - Date.now()))
      // The read itself (its slack included) ends with the hub's share.
      const result = await read(Math.min(WAIT_FOR_STATE_SLICE_MS, Math.ceil(held ?? WAIT_FOR_STATE_SLICE_MS)), waitTarget, hubDeadline ?? deadline)
      lastPollEnd = Date.now()
      refreshedByPoll = true
      return result
    }
    try {
      const resolved = await context.hub.wait(context.id, targetPredicate(new Set<SandboxState>(states), new Set(effectiveTerminal)), {
        timeout: hubBudget,
        regionHint: context.region !== undefined && strip(context.region).toLowerCase() === 'local' ? undefined : context.region,
        sandboxPollFn,
        onObserved: (outcome) => { refreshedByPoll = false; context.observe(outcome) },
      })
      context.observe(resolved)
      if (!refreshedByPoll && resolved.state !== 'destroyed') {
        const budget = remaining()
        if (budget === undefined || budget > 0) {
          try {
            await read(budget === undefined ? undefined : Math.min(Math.ceil(budget), MAX_TIMER_DELAY_MS))
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
      if (deadline !== undefined && error instanceof RequestTimeoutError && error.source === 'SDK') {
        // The events stream decided nothing within its share of the budget (a
        // stream that sends no transitions decides only at its reconcile). The
        // reserve left is read from the server, under the same deadline, so a
        // wait never runs out on the stream alone. A server's 408 is not this
        // wait running out.
        try {
          await pollForState(lastPollEnd)
        } catch (readError) {
          // What the read decided (a 404 outside a delete: the sandbox is
          // gone), the wait's own deadline, or a credential the API refuses
          // (final for the hub's own probe too), stands. Any other failed read,
          // a server's 408 included, does not replace the wait running out; it
          // is kept as the cause.
          if (
            readError instanceof SandboxTerminalStateError || readError instanceof NotFoundError
            || (readError instanceof RequestTimeoutError && readError.source === 'SDK')
            || readError instanceof AuthenticationError || readError instanceof ForbiddenError
          ) throw readError
          if (readError instanceof DaytonaError) throw new RequestTimeoutError(error.message, { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK', cause: readError })
          throw readError
        }
        return
      }
      if (!(error instanceof SandboxTerminalStateError)) throw error
      // What the handle last read, before the report overwrites it.
      const seen = context.current()
      context.observe({ state: error.state, rawState: error.rawState ?? '' })
      // A terminal state the hub reports may be one it held before this
      // request's frames arrived (an `error` from an earlier wait, while a
      // DELETE already moves the sandbox on): one read confirms it, and the
      // read decides. No read is possible once the budget is spent, when the
      // state already came from a full read, or for `destroyed`.
      const budget = remaining()
      if (refreshedByPoll || error.state === 'destroyed' || (budget !== undefined && budget <= 0)) throw refreshedByPoll ? withReason(error, seen) : error
      let outcome: SandboxReadOutcome
      try {
        outcome = await read(budget === undefined ? undefined : Math.min(Math.ceil(budget), MAX_TIMER_DELAY_MS))
      } catch (refreshError) {
        // The terminal outcome takes precedence over a failed metadata read.
        if (!(refreshError instanceof DaytonaError)) throw refreshError
        throw error
      }
      if (states.some((target) => target === outcome.state)) return
      if (effectiveTerminal.includes(outcome.state)) throw terminalError(outcome)
      // Neither a target nor terminal: the hub's report was stale. Poll for
      // the rest of the budget, under the same deadline.
    }
  }

  await pollForState()
}
