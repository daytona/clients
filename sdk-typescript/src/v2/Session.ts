/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { KeepLogs, ProcessInfo, ProcessResult, RunKeepLogs, StdinMode } from './types.js'
import { ProcessHandle } from './ProcessHandle.js'
import { runProcess, startProcess } from './ProcessRun.js'
import type { ProcessBinding } from './ProcessRun.js'
import { BadRequestError, ProcessNotFoundError, SessionNotFoundError } from './errors/DaytonaError.js'
import { assertInternal, INTERNAL } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, optionKeys } from './options.js'
import { refuseDotSegment } from './pathSegment.js'

/** A missing id must never degrade a session command into a plain, stateless exec. */
export function requireSessionId(value: unknown, field: 'sandboxId' | 'sessionId'): string {
  if (typeof value !== 'string' || value === '') {
    throw new BadRequestError(`session identity needs a non-empty string ${field}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  if (field === 'sessionId') refuseDotSegment(value, 'session id')
  return value
}

/** Settings for the persistent shell, inherited by every command. */
export interface CreateSessionOptions {
  /** Immutable tag inherited by every command run in this shell. */
  readonly tag?: string
  readonly cwd?: string
  readonly env?: Readonly<Record<string, string>>
  /**
   * A POSIX shell (bash, sh, dash, zsh, ksh) on Linux and macOS; another
   * shell is refused when the session is created.
   */
  readonly shell?: string
  readonly login?: boolean
  readonly name?: string
}

export interface SessionStartOptions {
  readonly stdin?: StdinMode
  /**
   * Server-side command deadline in seconds (omitted or `0`: none). The clock
   * starts when the command starts, after any queued commands ahead of it.
   * The command is stopped and the session survives; a command that has not
   * stopped 5 s after its timeout ends the session instead of blocking it.
   * In dash, BusyBox `sh` and mksh an interrupt leaves only the innermost shell
   * function or sourced file, as it does in bash older than 4.4 (macOS's
   * `/bin/bash`) when a DEBUG trap is set.
   */
  readonly executionTimeoutSeconds?: number
  readonly name?: string
  /** How long the sandbox keeps the command's record and log (default `'on_exit_ttl'`). */
  readonly keepLogs?: KeepLogs
}

export interface SessionRunOptions extends Omit<SessionStartOptions, 'keepLogs' | 'stdin'> {
  /** Only `'closed'` (default): `run()` returns no handle to write to; use `start()` for open stdin. */
  readonly stdin?: 'closed'
  /** How long the sandbox keeps the command's log (default `'on_exit_ttl'`). */
  readonly keepLogs?: RunKeepLogs
  /** Caller wait budget in seconds; omitted or 0 waits unbounded. Expiry does not stop the command. */
  readonly timeoutSeconds?: number
}

const SESSION_START_OPTION_KEYS = optionKeys<SessionStartOptions>()({ stdin: true, executionTimeoutSeconds: true, name: true, keepLogs: true })
const SESSION_RUN_OPTION_KEYS = optionKeys<SessionRunOptions>()({
  stdin: true, executionTimeoutSeconds: true, name: true, keepLogs: true, timeoutSeconds: true,
})

/** A shell session's serialized identity (`session.toJSON()`), the same shape the Python SDK writes. */
export interface SessionJSON {
  readonly sandboxId: string
  readonly sessionId: string
}

/**
 * One persistent shell. Commands run one at a time, in order; `cd`, exports,
 * and functions carry over. Set cwd/env with `process.createSession()`.
 * Interrupt a command with `handle.kill({ signal: 'INT' })`: the session
 * survives. Sessions survive pause/resume, but not a sandbox stop/start.
 * After `set -x` or `set -v` in a session, later commands' stderr also includes
 * the shell wrapper used to run each command. Returned by
 * `process.createSession()` / `process.connectSession()`, never constructed
 * directly.
 */
export class Session {
  public readonly sandboxId: string

  public readonly id: string

  private readonly binding: ProcessBinding

  /** @internal */
  constructor(token: Internal, binding: ProcessBinding, id: string)
  /** Built by the SDK: get it from `sandbox.process.createSession()` or `connectSession()`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, binding: ProcessBinding, id: string) {
    assertInternal(token, 'Session')
    this.binding = binding
    this.sandboxId = binding.sandboxId
    this.id = requireSessionId(id, 'sessionId')
  }

  /** Run in this shell, wait, and collect output using the process run path. */
  public async run(command: string, options: SessionRunOptions = {}): Promise<ProcessResult> {
    assertKnownOptions('SessionRunOptions', options, SESSION_RUN_OPTION_KEYS)
    const { timeoutSeconds, keepLogs, ...start } = options
    return runProcess(this.binding, { ...start, cmd: command, kind: 'exec', sessionId: this.id }, { timeoutSeconds, keepLogs })
  }

  /** Queue a command and return its handle without waiting for completion. */
  public async start(command: string, options: SessionStartOptions = {}): Promise<ProcessHandle> {
    assertKnownOptions('SessionStartOptions', options, SESSION_START_OPTION_KEYS)
    return startProcess(this.binding.transport, this.sandboxId, { ...options, cmd: command, kind: 'exec', sessionId: this.id, stdin: options.stdin ?? 'closed', keepLogs: options.keepLogs ?? 'on_exit_ttl' })
  }

  /** Read the shell process, including its current working directory. */
  public async get(): Promise<ProcessInfo> {
    return this.handle().get()
  }

  /** End the shell and delete it and all its command records. */
  public async delete(): Promise<void> {
    await this.handle().delete()
  }

  private handle(): ProcessHandle {
    return new ProcessHandle(INTERNAL, this.binding.transport, this.sandboxId, this.id, 'session')
  }

  /** Deletes the session on scope exit; one already deleted in the scope is fine. */
  public async [Symbol.asyncDispose](): Promise<void> {
    try {
      await this.delete()
    } catch (error) {
      if (error instanceof ProcessNotFoundError || error instanceof SessionNotFoundError) return
      throw error
    }
  }

  /** The session's JSON-safe identity, for `sandbox.process.connectSession()`. */
  public toJSON(): SessionJSON {
    return { sandboxId: this.sandboxId, sessionId: this.id }
  }
}
