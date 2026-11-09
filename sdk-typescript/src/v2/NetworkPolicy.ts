/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosResponse } from 'axios'
import { BadRequestError, InternalServerError } from './errors/DaytonaError.js'
import type { NetworkPolicy, NetworkPolicyInfo, NetworkPolicyInput } from './types.js'

const INPUT_FIELDS = new Set(['blockAll', 'allowCidrs', 'allowDomains'])

export function createNetworkPolicyCreateField(options: Record<string, unknown>): Record<string, unknown> | null | undefined {
  if (!Object.prototype.hasOwnProperty.call(options, 'networkPolicy')) return undefined
  const value = options.networkPolicy
  if (value === undefined) return undefined
  return value === null ? null : serializeNetworkPolicyInput(value)
}

export function serializeNetworkPolicyInput(input: unknown): Record<string, unknown> {
  if (!isRecord(input)) {
    throw new BadRequestError('networkPolicy must be an object', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  const unknownFields = Object.keys(input).filter((field) => !INPUT_FIELDS.has(field))
  if (unknownFields.length > 0) {
    throw new BadRequestError(`networkPolicy contains unknown fields: ${unknownFields.join(', ')}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  // An explicitly `undefined` optional field is the same as an omitted one
  // (computed optionals), while `null` and `[]` keep their distinct meanings.
  const body: Record<string, unknown> = {}
  let blockAll: boolean | undefined
  if (input.blockAll !== undefined && input.blockAll !== null) {
    if (typeof input.blockAll !== 'boolean') {
      throw new BadRequestError('networkPolicy.blockAll must be a boolean', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    blockAll = input.blockAll
  }
  if (input.allowCidrs !== undefined) {
    body.allow_cidrs = requestStringArray(input.allowCidrs, 'networkPolicy.allowCidrs')
  }
  let allowDomains: string[] | null | undefined
  if (input.allowDomains !== undefined) {
    allowDomains = input.allowDomains === null ? null : requestStringArray(input.allowDomains, 'networkPolicy.allowDomains')
    body.allow_domains = allowDomains
  }
  if (allowDomains !== undefined && allowDomains !== null && blockAll === false) {
    // Same code as the API's own 400 (the server catalog is upper case), so
    // callers can key on one value whichever side refused.
    throw new BadRequestError(
      'network_policy allow_domains requires block_all to be true or omitted',
      { statusCode: 400, code: 'NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL', source: 'SDK' },
    )
  }
  if (blockAll !== undefined) {
    body.block_all = blockAll
  }
  return body
}

export function parseNetworkPolicyResponse(response: AxiosResponse<unknown>): NetworkPolicyInfo {
  const data = recordValue(response.data, 'network policy response')
  const etagHeader = response.headers.etag
  if (typeof etagHeader !== 'string' || etagHeader.length === 0) {
    throw new InternalServerError('network policy response is missing ETag', { statusCode: 500, source: 'SDK' })
  }
  return {
    desired: optionalNetworkPolicy(data.desired, 'desired'),
    desiredRevision: requiredInteger(data.desired_revision, 'desired_revision'),
    etag: etagHeader,
    httpStatus: response.status,
    applied: optionalNetworkPolicy(data.applied, 'applied'),
    appliedRevision: optionalInteger(data.applied_revision, 'applied_revision'),
    appliedStartId: optionalString(data.applied_start_id),
    status: optionalString(data.status),
    error: optionalString(data.error),
  }
}

function optionalNetworkPolicy(value: unknown, fieldName: string): NetworkPolicy | undefined {
  if (value === undefined || value === null) return undefined
  const data = recordValue(value, fieldName)
  return {
    version: requiredInteger(data.version, `${fieldName}.version`),
    blockAll: requiredBoolean(data.block_all, `${fieldName}.block_all`),
    allowCidrs: responseStringArray(data.allow_cidrs, `${fieldName}.allow_cidrs`),
    allowDomains: optionalResponseStringArray(data.allow_domains, `${fieldName}.allow_domains`),
  }
}

function requestStringArray(value: unknown, fieldName: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new BadRequestError(`${fieldName} must be an array of strings`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return [...value]
}

function responseStringArray(value: unknown, fieldName: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new InternalServerError(`${fieldName} must be an array of strings`, { statusCode: 500, source: 'SDK' })
  }
  return [...value]
}

function optionalResponseStringArray(value: unknown, fieldName: string): string[] | null {
  if (value === undefined || value === null) return null
  return responseStringArray(value, fieldName)
}

function requiredBoolean(value: unknown, fieldName: string): boolean {
  if (typeof value === 'boolean') return value
  throw new InternalServerError(`${fieldName} must be a boolean`, { statusCode: 500, source: 'SDK' })
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

export type { NetworkPolicyInput }
