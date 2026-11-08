/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'

type Scalar = 'string' | 'number' | 'integer' | 'int32' | 'boolean' | 'object'

/**
 * `true`: the field may be absent or null. `'nullable'`: the contract requires
 * the key but allows `null` (an absent key is malformed). `false`: required.
 */
type Presence = boolean | 'nullable'

interface ArraySpec { readonly array: FieldSpec; readonly optional: Presence }
interface NestedSpec { readonly fields: ResponseFields; readonly optional: Presence }

/**
 * One field of a response: a scalar kind (`'object'` is any JSON object, such
 * as a label map), suffixed `?` when the contract lets the field be absent or
 * null, or `|null` when it requires the key but allows `null`; an array of a
 * field; or a nested set of fields.
 * @internal
 */
export type FieldSpec = Scalar | `${Scalar}?` | `${Scalar}|null` | ArraySpec | NestedSpec

/** @internal */
export type ResponseFields = Readonly<Record<string, FieldSpec>>

/** @internal */
export function arrayOf(item: FieldSpec, optional: Presence = false): ArraySpec {
  return { array: item, optional }
}

/** @internal */
export function nested(fields: ResponseFields, optional: Presence = false): NestedSpec {
  return { fields, optional }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function article(kind: string): string {
  if (kind === 'int32') return 'a 32-bit integer'
  return /^[aeiou]/.test(kind) ? `an ${kind}` : `a ${kind}`
}

function checkField(value: unknown, spec: FieldSpec, path: string, what: string): void {
  const presence: Presence = typeof spec === 'string' ? (spec.endsWith('?') ? true : spec.endsWith('|null') ? 'nullable' : false) : spec.optional
  if (value === undefined) {
    if (presence === true) return
    throw badRequest(`${what} response is missing its ${path}`)
  }
  if (value === null) {
    if (presence !== false) return
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
  const kind = spec.replace('?', '').replace('|null', '')
  const ok = kind === 'string' ? typeof value === 'string'
    : kind === 'number' ? typeof value === 'number' && Number.isFinite(value)
      // Any integral finite number (an int64 above 2^53 within a double's range
      // included, as in Python); a non-integral or non-finite one (JSON.parse
      // reads an integer too large for a double as Infinity) is refused.
      : kind === 'integer' ? Number.isInteger(value)
        // The contract's int32 format: its type rules out anything wider.
        : kind === 'int32' ? Number.isInteger(value) && (value as number) >= -2_147_483_648 && (value as number) <= 2_147_483_647
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
  sandbox_disk_mib: 'integer', max_sandboxes: 'integer', default_size: nested(MAC_RESOURCES, 'nullable'),
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
