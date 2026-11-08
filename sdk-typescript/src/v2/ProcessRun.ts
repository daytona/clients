/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { HttpTransport } from './http.js'
import { encodeQuery } from './http.js'
import type * as toolbox from './gen/toolbox-types.js'
import type { LogFrame, ProcessCommand, RunOptions, RunResult, WaitResult } from './types.js'
import { UnsupportedOperationError } from './errors/DaytonaError.js'
import { ProcessHandle } from './ProcessHandle.js'
import { bodyFor, commandSource, frameFromWire, startedProcessKind, waitDeadline, waitResult, WAIT_READ_SLACK_MS } from './ProcessWire.js'
import type { InternalProcessStartOptions } from './ProcessWire.js'

/** Bound toolbox identity shared by ordinary commands and shell-session commands. */
export interface ProcessBinding {
  readonly transport: HttpTransport
  readonly sandboxId: string
}
type InternalRunOptions = RunOptions & { readonly sessionId?: string }

/** Bound snapshots prevent a live process from out-writing the log paginator. */
export const TIMED_OUT_LOG_PAGE_LIMIT = 8

export async function collectAllLogs(handle: ProcessHandle, options: { maxPages?: number } = {}): Promise<LogFrame[]> {
  const logs: LogFrame[] = []
  let cursor: string | undefined
  for (let pages = 0; ; pages++) {
    if (options.maxPages !== undefined && pages >= options.maxPages) return logs
    const page = await handle.logs({ cursor })
    logs.push(...page.items)
    if (!page.nextCursor || page.nextCursor === cursor || page.items.length === 0) return logs
    cursor = page.nextCursor
  }
}

// Existing interpreter and PTY callers pass transport, identity, source, options,
// and HTTP budget independently; preserve this internal entrypoint's signature.
export async function startProcess(
  transport: HttpTransport,
  sandboxId: string,
  commandOrOptions: string | InternalProcessStartOptions,
  options?: InternalProcessStartOptions,
  config?: { readonly timeout?: number },
): Promise<ProcessHandle> {
  const body = bodyFor(commandOrOptions, options)
  const data = await transport.post<toolbox.ProcessDto>('/processes', body, config)
  return new ProcessHandle(transport, sandboxId, data.id, startedProcessKind(data, body))
}

export async function runProcess(binding: ProcessBinding, command: ProcessCommand, options: InternalRunOptions = {}): Promise<RunResult> {
  const body = bodyFor(commandSource(command), {
    ...options,
    keepLogs: options.keepLogs === 'none' ? 'on_exit_ttl' : (options.keepLogs ?? 'on_exit_ttl'),
  })
  // Reject invalid caller budgets locally before issuing any request.
  waitDeadline(options.timeout)
  // Runtime JS callers can pass a kind the TypeScript surface excludes.
  const requestedKind: string | undefined = options.kind
  if (requestedKind === 'pty') {
    throw new UnsupportedOperationError(
      'process.run() does not create PTYs; use sandbox.pty.start() or sandbox.pty.attach()',
      { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' },
    )
  }
  const requestedTimeout = options.timeout
  const query = encodeQuery({ waitMs: requestedTimeout === undefined ? 0 : Math.ceil(requestedTimeout), logs: 'text' })
  const startedAt = Date.now()
  // The HTTP timeout must exceed the requested wait to allow its response.
  const started = await binding.transport.post<toolbox.ProcessDto>(`/processes${query}`, body, {
    timeout: requestedTimeout === undefined || requestedTimeout === 0 ? 0 : requestedTimeout + WAIT_READ_SLACK_MS,
  })
  const handle = new ProcessHandle(binding.transport, binding.sandboxId, started.id, startedProcessKind(started, body))
  const settled = started.state === 'terminal'
  const remainingTimeout = requestedTimeout === undefined || requestedTimeout === 0
    ? undefined
    : Math.max(0, requestedTimeout - (Date.now() - startedAt))
  const result: WaitResult = settled
    ? waitResult(started)
    : remainingTimeout !== undefined && remainingTimeout <= 0
    ? { reason: 'timed_out' }
    : await handle.wait({ timeout: remainingTimeout })
  const logs = settled && started.logs?.eof
    ? started.logs.frames.map(frameFromWire)
    : await collectAllLogs(handle, result.reason === 'timed_out' ? { maxPages: TIMED_OUT_LOG_PAGE_LIMIT } : {})
  const stdout = logs.filter((frame) => frame.channel === 'stdout' || frame.channel === 'pty').map((frame) => frame.data).join('')
  const stderr = logs.filter((frame) => frame.channel === 'stderr').map((frame) => frame.data).join('')
  const output = logs
    .filter((frame) => frame.channel === 'stdout' || frame.channel === 'stderr' || frame.channel === 'pty')
    .map((frame) => frame.data).join('')
  return { ...result, processId: handle.id, logs, stdout, stderr, output }
}
