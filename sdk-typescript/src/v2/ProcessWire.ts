/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as toolbox from './gen/toolbox-types.js'
import type { SseMessage } from './http.js'
import type {
  KeepLogs, LogFrame, ProcessCommand, ProcessInfo, ProcessKind, ProcessSignal, RunKeepLogs, StdinMode,
  StreamLogEvent, TerminalSize, WaitResult,
} from './types.js'
import { PROCESS_KEEP_LOGS, PROCESS_KINDS, PROCESS_REASONS, PROCESS_STATES, PROCESS_STDIN_MODES } from './types.js'
import { BadRequestError, DaytonaError, UnsupportedOperationError, createDaytonaError } from './errors/DaytonaError.js'
import { knownOrUnknown } from './knownValue.js'
import { assertKnownOptions, optionKeys } from './options.js'

export type LogFrameWire = toolbox.LogFrameDto
export type LogsWire = { items?: LogFrameWire[] } | toolbox.ProcessLogsResponse

/** Internal: everything a create-process request can carry, before it becomes the wire body. */
export interface ProcessSpec {
  readonly cmd?: string
  readonly argv?: readonly string[]
  readonly code?: string
  readonly kind?: ProcessKind
  readonly language?: string
  readonly sessionId?: string
  readonly name?: string
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
  readonly user?: string
  readonly shell?: string
  readonly login?: boolean
  readonly stdin?: StdinMode
  readonly executionTimeoutSeconds?: number
  readonly terminal?: TerminalSize
  readonly keepLogs?: KeepLogs
}

/** Extra HTTP read budget for a bounded process long-poll. */
export const WAIT_READ_SLACK_MS = 5_000
export const WAIT_TRANSPORT_RETRY_LIMIT = 3

const SDK = { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' } as const

function badRequest(message: string): BadRequestError {
  return new BadRequestError(message, SDK)
}

/** A duration in seconds as whole wire milliseconds, rounded up (`undefined` stays unset). */
export function secondsToMs(name: string, seconds: number | undefined): number | undefined {
  if (seconds === undefined) return undefined
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    throw badRequest(`${name} must be a non-negative finite number of seconds`)
  }
  // Round away float noise (0.1 * 1000 = 100.00000000000001) before rounding up.
  return Math.ceil(Math.round(seconds * 1000 * 1e6) / 1e6)
}

/** The absolute deadline (epoch ms) of a caller wait budget in seconds; `undefined`/`0` = unbounded. */
export function waitDeadline(timeoutSeconds: number | undefined, name = 'timeoutSeconds'): number | undefined {
  const ms = secondsToMs(name, timeoutSeconds)
  if (ms === undefined || ms === 0) return undefined
  return Date.now() + ms
}

/** Round fractional wire budgets up to avoid under-waiting. */
export function remainingWaitBudgetMs(deadline: number): number {
  return Math.max(0, Math.ceil(deadline - Date.now()))
}

export function signalWire(signal: ProcessSignal | undefined): string | undefined {
  return signal ? `SIG${signal}` : undefined
}

/** The SDK spells signals without the wire's `SIG` prefix. */
export function bareSignal(signal: unknown): string | undefined {
  if (typeof signal !== 'string') return undefined
  return signal.startsWith('SIG') ? signal.slice(3) : signal
}

export function waitResult(input: toolbox.ExitResult): WaitResult {
  const rawReason = typeof input.reason === 'string' ? input.reason : undefined
  const exitCode = typeof input.exitCode === 'number' ? input.exitCode : undefined
  const signal = bareSignal(input.signal)
  return {
    reason: rawReason === undefined ? 'unknown' : knownOrUnknown(rawReason, PROCESS_REASONS),
    ...(rawReason === undefined ? {} : { rawReason }),
    ...(exitCode === undefined ? {} : { exitCode }),
    ...(signal === undefined ? {} : { signal }),
  }
}

export function frameFromWire(input: LogFrameWire): LogFrame {
  const { cursor, channel, data, timestamp } = input as Partial<Record<string, unknown>>
  if (typeof cursor !== 'string' || typeof channel !== 'string' || typeof data !== 'string'
    || typeof timestamp !== 'number' || !Number.isFinite(timestamp)) {
    throw badRequest('expected log frame: cursor, channel, data and timestamp are required')
  }
  return { cursor, channel, data, timestampMs: timestamp }
}

export function knownProcessKind(kind: unknown): ProcessKind | undefined {
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

export function invalidPtyVerbError(verb: 'attach' | 'resize' | 'delete', kind: ProcessInfo['kind']): UnsupportedOperationError {
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

const COMMAND_KEYS = optionKeys<{ readonly cmd?: string; readonly argv?: readonly string[] }>()({ cmd: true, argv: true })
const TERMINAL_SIZE_KEYS = optionKeys<TerminalSize>()({ cols: true, rows: true })

/** The command part of a process spec: exactly one of a shell string, `{ cmd }` or `{ argv }`. */
export function commandSpec(command: ProcessCommand): ProcessSpec {
  if (typeof command === 'string') return { cmd: command }
  if (typeof command !== 'object' || command === null) throw badRequest('command must be a string, { cmd } or { argv }')
  assertKnownOptions('ProcessCommand', command, COMMAND_KEYS)
  if ('argv' in command && command.argv !== undefined) {
    if (command.cmd !== undefined) throw badRequest('command takes exactly one of cmd or argv')
    return { argv: [...command.argv] }
  }
  return { cmd: command.cmd }
}

export function terminalSpec(terminal: TerminalSize): TerminalSize {
  assertKnownOptions('TerminalSize', terminal, TERMINAL_SIZE_KEYS)
  return { cols: terminal.cols, rows: terminal.rows }
}

/** `run()` reads the output back from the retained log, so it cannot honour `'none'`. */
export function runKeepLogs(keepLogs: RunKeepLogs | undefined): RunKeepLogs {
  if (keepLogs === undefined) return 'on_exit_ttl'
  if (keepLogs !== 'on_exit_ttl' && keepLogs !== 'until_cleanup') {
    throw badRequest(`keepLogs '${String(keepLogs)}' is not supported by run(); use 'on_exit_ttl' or 'until_cleanup' (start() accepts 'none')`)
  }
  return keepLogs
}

export function bodyFor(spec: ProcessSpec): toolbox.CreateProcessRequest {
  const hasShell = typeof spec.cmd === 'string'
  const hasArgv = Array.isArray(spec.argv)
  const hasCode = typeof spec.code === 'string'
  const sourceCount = [hasShell, hasArgv, hasCode].filter(Boolean).length
  if (!((spec.kind === 'pty' || spec.kind === 'session') && sourceCount === 0) && sourceCount !== 1) {
    throw badRequest('exactly one of cmd, argv, or code is required')
  }
  if (hasCode && spec.user !== undefined) {
    throw new UnsupportedOperationError(
      'user is not supported for code processes; code runs as the sandbox user',
      { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
    )
  }
  const timeoutMs = secondsToMs('executionTimeoutSeconds', spec.executionTimeoutSeconds)
  const body: toolbox.CreateProcessRequest = {
    shellCommand: spec.cmd, argv: spec.argv === undefined ? undefined : [...spec.argv], code: spec.code, kind: spec.kind,
    name: spec.name, sessionId: spec.sessionId, cwd: spec.cwd, env: spec.env === undefined ? undefined : { ...spec.env },
    user: spec.user, shell: spec.shell, login: spec.login,
    // Sessions take no stdin mode.
    stdin: spec.kind === 'session' ? undefined : spec.stdin,
    // Code cells have no server-side deadline. Shell-session commands do.
    timeoutMs: hasCode ? undefined : timeoutMs,
    terminal: spec.terminal, language: spec.language, keepLogs: spec.keepLogs,
  }
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined))
}

function requireString(input: Record<string, unknown>, field: string): string {
  const value = input[field]
  if (typeof value !== 'string') throw badRequest(`expected process response: ${field} is required`)
  return value
}

function optional<T>(value: T | null | undefined): T | undefined {
  return value === null ? undefined : value
}

export function processFromWire(input: toolbox.ProcessDto): ProcessInfo {
  const raw = input as unknown as Record<string, unknown>
  const rawKind = requireString(raw, 'kind')
  const rawState = requireString(raw, 'state')
  const rawStdin = requireString(raw, 'stdin')
  const rawKeepLogs = requireString(raw, 'keepLogs')
  // Nullable or malformed open values read as absent, never as a null raw value.
  const rawReason = typeof input.reason === 'string' ? input.reason : undefined
  const terminal = optional(input.terminal)
  const info: ProcessInfo = {
    id: requireString(raw, 'id'),
    kind: knownOrUnknown(rawKind, PROCESS_KINDS), rawKind,
    state: knownOrUnknown(rawState, PROCESS_STATES), rawState,
    cwd: requireString(raw, 'cwd'),
    stdin: knownOrUnknown(rawStdin, PROCESS_STDIN_MODES), rawStdin,
    keepLogs: knownOrUnknown(rawKeepLogs, PROCESS_KEEP_LOGS), rawKeepLogs,
    createdAt: requireString(raw, 'createdAt'),
    system: input.system === true,
    name: optional(input.name),
    pid: optional(input.pid),
    sessionId: optional(input.sessionId),
    argv: optional(input.argv),
    shellCommand: optional(input.shellCommand),
    code: optional(input.code),
    language: optional(input.language),
    shell: optional(input.shell),
    resolvedShell: optional(input.resolvedShell),
    login: optional(input.login),
    user: optional(input.user),
    executionTimeoutSeconds: typeof input.timeoutMs === 'number' ? input.timeoutMs / 1000 : undefined,
    terminal: terminal === undefined ? undefined : { cols: terminal.cols, rows: terminal.rows },
    exitCode: optional(input.exitCode),
    signal: bareSignal(input.signal),
    reason: rawReason === undefined ? undefined : knownOrUnknown(rawReason, PROCESS_REASONS),
    rawReason,
    startedAt: optional(input.startedAt),
    exitedAt: optional(input.exitedAt),
    truncatedHead: input.truncatedHead === true,
    firstAvailableCursor: optional(input.firstAvailableCursor),
    lastCursor: optional(input.lastCursor),
  }
  return Object.fromEntries(Object.entries(info).filter(([, value]) => value !== undefined)) as unknown as ProcessInfo
}

/** One SSE event of a followed process log; `undefined` for an event this release ignores. A stream error throws its typed error. */
export function streamEvent(message: SseMessage): StreamLogEvent | undefined {
  const parsed = message.data ? JSON.parse(message.data) as Record<string, unknown> : {}
  const cursorValue = typeof parsed.cursor === 'string' ? parsed.cursor : message.id
  const cursor = cursorValue === undefined || cursorValue === '' ? {} : { cursor: cursorValue }
  switch (message.event) {
    case 'state': return { type: 'state', ...cursor, result: waitResult(parsed as toolbox.ExitResult) }
    case 'warning': return {
      type: 'warning', ...cursor,
      message: typeof parsed.message === 'string' ? parsed.message : '',
      ...(typeof parsed.firstAvailableCursor === 'string' ? { firstAvailableCursor: parsed.firstAvailableCursor } : {}),
    }
    case 'eof': return { type: 'eof', ...cursor }
    case 'error': throw createDaytonaError(
      typeof parsed.message === 'string' ? parsed.message : 'process log stream failed',
      undefined,
      typeof parsed.code === 'string' ? parsed.code : undefined,
      typeof parsed.source === 'string' ? parsed.source : 'DAYTONA_DAEMON',
    )
    case 'log': return { type: 'log', ...cursor, frame: frameFromWire({ ...parsed, cursor: cursorValue } as LogFrameWire) }
    default: return undefined
  }
}

export function base64Encode(data: Uint8Array): string {
  const maybeBuffer = (globalThis as { Buffer?: { from(input: Uint8Array): { toString(encoding: 'base64'): string } } }).Buffer
  if (maybeBuffer) return maybeBuffer.from(data).toString('base64')
  if (typeof btoa !== 'function') throw new DaytonaError('base64 encoding is unavailable in this runtime', { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
  let binary = ''
  for (const byte of data) binary += String.fromCharCode(byte)
  return btoa(binary)
}
