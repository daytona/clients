/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ProcessDto, KeepLogs, RunResult } from './types.js'
import { ProcessHandle } from './ProcessHandle.js'
import { runProcess, startProcess } from './ProcessRun.js'
import type { ProcessBinding } from './ProcessRun.js'
import { BadRequestError, ProcessNotFoundError } from './errors/DaytonaError.js'

/** A missing id must never degrade a session command into a plain, stateless exec. */
export function requireSessionId(value: unknown, field: 'sandboxId' | 'sessionId'): string {
  if (typeof value !== 'string' || value === '') {
    throw new BadRequestError(`session identity needs a non-empty string ${field}`, 400, 'BAD_REQUEST', 'SDK')
  }
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
  readonly stdin?: 'closed' | 'open'
  /**
   * Server-side command deadline in milliseconds. The clock starts when the
   * command starts, after any queued commands ahead of it.
   * The command is stopped and the session survives; a command that has not
   * stopped 5 s after its timeout ends the session instead of blocking it.
   * In dash, BusyBox `sh` and mksh an interrupt leaves only the innermost shell
   * function or sourced file, as it does in bash older than 4.4 (macOS's
   * `/bin/bash`) when a DEBUG trap is set.
   */
  readonly executionTimeout?: number
  readonly name?: string
  readonly keepLogs?: KeepLogs
}

export interface SessionRunOptions extends SessionStartOptions {
  /** Caller wait budget in milliseconds; omitted or 0 waits unbounded. */
  readonly timeout?: number
}

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
 * the shell wrapper used to run each command.
 */
export class Session {
  public readonly sandboxId: string

  public readonly id: string

  constructor(private readonly binding: ProcessBinding, id: string) {
    this.sandboxId = binding.sandboxId
    this.id = requireSessionId(id, 'sessionId')
  }

  /** Run in this shell, wait, and collect output using the process run path. */
  public async run(command: string, options: SessionRunOptions = {}): Promise<RunResult> {
    return runProcess(this.binding, command, { ...options, sessionId: this.id })
  }

  /** Queue a command and return its handle without waiting for completion. */
  public async start(command: string, options: SessionStartOptions = {}): Promise<ProcessHandle> {
    return startProcess(this.binding.transport, this.sandboxId, command, { ...options, sessionId: this.id })
  }

  /** Read the shell process, including its current working directory. */
  public async get(): Promise<ProcessDto> {
    return new ProcessHandle(this.binding.transport, this.sandboxId, this.id, 'session').get()
  }

  /** End the shell and delete it and all its command records. */
  public async delete(): Promise<void> {
    await new ProcessHandle(this.binding.transport, this.sandboxId, this.id, 'session').delete()
  }

  /** Deletes the session on scope exit; one already deleted in the scope is fine. */
  public async [Symbol.asyncDispose](): Promise<void> {
    try {
      await this.delete()
    } catch (error) {
      if (error instanceof ProcessNotFoundError) return
      throw error
    }
  }

  public toJSON(): SessionJSON {
    return { sandboxId: this.sandboxId, sessionId: this.id }
  }
}
