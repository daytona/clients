/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type WebSocket from 'isomorphic-ws'
import type { Process as ProcessRecord, ProcessLogFrame, ProcessResult } from '@daytona/toolbox-api-client'
import type { ProcessHandle } from '../ProcessHandle'

export const PROCESS_LOG_ENCODINGS = ['text', 'base64'] as const
export type ProcessLogEncoding = (typeof PROCESS_LOG_ENCODINGS)[number]

export const PROCESS_LIST_STATES = ['running', 'terminal', 'all'] as const
export type ProcessListState = (typeof PROCESS_LIST_STATES)[number]

export const PROCESS_KINDS = ['exec', 'pty', 'code'] as const
export type ProcessKindValue = (typeof PROCESS_KINDS)[number]

export const PROCESS_KEEP_LOGS = ['until_cleanup', 'on_exit_ttl', 'none'] as const
export type ProcessKeepLogsValue = (typeof PROCESS_KEEP_LOGS)[number]

export const PROCESS_STDIN_MODES = ['none', 'pipe'] as const
export type ProcessStdinModeValue = (typeof PROCESS_STDIN_MODES)[number]

export const PROCESS_SHELL_SELECTORS = ['auto', 'sh', 'bash', 'zsh'] as const
export type ProcessShellSelectorValue = (typeof PROCESS_SHELL_SELECTORS)[number]

export type ProcessTerminalOptions = {
  readonly cols?: number
  readonly rows?: number
  readonly term?: string
}

export type ProcessStartOptions = {
  readonly argv?: readonly string[]
  readonly shellCommand?: string
  readonly shell?: ProcessShellSelectorValue
  readonly login?: boolean
  readonly name?: string
  readonly sessionId?: string
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
  readonly user?: string
  readonly stdin?: ProcessStdinModeValue
  readonly timeoutMs?: number
  readonly kind?: ProcessKindValue
  readonly terminal?: ProcessTerminalOptions
  readonly keepLogs?: ProcessKeepLogsValue
}

export type ProcessRunOptions = ProcessStartOptions & {
  readonly waitTimeoutMs?: number
  readonly onStdout?: (data: string) => void | Promise<void>
  readonly onStderr?: (data: string) => void | Promise<void>
}

export type ProcessListFilter = {
  readonly state?: ProcessListState
  readonly kind?: ProcessKindValue
  readonly sessionId?: string
  readonly name?: string
  readonly pid?: number
}

export type ProcessLogOptions = {
  readonly cursor?: string
  readonly limit?: number
  readonly encoding?: ProcessLogEncoding
}

export type ProcessStreamLogOptions = {
  readonly cursor?: string
  readonly encoding?: ProcessLogEncoding
}

export type ProcessKillOptions = {
  readonly signal?: string
  readonly escalateAfterMs?: number
  readonly escalateTo?: string
}

export type ProcessWaitOptions = {
  readonly timeoutMs?: number
}

export type ProcessLogFrameEvent = {
  readonly type: 'log'
  readonly cursor: string
  readonly frame: ProcessLogFrame
}

export type ProcessLogStateEvent = {
  readonly type: 'state'
  readonly cursor: string
  readonly process: ProcessRecord
}

export type ProcessLogWarningEvent = {
  readonly type: 'warning'
  readonly cursor: string
  readonly message: string
  readonly firstAvailableCursor: string
}

export type ProcessLogEofEvent = {
  readonly type: 'eof'
  readonly cursor: string
}

export type ProcessLogEvent = ProcessLogFrameEvent | ProcessLogStateEvent | ProcessLogWarningEvent | ProcessLogEofEvent

export type ProcessOutput = {
  readonly stdout: string
  readonly stderr: string
  readonly exitCode?: number
  readonly signal?: string
  readonly reason?: string
}

export type ProcessRunResult = ProcessResult & {
  readonly id: string
  readonly handle: ProcessHandle
  readonly stdout: string
  readonly stderr: string
  /** True when `waitTimeoutMs` elapsed before the process exited on its own. */
  readonly timedOut: boolean
}

export type PtySocket = WebSocket
