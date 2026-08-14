/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { AxiosHeaders } from 'axios'
import type { CreateProcessRequest } from '@daytona/toolbox-api-client'
import {
  createDaytonaError,
  DaytonaConnectionError,
  DaytonaConnectionTimeoutError,
  DaytonaError,
  DaytonaInvalidArgumentError,
  type ResponseHeaders,
} from './errors/DaytonaError'
import type { ProcessKillOptions, ProcessStartOptions } from './types/Process'
import { RUNTIME, Runtime } from './utils/Runtime'

export function buildCreateProcessRequest(options: ProcessStartOptions): CreateProcessRequest {
  const request: CreateProcessRequest = {}
  if (options.argv !== undefined) request.argv = [...options.argv]
  if (options.cwd !== undefined) request.cwd = options.cwd
  if (options.env !== undefined) request.env = { ...options.env }
  if (options.keepLogs !== undefined) request.keepLogs = options.keepLogs
  if (options.kind !== undefined) request.kind = options.kind
  if (options.login !== undefined) request.login = options.login
  if (options.name !== undefined) request.name = options.name
  if (options.sessionId !== undefined) request.sessionId = options.sessionId
  if (options.shell !== undefined) request.shell = options.shell
  if (options.shellCommand !== undefined) request.shellCommand = options.shellCommand
  if (options.stdin !== undefined) request.stdin = options.stdin
  if (options.terminal !== undefined) request.terminal = { ...options.terminal }
  if (options.timeoutMs !== undefined) request.timeoutMs = options.timeoutMs
  if (options.user !== undefined) request.user = options.user
  return request
}

export function createFetchDaytonaError(error: unknown): DaytonaError {
  if (error instanceof DaytonaError) {
    return error
  }
  if (error instanceof Error && (error.name === 'AbortError' || /timeout/i.test(error.message))) {
    return new DaytonaConnectionTimeoutError('Operation timed out')
  }
  return new DaytonaConnectionError(error instanceof Error ? error.message : String(error))
}

export async function createFetchResponseError(response: Response): Promise<DaytonaError> {
  const headerRecord: Record<string, string> = {}
  response.headers.forEach((value, key) => {
    headerRecord[key] = value
  })
  const headers: ResponseHeaders = new AxiosHeaders(headerRecord)
  const responseText = await response.text()
  const payload = parseErrorPayload(responseText)
  const message =
    typeof payload?.message === 'string'
      ? payload.message
      : typeof payload?.error === 'string'
        ? payload.error
        : responseText || response.statusText || `HTTP ${response.status}`
  const code =
    typeof payload?.code === 'string'
      ? payload.code
      : typeof payload?.error_code === 'string'
        ? payload.error_code
        : undefined
  const source = typeof payload?.source === 'string' ? payload.source : undefined
  return createDaytonaError(message, response.status, headers, code, source)
}

export function extractSseSegments(input: string): {
  events: Array<{ event: string; data: string }>
  remainder: string
} {
  const normalized = input.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const parts = normalized.split('\n\n')
  const remainder = parts.pop() ?? ''
  const events: Array<{ event: string; data: string }> = []

  for (const part of parts) {
    const lines = part.split('\n')
    let eventName = 'message'
    const dataLines: string[] = []
    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
        continue
      }
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }

    if (dataLines.length > 0) {
      events.push({ event: eventName, data: dataLines.join('\n') })
    }
  }

  return { events, remainder }
}

export function normalizeIdentifier(value: string, fieldName: string): string {
  const normalized = value.trim()
  if (normalized === '') {
    throw new DaytonaInvalidArgumentError(`${fieldName} must not be blank`)
  }
  return normalized
}

export function validateProcessStartOptions(options: ProcessStartOptions): void {
  const hasArgv = options.argv !== undefined && options.argv.length > 0
  const hasShellCommand = normalizeOptionalString(options.shellCommand) !== undefined
  const isPty = options.kind === 'pty'

  if (hasArgv && hasShellCommand) {
    throw new DaytonaInvalidArgumentError('provide exactly one of argv or shellCommand')
  }
  if (!hasArgv && !hasShellCommand && !isPty) {
    throw new DaytonaInvalidArgumentError('provide exactly one of argv or shellCommand')
  }
  if (options.name !== undefined && options.name.trim() === '') {
    throw new DaytonaInvalidArgumentError('name must not be blank')
  }
  validateWaitTimeout(options.timeoutMs)
}

export function validateKillOptions(options?: ProcessKillOptions): void {
  if (options?.escalateAfterMs !== undefined && options.escalateAfterMs < 0) {
    throw new DaytonaInvalidArgumentError('escalateAfterMs must be a non-negative number')
  }
}

export function validateWaitTimeout(timeoutMs: number | undefined): void {
  if (timeoutMs !== undefined && timeoutMs < 0) {
    throw new DaytonaInvalidArgumentError('timeoutMs must be a non-negative number')
  }
}

export function validateTerminalDimension(value: number, fieldName: string): void {
  if (!Number.isInteger(value) || value <= 0) {
    throw new DaytonaInvalidArgumentError(`${fieldName} must be a positive integer`)
  }
}

export function normalizeOptionalString(value: string | undefined): string | undefined {
  if (value === undefined) {
    return undefined
  }
  const normalized = value.trim()
  return normalized === '' ? undefined : normalized
}

export function normalizeSignal(value: string | undefined): string {
  return normalizeOptionalString(value) ?? 'SIGTERM'
}

export function normalizeHeaders(headers: unknown): Record<string, string> {
  if (headers === null || typeof headers !== 'object') {
    return {}
  }

  const normalized: Record<string, string> = {}
  for (const [key, value] of Object.entries(headers)) {
    if (typeof value === 'string') {
      normalized[key] = value
    }
  }
  return normalized
}

export function buildStreamingHeaders(baseHeaders: Record<string, string>, accept: string): Record<string, string> {
  if (requiresPreviewToken()) {
    const sdkVersion = baseHeaders['X-Daytona-SDK-Version']
    return sdkVersion ? { Accept: accept, 'X-Daytona-SDK-Version': sdkVersion } : { Accept: accept }
  }

  return {
    ...baseHeaders,
    Accept: accept,
  }
}

export function requiresPreviewToken(): boolean {
  return RUNTIME === Runtime.BROWSER || RUNTIME === Runtime.DENO || RUNTIME === Runtime.SERVERLESS
}

function parseErrorPayload(text: string): Record<string, unknown> | undefined {
  if (text.trim() === '') {
    return undefined
  }

  try {
    const parsed: unknown = JSON.parse(text)
    return parsed !== null && typeof parsed === 'object' ? Object.fromEntries(Object.entries(parsed)) : undefined
  } catch {
    return undefined
  }
}
