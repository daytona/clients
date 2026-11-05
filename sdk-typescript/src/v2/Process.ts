/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport, strictUriEncode } from './http.js'
import type {
  ListResult, ProcessCommand, ProcessDto, ProcessHandleJSON, ProcessListOptions,
  ProcessStartOptions, RunOptions, RunResult, TerminalSize,
} from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { BadRequestError, DaytonaError } from './errors/DaytonaError.js'
import { ProcessHandle } from './ProcessHandle.js'
import type { PtyDuplex } from './ProcessHandle.js'
import { commandSource, invalidPtyVerbError, knownProcessKind, processFromWire, WAIT_READ_SLACK_MS } from './ProcessWire.js'
import type { InternalProcessStartOptions } from './ProcessWire.js'
import { runProcess, startProcess } from './ProcessRun.js'
import { requireSessionId, Session } from './Session.js'
import type { CreateSessionOptions, SessionJSON } from './Session.js'

// Preserve internal import paths used by the interpreter and other namespaces.
export { ProcessHandle } from './ProcessHandle.js'
export type { PtyDuplex, WaitProcessOptions } from './ProcessHandle.js'
export { WAIT_READ_SLACK_MS, base64Encode } from './ProcessWire.js'
export { TIMED_OUT_LOG_PAGE_LIMIT, collectAllLogs, startProcess } from './ProcessRun.js'

export class ProcessService {
  constructor(
    public readonly transport: HttpTransport,
    public readonly sandboxId: string,
  ) {}

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
   * @param options.executionTimeout Server-side deadline in milliseconds;
   *   kills the process tree on expiry.
   */
  public async start(command: ProcessCommand, options: ProcessStartOptions = {}): Promise<ProcessHandle> {
    return startProcess(this.transport, this.sandboxId, commandSource(command), options)
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
   * @param options.timeout Caller wait budget in milliseconds; omitted or 0
   *   waits unbounded. Expiry does not kill the process.
   * @param options.executionTimeout Server-side deadline in milliseconds;
   *   kills the process tree on expiry.
   * @returns Output, stdout, stderr, logs and the exit result.
   */
  public async run(command: ProcessCommand, options: RunOptions = {}): Promise<RunResult> {
    return runProcess(this, command, options)
  }

  /** Create one persistent shell. Set its cwd/env here, not on its commands. */
  public async createSession(options: CreateSessionOptions = {}): Promise<Session> {
    const body: toolbox.CreateProcessRequest = { ...options, kind: 'session' }
    const data = await this.transport.post<toolbox.ProcessDto>('/processes', body)
    return this.connectSession(data.id)
  }

  /** Rebind a shell session by id or serialized identity, without a request. */
  public connectSession(sessionIdOrJSON: string | SessionJSON): Session {
    if (typeof sessionIdOrJSON === 'string') return new Session(this, sessionIdOrJSON)
    const sandboxId = requireSessionId(sessionIdOrJSON?.sandboxId, 'sandboxId')
    const sessionId = requireSessionId(sessionIdOrJSON?.sessionId, 'sessionId')
    if (sandboxId !== this.sandboxId) {
      throw new BadRequestError('session belongs to a different sandbox', 400, 'BAD_REQUEST', 'SDK')
    }
    return new Session(this, sessionId)
  }

  public connect(idOrHandle: string | ProcessHandleJSON): ProcessHandle {
    if (typeof idOrHandle !== 'string' && idOrHandle.sandboxId !== this.sandboxId) {
      throw new DaytonaError('process handle belongs to a different sandbox', 400, 'BAD_REQUEST')
    }
    return new ProcessHandle(
      this.transport,
      this.sandboxId,
      typeof idOrHandle === 'string' ? idOrHandle : idOrHandle.processId,
      // Unknown serialized kinds read as absent; the API validates operations.
      typeof idOrHandle === 'string' ? undefined : knownProcessKind(idOrHandle.kind),
    )
  }

  public async get(id: string): Promise<ProcessDto> {
    return processFromWire(await this.transport.get<toolbox.ProcessDto>(`/processes/${strictUriEncode(id)}`))
  }

  public async list(options: ProcessListOptions = {}): Promise<ListResult<ProcessDto>> {
    const query = encodeQuery({ state: options.state, kind: options.kind, sessionId: options.sessionId, name: options.name, pid: options.pid, tag: options.tag })
    const data = await this.transport.get<toolbox.ProcessDto[] | toolbox.ProcessListResponse | ListResult<toolbox.ProcessDto>>(`/processes${query}`)
    const page: ListResult<ProcessDto> = Array.isArray(data)
      ? { items: data.map(processFromWire) }
      : { items: data.items.map(processFromWire), nextCursor: 'nextCursor' in data ? data.nextCursor : undefined }
    // A sandbox whose daemon predates tags ignores the filter and returns
    // every process: keep only the tagged ones.
    return options.tag === undefined ? page : { ...page, items: page.items.filter((item) => item.tag === options.tag) }
  }

  /**
   * Kill every process with this tag and everything it started, keeping logs
   * and records. Returns their ids once none is running; the tag is reusable.
   * Survivors of deleted or expired records are killed too but not listed.
   * Throws UnsupportedOperationError on macOS and sandboxes without process containment.
   * @param options.timeout Exit-wait budget in milliseconds (1–60000; default 10000).
   * @throws ProcessKillIncompleteError if processes remain; retrying is safe.
   */
  public async killAll(options: { readonly tag: string; readonly timeout?: number }): Promise<string[]> {
    if (options.timeout !== undefined && (!Number.isFinite(options.timeout) || options.timeout <= 0)) {
      throw new BadRequestError('timeout must be a positive, finite number of milliseconds', 400, 'BAD_REQUEST', 'SDK')
    }
    const body: toolbox.KillProcessesRequest = { tag: options.tag }
    if (options.timeout !== undefined) body.timeoutMs = Math.ceil(options.timeout)
    const data = await this.transport.post<toolbox.KillProcessesResponse>('/processes:kill', body, {
      timeout: (options.timeout ?? 10_000) + WAIT_READ_SLACK_MS,
    })
    return data.processIds
  }
}

export class PtyService {
  constructor(private readonly process: ProcessService) {}

  public async start(command?: string, options: Omit<ProcessStartOptions, 'cmd'> & { terminal?: TerminalSize } = {}): Promise<ProcessHandle> {
    const ptyOptions: InternalProcessStartOptions = { ...options, kind: 'pty', stdin: options.stdin ?? 'open', keepLogs: options.keepLogs ?? 'until_cleanup' }
    return command === undefined
      ? startProcess(this.process.transport, this.process.sandboxId, ptyOptions)
      : startProcess(this.process.transport, this.process.sandboxId, command, ptyOptions)
  }

  public async attach(command?: string, options: Omit<ProcessStartOptions, 'cmd'> & { terminal?: TerminalSize } = {}): Promise<{ handle: ProcessHandle; connection: PtyDuplex }> {
    const handle = await this.start(command, options)
    try {
      return { handle, connection: await handle.attach() }
    } catch (error) {
      await handle.delete().catch(() => undefined)
      throw error
    }
  }

  public connect(idOrHandle: string | ProcessHandleJSON): ProcessHandle {
    return this.process.connect(idOrHandle)
  }

  public async list(): Promise<ListResult<ProcessDto>> {
    return this.process.list({ kind: 'pty' })
  }

  public async delete(idOrHandle: string | ProcessHandleJSON): Promise<void> {
    const handle = this.connect(idOrHandle)
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
