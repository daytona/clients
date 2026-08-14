/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Process as ProcessRecord, ProcessLogPage, ProcessResult } from '@daytona/toolbox-api-client'
import type {
  ProcessOutput,
  ProcessKillOptions,
  ProcessLogEvent,
  ProcessLogOptions,
  ProcessStreamLogOptions,
  ProcessWaitOptions,
  PtySocket,
} from './types/Process'
import { WithInstrumentation } from './utils/otel.decorator'
import { collectOutputFromLogs } from './process-run-output'

interface ProcessHandleOperations {
  getProcess(id: string): Promise<ProcessRecord>
  getLogs(id: string, options?: ProcessLogOptions): Promise<ProcessLogPage>
  streamProcessLogs(id: string, options?: ProcessStreamLogOptions): AsyncIterable<ProcessLogEvent>
  sendStdin(id: string, data: string | Uint8Array): Promise<void>
  sendStdinEof(id: string): Promise<void>
  killProcess(id: string, options?: ProcessKillOptions): Promise<void>
  resizeProcess(id: string, cols: number, rows: number): Promise<void>
  waitForProcess(id: string, options?: ProcessWaitOptions): Promise<ProcessResult>
  attachProcessTerminal(id: string): Promise<PtySocket>
  cleanupProcess(id: string): Promise<void>
}

export class ProcessHandle {
  constructor(
    public readonly id: string,
    private readonly operations: ProcessHandleOperations,
  ) {}

  /**
   * Fetches the current process record (state, pid, exit metadata, retention info).
   */
  @WithInstrumentation()
  public async get(): Promise<ProcessRecord> {
    return await this.operations.getProcess(this.id)
  }

  /**
   * Reads a page of retained log frames. Omit `cursor` (or pass `'start'`) to replay from
   * the beginning; pass `nextCursor` from the previous page to continue. If old frames were
   * evicted under retention caps the page reports `truncatedHead` and a stale cursor
   * surfaces a CURSOR_EXPIRED error carrying the first available cursor.
   */
  @WithInstrumentation()
  public async logs(options?: ProcessLogOptions): Promise<ProcessLogPage> {
    return await this.operations.getLogs(this.id, options)
  }

  /**
   * Streams log events live (frames, state changes, eof) as an async iterable, optionally
   * resuming from a cursor - missed output is replayed first, then live frames follow.
   * Iteration ends when the process exits and the stream reports eof.
   */
  public streamLogs(options?: ProcessStreamLogOptions): AsyncIterable<ProcessLogEvent> {
    return this.operations.streamProcessLogs(this.id, options)
  }

  /**
   * Writes data to the process's stdin (requires `stdin: 'pipe'` at start, or a PTY).
   */
  @WithInstrumentation()
  public async stdin(data: string | Uint8Array): Promise<void> {
    await this.operations.sendStdin(this.id, data)
  }

  /**
   * Closes the process's stdin, signalling end-of-input to programs that read until EOF.
   */
  @WithInstrumentation()
  public async stdinEof(): Promise<void> {
    await this.operations.sendStdinEof(this.id)
  }

  /**
   * Sends a signal to the process (default SIGKILL; pass `signal` to override).
   */
  @WithInstrumentation()
  public async kill(options?: ProcessKillOptions): Promise<void> {
    await this.operations.killProcess(this.id, options)
  }

  /**
   * Resizes the pseudo-terminal of a `kind: 'pty'` process.
   */
  @WithInstrumentation()
  public async resize(cols: number, rows: number): Promise<void> {
    await this.operations.resizeProcess(this.id, cols, rows)
  }

  /**
   * Blocks until the process exits (or `timeoutMs` elapses, reason `timed_out`) and
   * returns the exit result. Does not collect output - pair with {@link output} or use
   * `process.run` when you want both.
   */
  @WithInstrumentation()
  public async wait(options?: ProcessWaitOptions): Promise<ProcessResult> {
    return await this.operations.waitForProcess(this.id, options)
  }

  /**
   * Opens the interactive bidirectional terminal socket of a `kind: 'pty'` process
   * (raw terminal bytes in both directions).
   */
  @WithInstrumentation()
  public async attachTerminal(): Promise<PtySocket> {
    return await this.operations.attachProcessTerminal(this.id)
  }

  /**
   * Deletes the finished process's record and retained logs, freeing their
   * disk footprint in the sandbox. Fails on a running process (kill it first).
   */
  @WithInstrumentation()
  public async cleanup(): Promise<void> {
    await this.operations.cleanupProcess(this.id)
  }

  /**
   * Collects the process's retained stdout/stderr from the log ledger, plus
   * exit metadata when the process has finished. Works on running processes
   * (returns output so far) and after reconnecting to a finished one.
   *
   * @example
   * const handle = await sandbox.process.get(processId);
   * const { stdout, exitCode } = await handle.output();
   */
  @WithInstrumentation()
  public async output(): Promise<ProcessOutput> {
    const [record, collected] = await Promise.all([this.get(), collectOutputFromLogs(this)])
    return {
      stdout: collected.stdout,
      stderr: collected.stderr,
      exitCode: record.exitCode,
      signal: record.signal,
      reason: record.reason,
    }
  }
}
