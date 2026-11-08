/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosResponse } from 'axios'
import { BadRequestError, InternalServerError } from './errors/DaytonaError.js'
import type { SandboxSecretsInfo } from './types.js'

export function serializeSandboxSecretsInput(secrets: readonly string[]): Record<string, readonly string[]> {
  if (!Array.isArray(secrets) || secrets.some((item) => typeof item !== 'string')) {
    throw new BadRequestError('sandbox secrets must be an array of strings', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return { secrets: [...secrets] }
}

export function parseSandboxSecretsResponse(response: AxiosResponse<unknown>): SandboxSecretsInfo {
  const data = recordValue(response.data, 'sandbox secrets response')
  const etagHeader = response.headers.etag
  if (typeof etagHeader !== 'string' || etagHeader.length === 0) {
    throw new InternalServerError('sandbox secrets response is missing ETag', { statusCode: 500, source: 'SDK' })
  }
  return {
    secrets: requiredStringArray(data.secrets, 'secrets'),
    desiredRevision: requiredInteger(data.desired_revision, 'desired_revision'),
    etag: etagHeader,
    httpStatus: response.status,
    appliedRevision: optionalInteger(data.applied_revision, 'applied_revision'),
    status: optionalString(data.status),
    error: optionalString(data.error),
    captureStatus: optionalString(data.capture_status),
    captureUnresolvedCount: optionalInteger(data.capture_unresolved_count, 'capture_unresolved_count'),
  }
}

function requiredStringArray(value: unknown, fieldName: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new InternalServerError(`${fieldName} must be an array of strings`, { statusCode: 500, source: 'SDK' })
  }
  return [...value]
}

function requiredInteger(value: unknown, fieldName: string): number {
  if (typeof value === 'number' && Number.isInteger(value)) return value
  throw new InternalServerError(`${fieldName} must be an integer`, { statusCode: 500, source: 'SDK' })
}

function optionalInteger(value: unknown, fieldName: string): number | undefined {
  if (value === undefined || value === null) return undefined
  return requiredInteger(value, fieldName)
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function recordValue(value: unknown, fieldName: string): Record<string, unknown> {
  if (isRecord(value)) return value
  throw new InternalServerError(`${fieldName} must be an object`, { statusCode: 500, source: 'SDK' })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
