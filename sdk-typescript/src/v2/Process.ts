/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport, strictUriEncode } from './http.js'
import type {
  ListResult, ProcessCommand, ProcessHandleJSON, ProcessInfo, ProcessListOptions, ProcessResult,
  ProcessStartOptions, PtyStartOptions, RunOptions,
} from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { BadRequestError, DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { ProcessHandle } from './ProcessHandle.js'
import type { PtyDuplex } from './ProcessHandle.js'
import { bodyFor, commandSpec, invalidPtyVerbError, knownProcessKind, processFromWire, terminalSpec } from './ProcessWire.js'
import { envelopeList } from './toolboxModels.js'
import { runProcess, startProcess } from './ProcessRun.js'
import type { ProcessBinding } from './ProcessRun.js'
import { requireSessionId, Session } from './Session.js'
import type { CreateSessionOptions, SessionJSON } from './Session.js'
import { assertInternal, INTERNAL } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, optionKeys } from './options.js'

// Preserve internal import paths used by the interpreter and other namespaces.
export { ProcessHandle } from './ProcessHandle.js'
export type { PtyDuplex } from './ProcessHandle.js'
export { WAIT_READ_SLACK_MS, base64Encode } from './ProcessWire.js'
export { TIMED_OUT_LOG_PAGE_LIMIT, collectAllLogs, startProcess } from './ProcessRun.js'

const START_OPTION_KEYS = optionKeys<ProcessStartOptions>()({
  name: true, cwd: true, env: true, user: true, shell: true, login: true, stdin: true,
  executionTimeoutSeconds: true, keepLogs: true,
})
const RUN_OPTION_KEYS = optionKeys<RunOptions>()({
  name: true, cwd: true, env: true, user: true, shell: true, login: true, stdin: true,
  executionTimeoutSeconds: true, keepLogs: true, timeoutSeconds: true,
})
const CREATE_SESSION_OPTION_KEYS = optionKeys<CreateSessionOptions>()({ cwd: true, env: true, shell: true, login: true, name: true })
const LIST_OPTION_KEYS = optionKeys<ProcessListOptions>()({ state: true, kind: true, sessionId: true, name: true, pid: true })
const PTY_START_OPTION_KEYS = optionKeys<PtyStartOptions>()({
  terminal: true, cwd: true, env: true, name: true, user: true, shell: true, login: true, stdin: true,
  executionTimeoutSeconds: true, keepLogs: true,
})
const DEFAULT_TERMINAL_SIZE = { cols: 80, rows: 24 } as const

function requireProcessId(value: unknown, field: 'sandboxId' | 'processId'): string {
  if (typeof value !== 'string' || value === '') {
    throw new BadRequestError(`process handle identity needs a non-empty string ${field}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value
}

/** Sandbox process namespace (`sandbox.process`). */
export class ProcessService {
  public readonly sandboxId: string
  private readonly transport: HttpTransport

  constructor(token: Internal, transport: HttpTransport, sandboxId: string) {
    assertInternal(token, 'ProcessService')
    this.transport = transport
    this.sandboxId = sandboxId
  }

  /**
   * Start a detached process and return a handle immediately.
   *
   * Use {@link ProcessHandle.streamLogs}, {@link ProcessHandle.wait}, or
   * {@link ProcessHandle.kill} to control it. Prefer this for long-running
   * servers: start once, then poll the port or stream logs for readiness.
   *
   * String commands (or `{ cmd }`) run through the sandbox shell (`bash`,
   * else `sh`; pin another with `shell`). Quote globs and brackets, such as
   * `pip install -e '.[test]'`. Pass `{ argv: [...] }` to bypass the shell
   * for untrusted arguments. Plain commands do not share shell state; use
   * {@link createSession} for a persistent shell.
   *
   * @param command Shell command string, `{ cmd }`, or shell-free `{ argv }`.
   * @param options.executionTimeoutSeconds Server-side deadline in seconds;
   *   kills the process tree on expiry.
   */
  public async start(command: ProcessCommand, options: ProcessStartOptions = {}): Promise<ProcessHandle> {
    assertKnownOptions('ProcessStartOptions', options, START_OPTION_KEYS)
    return startProcess(this.transport, this.sandboxId, { ...options, ...commandSpec(command) })
  }

  /**
   * Run a command, wait for its shell to exit, and collect its output.
   *
   * Normally one round trip: starts, waits, and returns inline logs. Larger
   * output or a process outliving the wait window falls back to split calls.
   * Output is bounded by per-process retention; a timed-out result collects
   * only the first few log pages rather than chasing a live writer.
   *
   * Strings (or `{ cmd }`) use the sandbox shell (`bash`, else `sh`; override
   * with `shell`). Quote globs and brackets (`pip install -e '.[test]'`).
   * `{ argv: [...] }` bypasses the shell for untrusted arguments. For shared
   * shell state, use {@link createSession} instead.
   *
   * Background children holding output descriptors open drain for at most
   * about two seconds after the shell exits; later output is not included.
   * Prefer {@link start} for long-running servers, or redirect their output
   * (`nohup cmd > server.log 2>&1 &`).
   *
   * @param command Shell command string, `{ cmd }`, or shell-free `{ argv }`.
   * @param options.timeoutSeconds Caller wait budget in seconds; omitted or 0
   *   waits unbounded. Expiry does not kill the process.
   * @param options.executionTimeoutSeconds Server-side deadline in seconds;
   *   kills the process tree on expiry.
   * @param options.keepLogs `'on_exit_ttl'` (default) or `'until_cleanup'`;
   *   the output is read back from the log, so `'none'` is refused.
   * @returns Output, stdout, stderr, logs and the exit result.
   */
  public async run(command: ProcessCommand, options: RunOptions = {}): Promise<ProcessResult> {
    assertKnownOptions('RunOptions', options, RUN_OPTION_KEYS)
    const { timeoutSeconds, keepLogs, ...start } = options
    return runProcess(this.binding(), { ...start, ...commandSpec(command), kind: 'exec' }, { timeoutSeconds, keepLogs })
  }

  /**
   * Create one persistent shell; cwd and env apply to all its commands.
   * `shell` must be a POSIX shell (bash, sh, dash, zsh, ksh) on Linux and
   * macOS; another shell is refused when the session is created.
   */
  public async createSession(options: CreateSessionOptions = {}): Promise<Session> {
    assertKnownOptions('CreateSessionOptions', options, CREATE_SESSION_OPTION_KEYS)
    const data = await this.transport.post<toolbox.ProcessDto>('/processes', bodyFor({ ...options, kind: 'session' }))
    return this.connectSession(processFromWire(data).id)
  }

  /** Reconnect a shell session by id or by `session.toJSON()`, without a request. */
  public connectSession(session: string | SessionJSON): Session {
    if (typeof session === 'string') return new Session(INTERNAL, this.binding(), requireSessionId(session, 'sessionId'))
    const sandboxId = requireSessionId(session?.sandboxId, 'sandboxId')
    const sessionId = requireSessionId(session?.sessionId, 'sessionId')
    if (sandboxId !== this.sandboxId) {
      throw new BadRequestError('session belongs to a different sandbox', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    return new Session(INTERNAL, this.binding(), sessionId)
  }

  /** Reconnect a process by id or by `handle.toJSON()`, without a request. */
  public connect(process: string | ProcessHandleJSON): ProcessHandle {
    if (typeof process === 'string') {
      return new ProcessHandle(INTERNAL, this.transport, this.sandboxId, requireProcessId(process, 'processId'))
    }
    if (requireProcessId(process?.sandboxId, 'sandboxId') !== this.sandboxId) {
      throw new BadRequestError('process handle belongs to a different sandbox', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    return new ProcessHandle(
      INTERNAL,
      this.transport,
      this.sandboxId,
      requireProcessId(process.processId, 'processId'),
      // Unknown serialized kinds read as absent; the API validates operations.
      knownProcessKind(process.kind),
    )
  }

  private binding(): ProcessBinding {
    return { transport: this.transport, sandboxId: this.sandboxId }
  }

  /** Fetch current process metadata by id or live name alias. */
  public async get(id: string): Promise<ProcessInfo> {
    return processFromWire(await this.transport.get<toolbox.ProcessDto>(`/processes/${strictUriEncode(id)}`))
  }

  public async list(options: ProcessListOptions = {}): Promise<ListResult<ProcessInfo>> {
    assertKnownOptions('ProcessListOptions', options, LIST_OPTION_KEYS)
    const query = encodeQuery({ state: options.state, kind: options.kind, sessionId: options.sessionId, name: options.name, pid: options.pid })
    const data = await this.transport.get<toolbox.ProcessListResponse>(`/processes${query}`)
    return { items: envelopeList(data, 'items', 'process list response', processFromWire) }
  }
}

/** PTY namespace (`sandbox.pty`): interactive terminals. */
export class PtyService {
  private readonly process: ProcessService
  private readonly transport: HttpTransport

  constructor(token: Internal, process: ProcessService, transport: HttpTransport) {
    assertInternal(token, 'PtyService')
    this.process = process
    this.transport = transport
  }

  /** Start a detached PTY process; without `command` it runs the sandbox shell. The terminal defaults to 80×24. */
  public async start(command?: string, options: PtyStartOptions = {}): Promise<ProcessHandle> {
    assertKnownOptions('PtyStartOptions', options, PTY_START_OPTION_KEYS)
    const { terminal, stdin, keepLogs, ...rest } = options
    return startProcess(this.transport, this.process.sandboxId, {
      ...rest,
      ...(command === undefined ? {} : { cmd: command }),
      kind: 'pty',
      terminal: terminalSpec(terminal ?? DEFAULT_TERMINAL_SIZE),
      stdin: stdin ?? 'open',
      keepLogs: keepLogs ?? 'until_cleanup',
    })
  }

  /** Start a PTY and connect to its terminal; the PTY is deleted again if the connection fails. */
  public async attach(command?: string, options: PtyStartOptions = {}): Promise<{ handle: ProcessHandle; connection: PtyDuplex }> {
    const handle = await this.start(command, options)
    try {
      return { handle, connection: await handle.attach() }
    } catch (error) {
      await handle.delete().catch(() => undefined)
      throw error
    }
  }

  /** Reconnect a PTY by id or by `handle.toJSON()`, without a request. */
  public connect(process: string | ProcessHandleJSON): ProcessHandle {
    return this.process.connect(process)
  }

  public async list(): Promise<ListResult<ProcessInfo>> {
    return this.process.list({ kind: 'pty' })
  }

  /** Stop a PTY and delete it and its log. */
  public async delete(process: string | ProcessHandleJSON): Promise<void> {
    const handle = this.connect(process)
    // Check kind before the destructive action: this namespace only owns PTYs.
    const info = await handle.get()
    if (info.kind !== 'pty') throw invalidPtyVerbError('delete', info.kind)
    try {
      await handle.kill()
    } catch (error) {
      if (!(error instanceof DaytonaError) || error.code !== 'PROCESS_TERMINAL') throw error
    }
    await handle.delete()
  }
}
