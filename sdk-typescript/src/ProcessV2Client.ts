/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Configuration, InfoApi, Process as ProcessRecord, ProcessApi } from '@daytona/toolbox-api-client'
import { ProcessHandle } from './ProcessHandle'
import { ProcessV2Transport } from './ProcessV2Transport'
import { DaytonaInvalidArgumentError } from './errors/DaytonaError'
import {
  normalizeIdentifier,
  normalizeSerializedHandle,
  validateProcessStartOptions,
  validateWaitTimeout,
} from './process-v2-utils'
import type {
  ProcessListFilter,
  ProcessRunOptions,
  ProcessRunResult,
  ProcessStartOptions,
  SerializedProcessHandle,
} from './types/ProcessV2'

export class ProcessV2Client {
  private readonly transport: ProcessV2Transport

  constructor(
    clientConfig: Configuration,
    apiClient: ProcessApi,
    getPreviewToken: () => Promise<string>,
    private readonly sandboxId: string,
    infoApi?: Pick<InfoApi, 'getVersion'>,
  ) {
    this.transport = new ProcessV2Transport(clientConfig, apiClient, getPreviewToken, infoApi)
  }

  public async start(options: ProcessStartOptions = {}): Promise<ProcessHandle> {
    validateProcessStartOptions(options)
    const process = await this.transport.createProcess(options)
    return this.createHandle(process.id)
  }

  public async run(options: ProcessRunOptions = {}): Promise<ProcessRunResult> {
    validateWaitTimeout(options.waitTimeoutMs)
    const { waitTimeoutMs, ...startOptions } = options
    const handle = await this.start({
      ...startOptions,
      keepLogs: startOptions.keepLogs ?? 'on_exit_ttl',
    })
    const result = await handle.wait({ timeoutMs: waitTimeoutMs })
    return {
      ...result,
      id: handle.id,
      handle,
      toJSON: () => handle.toJSON(),
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

  public async fromJSON(serialized: SerializedProcessHandle): Promise<ProcessHandle> {
    const normalized = normalizeSerializedHandle(serialized)
    if (normalized.sandboxId !== this.sandboxId) {
      throw new DaytonaInvalidArgumentError(
        `Serialized handle sandboxId ${normalized.sandboxId} does not match sandbox ${this.sandboxId}`,
      )
    }
    return await this.get(normalized.processId)
  }

  private createHandle(processId: string): ProcessHandle {
    return new ProcessHandle(this.sandboxId, processId, this.transport)
  }
}
