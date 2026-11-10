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
import { UnsupportedOperationError, createDaytonaError } from './errors/DaytonaError.js'
import { knownOrUnknown } from './knownValue.js'
import { MAX_INT32, MAX_TIMER_DELAY_MS } from './options.js'
import { bool, int, int32, logFrameFromWire, malformedResponse, nonEmptyStr, nonNegativeInt, nonNegativeSafeInt, optional, positiveInt, record, str, strList, timestamp } from './toolboxModels.js'
import type { WireRecord } from './toolboxModels.js'
import { assertKnownOptions, badRequest, isPlainStringRecord, optionKeys } from './options.js'
import { deepFrozen } from './frozen.js'
import { nestedDeeperThan } from './jsonDepth.js'

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
  readonly tag?: string
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

const PROCESS_TAG = /^[A-Za-z0-9._-]{1,128}$/

/**
 * A process tag, as the toolbox takes it: a safe identifier
 * (`[A-Za-z0-9._-]`, 1-128 characters, not `.` or `..`). Anything else is
 * refused before any request (as Python's `process_tag`).
 */
export function processTag(value: string): string {
  if (typeof value !== 'string' || !PROCESS_TAG.test(value) || value === '.' || value === '..') {
    throw badRequest("tag must be 1-128 of the characters A-Z, a-z, 0-9, '.', '_' and '-', and not '.' or '..'")
  }
  return value
}

/** The default `timeoutMs` of `killAll()` / `kill_all()` and the longest the toolbox takes, in seconds. */
export const KILL_ALL_DEFAULT_TIMEOUT_SECONDS = 10
export const KILL_ALL_MAX_TIMEOUT_SECONDS = 60

/** Extra HTTP read budget for a bounded process long-poll. */
export const WAIT_READ_SLACK_MS = 5_000
export const WAIT_TRANSPORT_RETRY_LIMIT = 3

/**
 * A duration in seconds as whole wire milliseconds, rounded up (`undefined`
 * stays unset). A positive duration is at least 1 ms, so it never reads as
 * `0`; the result is finite.
 */
export function exactMs(name: string, seconds: number | undefined): number | undefined {
  if (seconds === undefined) return undefined
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    throw badRequest(`${name} must be a non-negative finite number of seconds`)
  }
  if (seconds === 0) return 0
  // The largest wire value both SDKs send exactly, checked before rounding (as Python does).
  if (seconds * 1000 > Number.MAX_SAFE_INTEGER) throw badRequest(`${name} is too large`)
  // Round away float noise (0.1 * 1000 = 100.00000000000001) before rounding up.
  return Math.max(1, Math.ceil(Math.round(seconds * 1000 * 1e6) / 1e6))
}

/**
 * A caller budget or deadline in seconds as wire milliseconds, like
 * {@link exactMs}, except that a budget above the longest timer
 * ({@link MAX_TIMER_DELAY_MS}, about 24.8 days) is no limit (`0`), as the
 * client's request timeout is.
 */
export function secondsToMs(name: string, seconds: number | undefined): number | undefined {
  return aboveTimerMaximum(name, seconds) ? 0 : exactMs(name, seconds)
}

/** Whether a valid duration in seconds exceeds the longest timer (no limit); checked before any conversion. */
export function aboveTimerMaximum(name: string, seconds: number | undefined): boolean {
  if (seconds === undefined) return false
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    throw badRequest(`${name} must be a non-negative finite number of seconds`)
  }
  return seconds * 1000 > MAX_TIMER_DELAY_MS
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
  const raw = record(input, 'wait result')
  const rawReason = optional(raw, 'reason', str, 'wait result')
  const exitCode = optional(raw, 'exitCode', int32, 'wait result')
  const signal = bareSignal(optional(raw, 'signal', str, 'wait result'))
  return deepFrozen({
    reason: rawReason === undefined ? 'unknown' : knownOrUnknown(rawReason, PROCESS_REASONS),
    ...(rawReason === undefined ? {} : { rawReason }),
    ...(exitCode === undefined ? {} : { exitCode }),
    ...(signal === undefined ? {} : { signal }),
  })
}

export function frameFromWire(input: LogFrameWire): LogFrame {
  return logFrameFromWire(input)
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
    const argv: unknown = command.argv
    if (!Array.isArray(argv) || argv.length === 0 || !argv.every((item) => typeof item === 'string')) {
      throw badRequest('argv must be a non-empty array of strings')
    }
    return { argv: [...argv] as string[] }
  }
  return { cmd: command.cmd }
}


/** The largest terminal dimension the sandbox takes (an unsigned 16-bit value). */
export const TERMINAL_DIMENSION_MAX = 65535

/** A terminal's `cols` / `rows`: an integer from 1 to 65535. */
export function terminalDimension(value: unknown, name: 'cols' | 'rows'): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > TERMINAL_DIMENSION_MAX) {
    throw badRequest(`${name} must be an integer between 1 and ${TERMINAL_DIMENSION_MAX}`)
  }
  return value
}

export function terminalSpec(terminal: TerminalSize): TerminalSize {
  assertKnownOptions('TerminalSize', terminal, TERMINAL_SIZE_KEYS)
  return { cols: terminalDimension(terminal.cols, 'cols'), rows: terminalDimension(terminal.rows, 'rows') }
}

/** A process `env`: a plain object of strings (a `Map`, an array or a non-string value is refused, not mangled). */
function processEnv(env: unknown): Record<string, string> {
  if (!isPlainStringRecord(env)) throw badRequest('env must be a plain object of string to string')
  return { ...env }
}

/** `run()` returns no handle to write to, so it cannot keep stdin open. */
export function runStdin(stdin: unknown): 'closed' {
  if (stdin === undefined || stdin === 'closed') return 'closed'
  const shown = typeof stdin === 'string' ? `'${stdin}'` : 'other than \'closed\''
  throw badRequest(`stdin ${shown} is not supported by run(); it returns no handle to write to. Use start() for a process with open stdin`)
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
  // 0 and a value above the longest timer mean no deadline, as for every SDK
  // duration: timeoutMs is omitted (a 0 on the wire is not relied on).
  const timeoutMs = aboveTimerMaximum('executionTimeoutSeconds', spec.executionTimeoutSeconds) || spec.executionTimeoutSeconds === 0
    ? undefined
    : exactMs('executionTimeoutSeconds', spec.executionTimeoutSeconds)
  const body: toolbox.CreateProcessRequest = {
    shellCommand: spec.cmd, argv: spec.argv === undefined ? undefined : [...spec.argv], code: spec.code, kind: spec.kind,
    name: spec.name, tag: spec.tag === undefined ? undefined : processTag(spec.tag),
    sessionId: spec.sessionId, cwd: spec.cwd, env: spec.env === undefined ? undefined : processEnv(spec.env),
    user: spec.user, shell: spec.shell, login: spec.login,
    // Sessions take no stdin mode.
    stdin: spec.kind === 'session' ? undefined : spec.stdin,
    // Code cells have no server-side deadline. Shell-session commands do.
    timeoutMs: hasCode ? undefined : timeoutMs,
    terminal: spec.terminal, language: spec.language, keepLogs: spec.keepLogs,
  }
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined))
}

const PROCESS = 'process response'

function terminalFromWire(input: WireRecord): TerminalSize | undefined {
  const value = input.terminal
  if (value === undefined || value === null) return undefined
  const d = `${PROCESS}: terminal`
  const terminal = record(value, d)
  // int32 dimensions of at least 1, as the request's (terminalDimension).
  const dimension = (field: 'cols' | 'rows'): number => {
    const size = positiveInt(terminal, field, d)
    if (size > MAX_INT32) throw malformedResponse(`${d}: ${field} must be an integer between 1 and ${MAX_INT32}`)
    return size
  }
  return { cols: dimension('cols'), rows: dimension('rows') }
}

export function processFromWire(input: toolbox.ProcessDto): ProcessInfo {
  const raw = record(input, PROCESS)
  const rawKind = str(raw, 'kind', PROCESS)
  const rawState = str(raw, 'state', PROCESS)
  const rawStdin = str(raw, 'stdin', PROCESS)
  const rawKeepLogs = str(raw, 'keepLogs', PROCESS)
  const rawReason = optional(raw, 'reason', str, PROCESS)
  const timeoutMs = optional(raw, 'timeoutMs', nonNegativeSafeInt, PROCESS)
  const argv = optional(raw, 'argv', strList, PROCESS)
  const info: ProcessInfo = {
    id: nonEmptyStr(raw, 'id', PROCESS),
    kind: knownOrUnknown(rawKind, PROCESS_KINDS), rawKind,
    state: knownOrUnknown(rawState, PROCESS_STATES), rawState,
    cwd: str(raw, 'cwd', PROCESS),
    stdin: knownOrUnknown(rawStdin, PROCESS_STDIN_MODES), rawStdin,
    keepLogs: knownOrUnknown(rawKeepLogs, PROCESS_KEEP_LOGS), rawKeepLogs,
    createdAt: timestamp(raw, 'createdAt', PROCESS),
    system: optional(raw, 'system', bool, PROCESS) === true,
    name: optional(raw, 'name', str, PROCESS),
    tag: optional(raw, 'tag', str, PROCESS),
    pid: optional(raw, 'pid', int32, PROCESS),
    sessionId: optional(raw, 'sessionId', str, PROCESS),
    argv,
    shellCommand: optional(raw, 'shellCommand', str, PROCESS),
    code: optional(raw, 'code', str, PROCESS),
    language: optional(raw, 'language', str, PROCESS),
    shell: optional(raw, 'shell', str, PROCESS),
    resolvedShell: optional(raw, 'resolvedShell', str, PROCESS),
    login: optional(raw, 'login', bool, PROCESS),
    user: optional(raw, 'user', str, PROCESS),
    executionTimeoutSeconds: timeoutMs === undefined ? undefined : timeoutMs / 1000,
    terminal: terminalFromWire(raw),
    exitCode: optional(raw, 'exitCode', int32, PROCESS),
    signal: bareSignal(optional(raw, 'signal', str, PROCESS)),
    reason: rawReason === undefined ? undefined : knownOrUnknown(rawReason, PROCESS_REASONS),
    rawReason,
    startedAt: optional(raw, 'startedAt', timestamp, PROCESS),
    exitedAt: optional(raw, 'exitedAt', timestamp, PROCESS),
    truncatedHead: bool(raw, 'truncatedHead', PROCESS),
    firstAvailableCursor: optional(raw, 'firstAvailableCursor', str, PROCESS),
    lastCursor: optional(raw, 'lastCursor', str, PROCESS),
  }
  return deepFrozen(Object.fromEntries(Object.entries(info).filter(([, value]) => value !== undefined)) as unknown as ProcessInfo)
}

/**
 * One SSE event of a followed process log; `undefined` for an event this
 * release ignores. An event's cursor is its payload's, else the SSE `id:`
 * line's; an event missing a field the contract requires throws the
 * malformed-response error, and a stream error throws its typed error.
 */
export function streamEvent(message: SseMessage): StreamLogEvent | undefined {
  if (!['log', 'state', 'warning', 'eof', 'error'].includes(message.event)) return undefined
  const description = `process log ${message.event} event`
  let parsed: unknown
  try {
    parsed = message.data ? JSON.parse(message.data) : {}
  } catch {
    throw malformedResponse(`${description}: the data is not JSON`)
  }
  if (nestedDeeperThan(parsed)) throw malformedResponse(`${description}: the JSON is nested too deeply`)
  const payload = record(parsed, description)
  if (message.event === 'error') {
    // The stream's own error is thrown as its typed error; its cursor is
    // neither read nor returned, so a missing one does not mask it.
    throw createDaytonaError(
      str(payload, 'message', description),
      undefined,
      typeof payload.code === 'string' ? payload.code : undefined,
      typeof payload.source === 'string' ? payload.source : 'DAYTONA_DAEMON',
    )
  }
  // The payload's cursor, else (only when the key is missing) the SSE id; a
  // JSON null is a present, invalid cursor.
  const hasCursor = Object.prototype.hasOwnProperty.call(payload, 'cursor')
  if (hasCursor && (typeof payload.cursor !== 'string' || payload.cursor === '')) {
    throw malformedResponse(`${description}: cursor must be a non-empty string`)
  }
  const cursor = hasCursor ? payload.cursor as string : message.id
  if (cursor === undefined || cursor === '') throw malformedResponse(`${description}: cursor is required`)
  switch (message.event) {
    case 'log': return deepFrozen({ type: 'log', cursor, frame: frameFromWire({ ...payload, cursor } as LogFrameWire) })
    // ProcessStateEventDto is the whole process record plus the cursor:
    // every field the record requires is checked, timestamps included.
    case 'state': {
      processFromWire(payload as toolbox.ProcessDto)
      return deepFrozen({ type: 'state', cursor, result: waitResult(payload as toolbox.ExitResult) })
    }
    case 'eof': return deepFrozen({ type: 'eof', cursor })
    default: return deepFrozen({
      type: 'warning', cursor,
      message: str(payload, 'message', description),
      firstAvailableCursor: str(payload, 'firstAvailableCursor', description),
    })
  }
}

export function base64Encode(data: Uint8Array): string {
  const maybeBuffer = (globalThis as { Buffer?: { from(input: Uint8Array): { toString(encoding: 'base64'): string } } }).Buffer
  if (maybeBuffer) return maybeBuffer.from(data).toString('base64')
  if (typeof btoa !== 'function') throw createDaytonaError('base64 encoding is unavailable in this runtime', 400, 'UNSUPPORTED_OPERATION', 'SDK')
  let binary = ''
  for (const byte of data) binary += String.fromCharCode(byte)
  return btoa(binary)
}
