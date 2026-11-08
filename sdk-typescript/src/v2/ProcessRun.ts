/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { HttpTransport } from './http.js'
import { encodeQuery } from './http.js'
import type * as toolbox from './gen/toolbox-types.js'
import type { LogFrame, ProcessResult, RunKeepLogs, WaitResult } from './types.js'
import { ProcessHandle } from './ProcessHandle.js'
import {
  bodyFor, frameFromWire, runKeepLogs, secondsToMs, startedProcessKind, waitResult, WAIT_READ_SLACK_MS,
} from './ProcessWire.js'
import type { ProcessSpec } from './ProcessWire.js'
import { INTERNAL } from './internal.js'
import { logPageFromWire, record, str } from './toolboxModels.js'

/** Bound toolbox identity shared by ordinary commands and shell-session commands. */
export interface ProcessBinding {
  readonly transport: HttpTransport
  readonly sandboxId: string
}

/** Bound snapshots prevent a live process from out-writing the log paginator. */
export const TIMED_OUT_LOG_PAGE_LIMIT = 8

export async function collectAllLogs(handle: ProcessHandle, options: { maxPages?: number } = {}): Promise<LogFrame[]> {
  const logs: LogFrame[] = []
  let cursor: string | undefined
  for (let pages = 0; ; pages++) {
    if (options.maxPages !== undefined && pages >= options.maxPages) return logs
    const page = await handle.logs(cursor === undefined ? {} : { cursor })
    logs.push(...page.items)
    if (page.eof || !page.nextCursor || page.nextCursor === cursor || page.items.length === 0) return logs
    cursor = page.nextCursor
  }
}

/** Create a process; `config.timeout` (ms, `0` = unbounded) bounds the create request itself. */
export async function startProcess(
  transport: HttpTransport,
  sandboxId: string,
  spec: ProcessSpec,
  config?: { readonly timeout?: number },
): Promise<ProcessHandle> {
  const body = bodyFor(spec)
  const data = await transport.post<toolbox.ProcessDto>('/processes', body, config)
  return new ProcessHandle(INTERNAL, transport, sandboxId, str(record(data, 'process response'), 'id', 'process response'), startedProcessKind(data, body))
}

/** How long `run()` without a `timeoutSeconds` holds its first request, as the Python SDK does. */
const RUN_WAIT_MS = 60_000

/** `process.run()` / `session.run()`: start, wait within `timeoutSeconds`, collect the output. */
export async function runProcess(
  binding: ProcessBinding,
  spec: ProcessSpec,
  options: { readonly timeoutSeconds?: number; readonly keepLogs?: RunKeepLogs },
): Promise<ProcessResult> {
  const body = bodyFor({ ...spec, keepLogs: runKeepLogs(options.keepLogs) })
  // Reject invalid caller budgets locally before issuing any request.
  const requestedTimeout = secondsToMs('timeoutSeconds', options.timeoutSeconds)
  // Without a budget the first request still holds for one wait window (as
  // the Python SDK does), so a short command is one round trip; the rest of
  // an unbounded wait continues with wait().
  const holdMs = requestedTimeout ?? RUN_WAIT_MS
  const query = encodeQuery({ waitMs: holdMs, logs: 'text' })
  const startedAt = Date.now()
  // The HTTP timeout must exceed the requested wait to allow its response.
  const started = await binding.transport.post<toolbox.ProcessDto>(`/processes${query}`, body, {
    timeout: holdMs === 0 ? 0 : holdMs + WAIT_READ_SLACK_MS,
  })
  const handle = new ProcessHandle(
    INTERNAL, binding.transport, binding.sandboxId, str(record(started, 'process response'), 'id', 'process response'), startedProcessKind(started, body),
  )
  const settled = started.state === 'terminal'
  const remainingTimeout = requestedTimeout === undefined || requestedTimeout === 0
    ? undefined
    : Math.max(0, requestedTimeout - (Date.now() - startedAt))
  const result: WaitResult = settled
    ? waitResult(started)
    : remainingTimeout !== undefined && remainingTimeout <= 0
    ? { reason: 'timed_out' }
    : await handle.wait(remainingTimeout === undefined ? {} : { timeoutSeconds: remainingTimeout / 1000 })
  const inline = started.logs === undefined || started.logs === null ? undefined : logPageFromWire(started.logs)
  const logs = settled && inline?.eof
    ? inline.items
    : await collectAllLogs(handle, result.reason === 'timed_out' ? { maxPages: TIMED_OUT_LOG_PAGE_LIMIT } : {})
  const stdout = logs.filter((frame) => frame.channel === 'stdout' || frame.channel === 'pty').map((frame) => frame.data).join('')
  const stderr = logs.filter((frame) => frame.channel === 'stderr').map((frame) => frame.data).join('')
  const output = logs
    .filter((frame) => frame.channel === 'stdout' || frame.channel === 'stderr' || frame.channel === 'pty')
    .map((frame) => frame.data).join('')
  return { ...result, processId: handle.id, logs, stdout, stderr, output }
}
