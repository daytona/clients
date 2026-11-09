/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as toolbox from './gen/toolbox-types.js'
import type { SseMessage } from './http.js'
import type { LogFrame, ProcessCommand, ProcessDto, ProcessKind, ProcessSignal, ProcessStartOptions, StreamLogEvent, WaitResult } from './types.js'
import { PROCESS_KEEP_LOGS, PROCESS_KINDS, PROCESS_REASONS, PROCESS_STATES, PROCESS_STDIN_MODES } from './types.js'
import { UnsupportedOperationError, createDaytonaError } from './errors/DaytonaError.js'
import { knownOrUnknown } from './knownValue.js'
import { badRequest } from './options.js'

export type LogFrameWire = toolbox.LogFrameDto
export type LogsWire = { items?: LogFrameWire[] } | toolbox.ProcessLogsResponse
/** Internal only: interpreter contexts and bound shell sessions supply their own session id. */
export type InternalProcessStartOptions = ProcessStartOptions & {
  readonly kind?: ProcessKind
  readonly language?: string
  readonly code?: string
  readonly sessionId?: string
}

/** Extra HTTP read budget for a bounded process long-poll. */
export const WAIT_READ_SLACK_MS = 5_000
export const WAIT_TRANSPORT_RETRY_LIMIT = 3

export function waitDeadline(timeout: number | undefined): number | undefined {
  if (timeout === undefined || timeout === 0) return undefined
  if (!Number.isFinite(timeout) || timeout < 0) {
    throw badRequest('timeout must be a non-negative finite number')
  }
  return Date.now() + timeout
}

/** Round fractional wire budgets up to avoid under-waiting. */
export function remainingWaitBudgetMs(deadline: number): number {
  return Math.max(0, Math.ceil(deadline - Date.now()))
}

export function signalWire(signal: ProcessSignal | undefined): string | undefined {
  return signal ? `SIG${signal}` : undefined
}

export function waitResult(input: toolbox.ExitResult): WaitResult {
  const rawReason = input.reason
  const exitCode = typeof input.exitCode === 'number' ? input.exitCode : undefined
  const signal = typeof input.signal === 'string'
    ? (input.signal.startsWith('SIG') ? input.signal.slice(3) : input.signal)
    : undefined
  if (typeof rawReason !== 'string') return { reason: 'failed', exitCode, signal }
  const reason = knownOrUnknown(rawReason, PROCESS_REASONS)
  switch (reason) {
    case 'exited': return { reason, rawReason, exitCode: exitCode ?? 0 }
    case 'signaled': return { reason, rawReason, signal: signal ?? 'KILL' }
    case 'timed_out': return { reason, rawReason }
    case 'lost':
    case 'failed':
    case 'unknown': return { reason, rawReason, exitCode, signal }
    default: return reason satisfies never
  }
}

export function frameFromWire(input: LogFrameWire): LogFrame {
  const timestamp = typeof input.timestamp === 'number' && Number.isFinite(input.timestamp)
    ? input.timestamp
    : undefined
  return {
    cursor: input.cursor ?? '', channel: input.channel ?? 'stdout',
    data: input.data ?? '', timestamp,
  }
}

export function knownProcessKind(kind: string | undefined): ProcessKind | undefined {
  return PROCESS_KINDS.find((candidate) => candidate === kind)
}

// Unknown response kinds fall back to the requested kind for verb gating.
export function startedProcessKind(started: toolbox.ProcessDto, body: toolbox.CreateProcessRequest): ProcessKind {
  const reported = knownProcessKind(started.kind)
  if (reported !== undefined) return reported
  const requested = knownProcessKind(body.kind)
  switch (requested) {
    case 'session':
    case 'pty':
    case 'code': return requested
    case 'exec':
    case undefined: return typeof body.code === 'string' ? 'code' : 'exec'
    default: return requested satisfies never
  }
}

export function invalidPtyVerbError(verb: 'attach' | 'resize' | 'delete', kind: ProcessDto['kind']): UnsupportedOperationError {
  return new UnsupportedOperationError(
    `process.${verb}() is only supported for pty handles; handle kind is ${kind}`,
    { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
  )
}

export function invalidStdinVerbError(verb: 'writeStdin' | 'closeStdin', kind: ProcessKind): UnsupportedOperationError {
  return new UnsupportedOperationError(
    `process.${verb}() is only supported for exec and pty handles; handle kind is ${kind}`,
    { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
  )
}

export function commandSource(command: ProcessCommand): string | InternalProcessStartOptions {
  if (typeof command === 'string') return command
  return 'argv' in command && command.argv !== undefined ? { argv: [...command.argv] } : { cmd: command.cmd }
}

export function bodyFor(commandOrOptions: string | InternalProcessStartOptions, options?: InternalProcessStartOptions): toolbox.CreateProcessRequest {
  const merged = typeof commandOrOptions === 'string'
    ? { ...options, cmd: commandOrOptions }
    : { ...commandOrOptions, ...options }
  const hasShell = typeof merged.cmd === 'string'
  const hasArgv = Array.isArray(merged.argv)
  const hasCode = typeof merged.code === 'string'
  const sourceCount = [hasShell, hasArgv, hasCode].filter(Boolean).length
  if (!(merged.kind === 'pty' && sourceCount === 0) && sourceCount !== 1) {
    throw createDaytonaError('exactly one of cmd, argv, or code is required', 400, 'BAD_REQUEST', 'SDK')
  }
  if (hasCode && merged.user !== undefined) {
    throw new UnsupportedOperationError(
      'user is not supported for code processes; code runs as the sandbox user',
      { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
    )
  }
  if (merged.executionTimeout !== undefined && (!Number.isFinite(merged.executionTimeout) || merged.executionTimeout < 0)) {
    throw badRequest('executionTimeout must be a non-negative finite number')
  }
  if (merged.terminal !== undefined && merged.kind !== 'pty') {
    throw new UnsupportedOperationError(
      'terminal cols/rows are only supported for pty processes; use sandbox.pty.start() or sandbox.pty.attach()',
      { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
    )
  }
  const body: toolbox.CreateProcessRequest = {
    shellCommand: merged.cmd, argv: merged.argv, code: merged.code, kind: merged.kind,
    name: merged.name, tag: merged.tag, sessionId: merged.sessionId, cwd: merged.cwd, env: merged.env,
    user: merged.user, shell: merged.shell, login: merged.login, stdin: merged.stdin,
    // Code cells have no server-side deadline. Shell-session commands do.
    timeoutMs: hasCode || merged.executionTimeout === undefined ? undefined : Math.ceil(merged.executionTimeout),
    terminal: merged.terminal, language: merged.language, keepLogs: merged.keepLogs,
  }
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined))
}

export function processFromWire(input: toolbox.ProcessDto): ProcessDto {
  const { timeoutMs, ...rest } = input
  // Nullable or malformed open values read as absent, never as a null raw value.
  const rawReason = typeof input.reason === 'string' ? input.reason : undefined
  const rawStdin = typeof input.stdin === 'string' ? input.stdin : undefined
  const rawKeepLogs = typeof input.keepLogs === 'string' ? input.keepLogs : undefined
  return {
    ...rest, executionTimeout: timeoutMs,
    kind: knownOrUnknown(input.kind, PROCESS_KINDS), rawKind: input.kind,
    state: knownOrUnknown(input.state, PROCESS_STATES), rawState: input.state,
    stdin: rawStdin === undefined ? undefined : knownOrUnknown(rawStdin, PROCESS_STDIN_MODES), rawStdin,
    keepLogs: rawKeepLogs === undefined ? undefined : knownOrUnknown(rawKeepLogs, PROCESS_KEEP_LOGS), rawKeepLogs,
    reason: rawReason === undefined ? undefined : knownOrUnknown(rawReason, PROCESS_REASONS), rawReason,
  }
}

export function streamEvent(message: SseMessage): StreamLogEvent {
  const parsed = message.data ? JSON.parse(message.data) as Record<string, unknown> : {}
  const cursor = typeof parsed.cursor === 'string' ? parsed.cursor : message.id
  if (message.event === 'state') return { type: 'state', cursor, result: waitResult(parsed as toolbox.ExitResult) }
  if (message.event === 'warning') {
    return {
      type: 'warning', cursor,
      message: typeof parsed.message === 'string' ? parsed.message : message.data,
      firstAvailableCursor: typeof parsed.firstAvailableCursor === 'string' ? parsed.firstAvailableCursor : undefined,
    }
  }
  if (message.event === 'eof') return { type: 'eof', cursor }
  if (message.event === 'error') return { type: 'error', cursor, error: createDaytonaError(typeof parsed.message === 'string' ? parsed.message : message.data) }
  return { type: 'log', cursor, frame: frameFromWire({ ...parsed, cursor } as LogFrameWire) }
}

export function base64Encode(data: Uint8Array): string {
  const maybeBuffer = (globalThis as { Buffer?: { from(input: Uint8Array): { toString(encoding: 'base64'): string } } }).Buffer
  if (maybeBuffer) return maybeBuffer.from(data).toString('base64')
  if (typeof btoa !== 'function') throw createDaytonaError('base64 encoding is unavailable in this runtime', 400, 'UNSUPPORTED_OPERATION', 'SDK')
  let binary = ''
  for (const byte of data) binary += String.fromCharCode(byte)
  return btoa(binary)
}
