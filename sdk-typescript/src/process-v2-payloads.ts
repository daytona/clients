/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Process as ProcessRecord, ProcessLogFrame } from '@daytona/toolbox-api-client'
import { DaytonaError } from './errors/DaytonaError'
import type {
  ProcessLogEofEvent,
  ProcessLogEvent,
  ProcessLogStateEvent,
  ProcessLogWarningEvent,
} from './types/ProcessV2'
import { PROCESS_KEEP_LOGS, PROCESS_KINDS, PROCESS_SHELL_SELECTORS, PROCESS_STDIN_MODES } from './types/ProcessV2'

const PROCESS_STATES = ['running', 'terminal'] as const
const PROCESS_REASONS = ['exited', 'signaled', 'timed_out', 'sandbox_stopped', 'failed'] as const
const PROCESS_LOG_CHANNELS = ['stdout', 'stderr', 'pty', 'system'] as const

type JsonRecord = Record<string, unknown>

function isJsonRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object'
}

function expectObject(value: unknown, message: string): JsonRecord {
  if (!isJsonRecord(value)) {
    throw new DaytonaError(message)
  }
  return value
}

function requireString(payload: JsonRecord, key: string): string {
  const value = payload[key]
  if (typeof value !== 'string') {
    throw new DaytonaError(`Invalid process v2 SSE payload: ${key} must be a string`)
  }
  return value
}

function optionalString(payload: JsonRecord, key: string): string | undefined {
  const value = payload[key]
  return typeof value === 'string' ? value : undefined
}

function optionalNumber(payload: JsonRecord, key: string): number | undefined {
  const value = payload[key]
  return typeof value === 'number' ? value : undefined
}

function optionalBoolean(payload: JsonRecord, key: string): boolean | undefined {
  const value = payload[key]
  return typeof value === 'boolean' ? value : undefined
}

function optionalStringArray(payload: JsonRecord, key: string): string[] | undefined {
  const value = payload[key]
  if (!Array.isArray(value)) {
    return undefined
  }
  const result: string[] = []
  for (const entry of value) {
    if (typeof entry !== 'string') {
      throw new DaytonaError(`Invalid process v2 SSE payload: ${key} must contain only strings`)
    }
    result.push(entry)
  }
  return result
}

function optionalStringRecord(payload: JsonRecord, key: string): { [key: string]: string } | undefined {
  const value = payload[key]
  if (value === null || typeof value !== 'object') {
    return undefined
  }

  const record: { [key: string]: string } = {}
  for (const [entryKey, entryValue] of Object.entries(value)) {
    if (typeof entryValue !== 'string') {
      throw new DaytonaError(`Invalid process v2 SSE payload: ${key}.${entryKey} must be a string`)
    }
    record[entryKey] = entryValue
  }
  return record
}

function parseLiteralUnion<T extends string>(value: unknown, key: string, allowed: readonly T[]): T {
  if (typeof value !== 'string') {
    throw new DaytonaError(`Invalid process v2 SSE payload: ${key} must be a string`)
  }
  const match = allowed.find((candidate) => candidate === value)
  if (match === undefined) {
    throw new DaytonaError(`Invalid process v2 SSE payload: ${key} has unsupported value ${value}`)
  }
  return match
}

function optionalLiteralUnion<T extends string>(
  payload: JsonRecord,
  key: string,
  allowed: readonly T[],
): T | undefined {
  const value = payload[key]
  if (value === undefined) {
    return undefined
  }
  return parseLiteralUnion(value, key, allowed)
}

function optionalTerminal(payload: JsonRecord, key: string): ProcessRecord['terminal'] {
  const value = payload[key]
  if (!isJsonRecord(value)) {
    return undefined
  }

  const terminalPayload = value
  const terminal: NonNullable<ProcessRecord['terminal']> = {}
  const cols = optionalNumber(terminalPayload, 'cols')
  const rows = optionalNumber(terminalPayload, 'rows')
  const term = optionalString(terminalPayload, 'term')

  if (cols !== undefined) {
    terminal.cols = cols
  }
  if (rows !== undefined) {
    terminal.rows = rows
  }
  if (term !== undefined) {
    terminal.term = term
  }
  return terminal
}

export function parseProcessLogFrame(value: unknown): ProcessLogFrame {
  const payload = expectObject(value, 'Invalid process v2 SSE payload: log frame must be an object')

  const frame: ProcessLogFrame = {
    channel: parseLiteralUnion(payload.channel, 'channel', PROCESS_LOG_CHANNELS),
    cursor: requireString(payload, 'cursor'),
    seq: (() => {
      const seq = payload.seq
      if (typeof seq !== 'number') {
        throw new DaytonaError('Invalid process v2 SSE payload: seq must be a number')
      }
      return seq
    })(),
    timestamp: requireString(payload, 'timestamp'),
  }

  const data = optionalString(payload, 'data')
  const encoding = optionalString(payload, 'encoding')
  if (data !== undefined) {
    frame.data = data
  }
  if (encoding !== undefined) {
    frame.encoding = encoding
  }
  return frame
}

export function parseProcessRecord(value: unknown): ProcessRecord {
  const payload = expectObject(value, 'Invalid process v2 SSE payload: process must be an object')

  const process: ProcessRecord = {
    createdAt: requireString(payload, 'createdAt'),
    id: requireString(payload, 'id'),
    kind: parseLiteralUnion(payload.kind, 'kind', PROCESS_KINDS),
    state: parseLiteralUnion(payload.state, 'state', PROCESS_STATES),
  }

  const argv = optionalStringArray(payload, 'argv')
  const cwd = optionalString(payload, 'cwd')
  const env = optionalStringRecord(payload, 'env')
  const exitCode = optionalNumber(payload, 'exitCode')
  const exitedAt = optionalString(payload, 'exitedAt')
  const firstAvailableCursor = optionalString(payload, 'firstAvailableCursor')
  const keepLogs = optionalLiteralUnion(payload, 'keepLogs', PROCESS_KEEP_LOGS)
  const lastCursor = optionalString(payload, 'lastCursor')
  const legacyCommandId = optionalString(payload, 'legacyCommandId')
  const login = optionalBoolean(payload, 'login')
  const name = optionalString(payload, 'name')
  const pid = optionalNumber(payload, 'pid')
  const reason = optionalLiteralUnion(payload, 'reason', PROCESS_REASONS)
  const resolvedShell = optionalString(payload, 'resolvedShell')
  const sessionId = optionalString(payload, 'sessionId')
  const shell = optionalLiteralUnion(payload, 'shell', PROCESS_SHELL_SELECTORS)
  const shellCommand = optionalString(payload, 'shellCommand')
  const signal = optionalString(payload, 'signal')
  const startedAt = optionalString(payload, 'startedAt')
  const stdin = optionalLiteralUnion(payload, 'stdin', PROCESS_STDIN_MODES)
  const system = optionalBoolean(payload, 'system')
  const terminal = optionalTerminal(payload, 'terminal')
  const timeoutMs = optionalNumber(payload, 'timeoutMs')
  const truncatedHead = optionalBoolean(payload, 'truncatedHead')
  const user = optionalString(payload, 'user')

  if (argv !== undefined) process.argv = argv
  if (cwd !== undefined) process.cwd = cwd
  if (env !== undefined) process.env = env
  if (exitCode !== undefined) process.exitCode = exitCode
  if (exitedAt !== undefined) process.exitedAt = exitedAt
  if (firstAvailableCursor !== undefined) process.firstAvailableCursor = firstAvailableCursor
  if (keepLogs !== undefined) process.keepLogs = keepLogs
  if (lastCursor !== undefined) process.lastCursor = lastCursor
  if (legacyCommandId !== undefined) process.legacyCommandId = legacyCommandId
  if (login !== undefined) process.login = login
  if (name !== undefined) process.name = name
  if (pid !== undefined) process.pid = pid
  if (reason !== undefined) process.reason = reason
  if (resolvedShell !== undefined) process.resolvedShell = resolvedShell
  if (sessionId !== undefined) process.sessionId = sessionId
  if (shell !== undefined) process.shell = shell
  if (shellCommand !== undefined) process.shellCommand = shellCommand
  if (signal !== undefined) process.signal = signal
  if (startedAt !== undefined) process.startedAt = startedAt
  if (stdin !== undefined) process.stdin = stdin
  if (system !== undefined) process.system = system
  if (terminal !== undefined) process.terminal = terminal
  if (timeoutMs !== undefined) process.timeoutMs = timeoutMs
  if (truncatedHead !== undefined) process.truncatedHead = truncatedHead
  if (user !== undefined) process.user = user

  return process
}

function parseWarningEvent(value: unknown): ProcessLogWarningEvent {
  const payload = expectObject(value, 'Invalid process v2 SSE payload: warning must be an object')
  return {
    type: 'warning',
    cursor: requireString(payload, 'cursor'),
    firstAvailableCursor: requireString(payload, 'firstAvailableCursor'),
    message: requireString(payload, 'message'),
  }
}

function parseStateEvent(value: unknown): ProcessLogStateEvent {
  const payload = expectObject(value, 'Invalid process v2 SSE payload: state must be an object')
  const cursor = requireString(payload, 'cursor')
  const processPayload: JsonRecord = {}
  for (const [key, entryValue] of Object.entries(payload)) {
    if (key !== 'cursor') {
      processPayload[key] = entryValue
    }
  }

  return {
    type: 'state',
    cursor,
    process: parseProcessRecord(processPayload),
  }
}

function parseEofEvent(value: unknown): ProcessLogEofEvent {
  const payload = expectObject(value, 'Invalid process v2 SSE payload: eof must be an object')
  return {
    type: 'eof',
    cursor: requireString(payload, 'cursor'),
  }
}

export function parseProcessLogEvent(eventName: string, data: string): ProcessLogEvent {
  const parsed: unknown = JSON.parse(data)

  switch (eventName) {
    case 'log': {
      const frame = parseProcessLogFrame(parsed)
      return {
        type: 'log',
        cursor: frame.cursor,
        frame,
      }
    }
    case 'state':
      return parseStateEvent(parsed)
    case 'warning':
      return parseWarningEvent(parsed)
    case 'eof':
      return parseEofEvent(parsed)
    default:
      throw new DaytonaError(`Unsupported process v2 SSE event: ${eventName}`)
  }
}
