/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  Configuration,
  InfoApi,
  Process as ProcessRecord,
  ProcessApi,
  ProcessLogPage,
  ProcessResult,
} from '@daytona/toolbox-api-client'
import { DaytonaDaemonUpgradeRequiredError, DaytonaError, SOURCE_DAEMON } from './errors/DaytonaError'
import { parseProcessLogEvent } from './process-v2-payloads'
import {
  buildStreamingHeaders,
  buildCreateProcessRequest,
  createFetchDaytonaError,
  createFetchResponseError,
  DAEMON_UPGRADE_REQUIRED_CODE,
  extractSseSegments,
  normalizeHeaders,
  normalizeIdentifier,
  normalizeOptionalString,
  normalizeSignal,
  requiresPreviewToken,
  shouldTranslateProcessV2Unsupported,
  validateKillOptions,
  validateTerminalDimension,
  validateWaitTimeout,
} from './process-v2-utils'
import type {
  ProcessKillOptions,
  ProcessListFilter,
  ProcessLogEvent,
  ProcessLogOptions,
  ProcessStartOptions,
  ProcessStreamLogOptions,
  ProcessWaitOptions,
  PtySocket,
} from './types/ProcessV2'
import { createSandboxWebSocket } from './utils/WebSocket'

type ProcessV2Status = 'unknown' | 'supported' | 'unsupported'

export class ProcessV2Transport {
  private status: ProcessV2Status = 'unknown'
  private upgradeError?: DaytonaDaemonUpgradeRequiredError

  constructor(
    private readonly clientConfig: Configuration,
    private readonly apiClient: ProcessApi,
    private readonly getPreviewToken: () => Promise<string>,
    private readonly infoApi?: Pick<InfoApi, 'getVersion'>,
  ) {}

  public async createProcess(options: ProcessStartOptions): Promise<ProcessRecord> {
    return (await this.executeProcessV2(() => this.apiClient.createProcessV2(buildCreateProcessRequest(options)))).data
  }

  public async listProcesses(filter?: ProcessListFilter): Promise<ProcessRecord[]> {
    return (
      await this.executeProcessV2(() =>
        this.apiClient.listProcessesV2(filter?.state, filter?.kind, filter?.sessionId, filter?.name, filter?.pid),
      )
    ).data
  }

  public async getProcess(id: string): Promise<ProcessRecord> {
    const processId = normalizeIdentifier(id, 'processId')
    return (await this.executeProcessV2(() => this.apiClient.getProcessV2(processId))).data
  }

  public async getLogs(id: string, options?: ProcessLogOptions): Promise<ProcessLogPage> {
    const processId = normalizeIdentifier(id, 'processId')
    return (
      await this.executeProcessV2(() =>
        this.apiClient.getProcessLogsV2(processId, options?.cursor, options?.limit, options?.encoding),
      )
    ).data
  }

  public async *streamProcessLogs(id: string, options?: ProcessStreamLogOptions): AsyncGenerator<ProcessLogEvent> {
    const processId = normalizeIdentifier(id, 'processId')
    const request = await this.buildStreamingRequest(processId, options?.cursor)
    const response = await this.fetchProcessV2(request.url, request.headers)
    if (!response.body) {
      throw new DaytonaError('No streaming support')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    try {
      while (true) {
        const chunk = await reader.read()
        if (chunk.done) {
          break
        }

        buffer += decoder.decode(chunk.value, { stream: true })
        const segments = extractSseSegments(buffer)
        buffer = segments.remainder
        for (const segment of segments.events) {
          const parsed = parseProcessLogEvent(segment.event, segment.data)
          yield parsed
          if (parsed.type === 'eof') {
            return
          }
        }
      }

      const remainder = decoder.decode()
      if (remainder) {
        buffer += remainder
        const segments = extractSseSegments(buffer)
        for (const segment of segments.events) {
          const parsed = parseProcessLogEvent(segment.event, segment.data)
          yield parsed
          if (parsed.type === 'eof') {
            return
          }
        }
      }
    } finally {
      try {
        await reader.cancel()
      } catch {
        // Best-effort teardown: the stream is already ending, and surfacing a
        // cancellation failure here would mask the caller's real error.
      }
    }
  }

  public async sendStdin(id: string, data: string | Uint8Array): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.executeProcessV2(() =>
      this.apiClient.sendProcessStdinV2(processId, {
        data: typeof data === 'string' ? data : new TextDecoder('utf-8').decode(data),
      }),
    )
  }

  public async sendStdinEof(id: string): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.executeProcessV2(() => this.apiClient.sendProcessStdinV2(processId, { eof: true }))
  }

  public async killProcess(id: string, options?: ProcessKillOptions): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    validateKillOptions(options)
    await this.executeProcessV2(() =>
      this.apiClient.signalProcessV2(processId, {
        signal: normalizeSignal(options?.signal),
        escalateAfterMs: options?.escalateAfterMs,
        escalateTo: normalizeOptionalString(options?.escalateTo),
      }),
    )
  }

  public async resizeProcess(id: string, cols: number, rows: number): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    validateTerminalDimension(cols, 'cols')
    validateTerminalDimension(rows, 'rows')
    await this.executeProcessV2(() => this.apiClient.resizeProcessV2(processId, { cols, rows }))
  }

  public async waitForProcess(id: string, options?: ProcessWaitOptions): Promise<ProcessResult> {
    const processId = normalizeIdentifier(id, 'processId')
    validateWaitTimeout(options?.timeoutMs)
    return (await this.executeProcessV2(() => this.apiClient.waitForProcessV2(processId, options?.timeoutMs))).data
  }

  public async attachProcessTerminal(id: string): Promise<PtySocket> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.getProcess(processId)
    const basePath = this.clientConfig.basePath.replace(/^http/, 'ws').replace(/\/+$/, '')
    const url = `${basePath}/process/v2/processes/${encodeURIComponent(processId)}/attach`
    return await createSandboxWebSocket(
      url,
      normalizeHeaders(this.clientConfig.baseOptions?.headers),
      this.getPreviewToken,
    )
  }

  private async executeProcessV2<T>(operation: () => Promise<T>): Promise<T> {
    if (this.status === 'unsupported' && this.upgradeError) {
      throw this.upgradeError
    }

    try {
      const result = await operation()
      this.status = 'supported'
      return result
    } catch (error) {
      if (shouldTranslateProcessV2Unsupported(error)) {
        throw await this.buildUpgradeRequiredError(error)
      }
      if (error instanceof DaytonaError && error.statusCode !== undefined) {
        this.status = 'supported'
      }
      throw error
    }
  }

  private async fetchProcessV2(url: string, headers: Record<string, string>): Promise<Response> {
    if (this.status === 'unsupported' && this.upgradeError) {
      throw this.upgradeError
    }

    let response: Response
    try {
      response = await fetch(url, { method: 'GET', headers })
    } catch (error) {
      throw createFetchDaytonaError(error)
    }

    if (!response.ok) {
      const daytonaError = await createFetchResponseError(response)
      if (shouldTranslateProcessV2Unsupported(daytonaError)) {
        throw await this.buildUpgradeRequiredError(daytonaError)
      }
      this.status = 'supported'
      throw daytonaError
    }

    this.status = 'supported'
    return response
  }

  private async buildUpgradeRequiredError(trigger: DaytonaError): Promise<DaytonaDaemonUpgradeRequiredError> {
    if (this.upgradeError) {
      return this.upgradeError
    }

    const daemonVersion = await this.readDaemonVersion()
    this.status = 'unsupported'
    this.upgradeError = new DaytonaDaemonUpgradeRequiredError(
      daemonVersion
        ? `Process v2 requires a newer sandbox daemon. Current daemon version: ${daemonVersion}`
        : 'Process v2 requires a newer sandbox daemon.',
      trigger.statusCode,
      trigger.headers,
      DAEMON_UPGRADE_REQUIRED_CODE,
      SOURCE_DAEMON,
      daemonVersion,
    )
    return this.upgradeError
  }

  private async readDaemonVersion(): Promise<string | undefined> {
    if (!this.infoApi) {
      return undefined
    }

    try {
      const response = await this.infoApi.getVersion()
      return typeof response.data.version === 'string' ? response.data.version : undefined
    } catch {
      return undefined
    }
  }

  private async buildStreamingRequest(
    processId: string,
    cursor?: string,
  ): Promise<{ url: string; headers: Record<string, string> }> {
    const params = new URLSearchParams({ follow: 'true' })
    if (cursor !== undefined && cursor !== '') {
      params.set('cursor', cursor)
    }

    let url = `${this.clientConfig.basePath.replace(/\/+$/, '')}/process/v2/processes/${encodeURIComponent(processId)}/logs?${params.toString()}`
    const headers = buildStreamingHeaders(
      normalizeHeaders(this.clientConfig.baseOptions?.headers),
      'application/json,text/event-stream',
    )

    if (requiresPreviewToken()) {
      url += `&DAYTONA_SANDBOX_AUTH_KEY=${encodeURIComponent(await this.getPreviewToken())}`
    }

    return { url, headers }
  }
}
