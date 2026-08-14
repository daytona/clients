/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  Configuration,
  Process as ProcessRecord,
  ProcessApi,
  ProcessLogPage,
  ProcessResult,
} from '@daytona/toolbox-api-client'
import { DaytonaError } from './errors/DaytonaError'
import { parseProcessLogEvent } from './process-payloads'
import {
  buildStreamingHeaders,
  buildCreateProcessRequest,
  createFetchDaytonaError,
  createFetchResponseError,
  encodeStdinPayload,
  extractSseSegments,
  normalizeHeaders,
  normalizeIdentifier,
  normalizeOptionalString,
  normalizeSignal,
  requiresPreviewToken,
  validateKillOptions,
  validateTerminalDimension,
  validateWaitTimeout,
} from './process-utils'
import type {
  ProcessKillOptions,
  ProcessListFilter,
  ProcessLogEvent,
  ProcessLogOptions,
  ProcessStartOptions,
  ProcessStreamLogOptions,
  ProcessWaitOptions,
  PtySocket,
} from './types/Process'
import { createSandboxWebSocket } from './utils/WebSocket'

export class ProcessTransport {
  constructor(
    private readonly clientConfig: Configuration,
    private readonly apiClient: ProcessApi,
    private readonly getPreviewToken: () => Promise<string>,
  ) {}

  public async createProcess(options: ProcessStartOptions): Promise<ProcessRecord> {
    return (await this.apiClient.createProcess(buildCreateProcessRequest(options))).data
  }

  public async listProcesses(filter?: ProcessListFilter): Promise<ProcessRecord[]> {
    return (
      await this.apiClient.listProcesses(filter?.state, filter?.kind, filter?.sessionId, filter?.name, filter?.pid)
    ).data
  }

  public async getProcess(id: string): Promise<ProcessRecord> {
    const processId = normalizeIdentifier(id, 'processId')
    return (await this.apiClient.getProcess(processId)).data
  }

  public async cleanupProcess(id: string): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.apiClient.cleanupProcess(processId)
  }

  public async getLogs(id: string, options?: ProcessLogOptions): Promise<ProcessLogPage> {
    const processId = normalizeIdentifier(id, 'processId')
    return (await this.apiClient.readProcessLogs(processId, options?.cursor, options?.limit, options?.encoding)).data
  }

  public async *streamProcessLogs(id: string, options?: ProcessStreamLogOptions): AsyncGenerator<ProcessLogEvent> {
    const processId = normalizeIdentifier(id, 'processId')
    const request = await this.buildStreamingRequest(processId, options?.cursor, options?.encoding)
    const response = await this.fetchProcess(request.url, request.headers)
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
    await this.apiClient.sendProcessStdin(processId, { data: encodeStdinPayload(data) })
  }

  public async sendStdinEof(id: string): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.apiClient.sendProcessStdin(processId, { eof: true })
  }

  public async killProcess(id: string, options?: ProcessKillOptions): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    validateKillOptions(options)
    await this.apiClient.signalProcess(processId, {
      signal: normalizeSignal(options?.signal),
      escalateAfterMs: options?.escalateAfterMs,
      escalateTo: normalizeOptionalString(options?.escalateTo),
    })
  }

  public async resizeProcess(id: string, cols: number, rows: number): Promise<void> {
    const processId = normalizeIdentifier(id, 'processId')
    validateTerminalDimension(cols, 'cols')
    validateTerminalDimension(rows, 'rows')
    await this.apiClient.resizeProcess(processId, { cols, rows })
  }

  public async waitForProcess(id: string, options?: ProcessWaitOptions): Promise<ProcessResult> {
    const processId = normalizeIdentifier(id, 'processId')
    validateWaitTimeout(options?.timeoutMs)
    return (await this.apiClient.waitForProcess(processId, options?.timeoutMs)).data
  }

  public async attachProcessTerminal(id: string): Promise<PtySocket> {
    const processId = normalizeIdentifier(id, 'processId')
    await this.getProcess(processId)
    const basePath = this.clientConfig.basePath.replace(/^http/, 'ws').replace(/\/+$/, '')
    const url = `${basePath}/processes/${encodeURIComponent(processId)}/attach`
    return await createSandboxWebSocket(
      url,
      normalizeHeaders(this.clientConfig.baseOptions?.headers),
      this.getPreviewToken,
    )
  }

  private async fetchProcess(url: string, headers: Record<string, string>): Promise<Response> {
    let response: Response
    try {
      response = await fetch(url, { method: 'GET', headers })
    } catch (error) {
      throw createFetchDaytonaError(error)
    }

    if (!response.ok) {
      throw await createFetchResponseError(response)
    }

    return response
  }

  private async buildStreamingRequest(
    processId: string,
    cursor?: string,
    encoding?: string,
  ): Promise<{ url: string; headers: Record<string, string> }> {
    const params = new URLSearchParams({ follow: 'true' })
    if (cursor !== undefined && cursor !== '') {
      params.set('cursor', cursor)
    }
    if (encoding !== undefined) {
      params.set('encoding', encoding)
    }

    let url = `${this.clientConfig.basePath.replace(/\/+$/, '')}/processes/${encodeURIComponent(processId)}/logs?${params.toString()}`
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
