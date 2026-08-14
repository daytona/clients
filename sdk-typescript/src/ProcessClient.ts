/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Configuration, Process as ProcessRecord, ProcessApi } from '@daytona/toolbox-api-client'
import { ProcessHandle } from './ProcessHandle'
import { ProcessTransport } from './ProcessTransport'
import { collectOutputFromLogs, streamOutputWithCallbacks } from './process-run-output'
import { DaytonaInvalidArgumentError } from './errors/DaytonaError'
import { normalizeIdentifier, validateProcessStartOptions, validateWaitTimeout } from './process-utils'
import type { ProcessListFilter, ProcessRunOptions, ProcessRunResult, ProcessStartOptions } from './types/Process'

export class ProcessClient {
  private readonly transport: ProcessTransport

  constructor(clientConfig: Configuration, apiClient: ProcessApi, getPreviewToken: () => Promise<string>) {
    this.transport = new ProcessTransport(clientConfig, apiClient, getPreviewToken)
  }

  public async start(options: ProcessStartOptions = {}): Promise<ProcessHandle> {
    validateProcessStartOptions(options)
    const process = await this.transport.createProcess(options)
    return this.createHandle(process.id)
  }

  public async run(options: ProcessRunOptions = {}): Promise<ProcessRunResult> {
    validateWaitTimeout(options.waitTimeoutMs)
    const { waitTimeoutMs, onStdout, onStderr, ...startOptions } = options
    if (startOptions.keepLogs === 'none' && !onStdout && !onStderr) {
      throw new DaytonaInvalidArgumentError(
        "keepLogs: 'none' discards output as it is produced, so run() cannot return stdout/stderr. " +
          'Consume the output live with onStdout/onStderr, or keep it with a retaining mode ' +
          "('on_exit_ttl' or 'until_cleanup').",
      )
    }
    const handle = await this.start({
      ...startOptions,
      keepLogs: startOptions.keepLogs ?? 'on_exit_ttl',
    })

    // With callbacks the output is consumed live off the SSE stream; otherwise
    // the retained ledger is paged after completion. Both yield the same
    // aggregated stdout/stderr on the result.
    if (onStdout || onStderr) {
      const output = await streamOutputWithCallbacks(handle, onStdout, onStderr, waitTimeoutMs)
      const result = await handle.wait({ timeoutMs: output.timedOut ? 1 : undefined })
      return {
        ...result,
        id: handle.id,
        handle,
        stdout: output.stdout,
        stderr: output.stderr,
        timedOut: output.timedOut || result.reason === 'timed_out',
      }
    }

    const result = await handle.wait({ timeoutMs: waitTimeoutMs })
    const output = await collectOutputFromLogs(handle)
    return {
      ...result,
      id: handle.id,
      handle,
      stdout: output.stdout,
      stderr: output.stderr,
      timedOut: output.timedOut || result.reason === 'timed_out',
    }
  }

  public async get(id: string): Promise<ProcessHandle> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.transport.getProcess(processId)
    return this.createHandle(processId)
  }

  public async list(filter?: ProcessListFilter): Promise<ProcessRecord[]> {
    return await this.transport.listProcesses(filter)
  }

  private createHandle(processId: string): ProcessHandle {
    return new ProcessHandle(processId, this.transport)
  }
}
