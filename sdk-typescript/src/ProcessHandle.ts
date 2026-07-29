/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Process as ProcessRecord, ProcessLogPage, ProcessResult } from '@daytona/toolbox-api-client'
import type {
  ProcessKillOptions,
  ProcessLogEvent,
  ProcessLogOptions,
  ProcessStreamLogOptions,
  ProcessWaitOptions,
  PtySocket,
  SerializedProcessHandle,
} from './types/ProcessV2'
import { WithInstrumentation } from './utils/otel.decorator'

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
}

export class ProcessHandle {
  constructor(
    private readonly sandboxId: string,
    public readonly id: string,
    private readonly operations: ProcessHandleOperations,
  ) {}

  toJSON(): SerializedProcessHandle {
    return {
      sandboxId: this.sandboxId,
      processId: this.id,
    }
  }

  @WithInstrumentation()
  public async get(): Promise<ProcessRecord> {
    return await this.operations.getProcess(this.id)
  }

  @WithInstrumentation()
  public async logs(options?: ProcessLogOptions): Promise<ProcessLogPage> {
    return await this.operations.getLogs(this.id, options)
  }

  public streamLogs(options?: ProcessStreamLogOptions): AsyncIterable<ProcessLogEvent> {
    return this.operations.streamProcessLogs(this.id, options)
  }

  @WithInstrumentation()
  public async stdin(data: string | Uint8Array): Promise<void> {
    await this.operations.sendStdin(this.id, data)
  }

  @WithInstrumentation()
  public async stdinEof(): Promise<void> {
    await this.operations.sendStdinEof(this.id)
  }

  @WithInstrumentation()
  public async kill(options?: ProcessKillOptions): Promise<void> {
    await this.operations.killProcess(this.id, options)
  }

  @WithInstrumentation()
  public async resize(cols: number, rows: number): Promise<void> {
    await this.operations.resizeProcess(this.id, cols, rows)
  }

  @WithInstrumentation()
  public async wait(options?: ProcessWaitOptions): Promise<ProcessResult> {
    return await this.operations.waitForProcess(this.id, options)
  }

  @WithInstrumentation()
  public async attachTerminal(): Promise<PtySocket> {
    return await this.operations.attachProcessTerminal(this.id)
  }
}
