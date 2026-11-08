/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'

type Scalar = 'string' | 'number' | 'integer' | 'boolean' | 'object'

interface ArraySpec { readonly array: FieldSpec; readonly optional: boolean }
interface NestedSpec { readonly fields: ResponseFields; readonly optional: boolean }

/**
 * One field of a response shape: a scalar kind (`'object'` is any JSON object,
 * such as a label map), optionally suffixed `?` when the contract lets the
 * field be absent or null, an array of a field, or a nested shape.
 * @internal
 */
export type FieldSpec = Scalar | `${Scalar}?` | ArraySpec | NestedSpec

/** @internal */
export type ResponseFields = Readonly<Record<string, FieldSpec>>

/** @internal */
export function arrayOf(item: FieldSpec, optional = false): ArraySpec {
  return { array: item, optional }
}

/** @internal */
export function nested(fields: ResponseFields, optional = false): NestedSpec {
  return { fields, optional }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function article(kind: string): string {
  return /^[aeiou]/.test(kind) ? `an ${kind}` : `a ${kind}`
}

function checkField(value: unknown, spec: FieldSpec, path: string, what: string): void {
  const optional = typeof spec === 'string' ? spec.endsWith('?') : spec.optional
  if (value === undefined || value === null) {
    if (optional) return
    throw badRequest(`${what} response is missing its ${path}`)
  }
  if (typeof spec !== 'string') {
    if ('array' in spec) {
      if (!Array.isArray(value)) throw badRequest(`${what} response has a ${path} that is not an array`)
      value.forEach((item: unknown, index) => checkField(item, spec.array, `${path}[${index}]`, what))
      return
    }
    if (!isObject(value)) throw badRequest(`${what} response has a ${path} that is not an object`)
    checkFields(value, spec.fields, `${path}.`, what)
    return
  }
  const kind = spec.replace('?', '')
  const ok = kind === 'string' ? typeof value === 'string'
    : kind === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : kind === 'integer' ? Number.isSafeInteger(value)
        : kind === 'boolean' ? typeof value === 'boolean'
          : isObject(value)
  if (!ok) throw badRequest(`${what} response has a ${path} that is not ${article(kind)}`)
}

function checkFields(value: Record<string, unknown>, fields: ResponseFields, prefix: string, what: string): void {
  for (const [field, spec] of Object.entries(fields)) checkField(Reflect.get(value, field), spec, `${prefix}${field}`, what)
}

/**
 * Check a response against the contract fields the SDK maps: a missing
 * required field or a wrong type, at any depth, is the SDK's own
 * `BadRequestError` naming the field (the malformed-response rule), before the
 * mapper reads it and before any further request. Unknown fields are tolerated.
 * @internal
 */
export function checkResponse<T>(value: unknown, fields: ResponseFields, what: string): T {
  if (!isObject(value)) throw badRequest(`${what} response is not a JSON object`)
  checkFields(value, fields, '', what)
  // SAFETY: every field the mappers read was checked against `fields` above.
  return value as T
}

/** The `{ id, status }` acknowledgement of an accepted asynchronous operation. @internal */
export const ASYNC_OPERATION: ResponseFields = { id: 'string', status: 'string' }

const TIMESTAMP = 'string'

/** @internal */
export const MAC_RESOURCES: ResponseFields = { cores: 'integer', memory_mib: 'integer', disk_mib: 'integer' }

/** @internal */
export const MAC_MODEL: ResponseFields = {
  id: 'string', name: 'string', cores: 'integer', memory_mib: 'integer', disk_mib: 'integer',
  sandbox_disk_mib: 'integer', max_sandboxes: 'integer', default_size: nested(MAC_RESOURCES, true),
}

/** @internal */
export const MAC_RESERVATION: ResponseFields = {
  id: 'string', org_id: 'string', project_id: 'string', region_id: 'string', status: 'string', origin: 'string',
  auto_renew: 'boolean', sandboxes_per_mac: 'integer', slots_used: 'integer', period_n: 'integer',
  period_seconds: 'integer', renewal_pending: 'boolean', created_at: TIMESTAMP,
  resources_used: nested(MAC_RESOURCES), capacity: nested(MAC_RESOURCES, true), model: 'string?',
  end_reason: 'string?', activated_at: 'string?', pending_mac_started_at: 'string?', period_started_at: 'string?',
  period_end: 'string?', reissued_at: 'string?', ended_at: 'string?', renewal_error: 'string?',
}
