/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, createDaytonaError } from './errors/DaytonaError.js'
import type { SandboxDto, SandboxState, SearchSandboxesOptions, SearchTimestamp } from './types.js'
import { assertLabels, badRequest } from './options.js'
import { optionalQueryText, pageParams, queryText } from './pageParams.js'
import { SandboxClass, SandboxSearchSort, SortOrder } from './gen/api-enums.js'
import { assertSandboxResponse } from './sandboxMetadata.js'

export const SEARCH_PATH = '/sandboxes/search'

/**
 * RFC 3339 form of a search bound; `Date` values are serialized in UTC.
 *
 * An invalid `Date` (`new Date('nope')`, `new Date(NaN)`) and anything other
 * than a `Date` or a string are the SDK's own {@link BadRequestError} — never
 * the `RangeError` `toISOString()` would throw — and no request goes out.
 * Mirrors Python's `_core.search_timestamp`.
 */
export function searchTimestamp(value: SearchTimestamp): string {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) {
      throw createDaytonaError('timestamp bounds must be a valid Date or an RFC 3339 string', 400, 'BAD_REQUEST', 'SDK')
    }
    return value.toISOString()
  }
  // An RFC 3339 date-time (the contract's format), the same pattern Python checks.
  if (typeof value === 'string' && RFC3339.test(value)) return value
  throw createDaytonaError('timestamp bounds must be a valid Date or an RFC 3339 string', 400, 'BAD_REQUEST', 'SDK')
}

const RFC3339 = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])[Tt](?:[01]\d|2[0-3]):[0-5]\d:(?:[0-5]\d|60)(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/

// Input-boundary checks mirror Python's `_core.search_params` byte for byte:
// a bare string is one entry (the option types are `string | readonly
// string[]`, like Python's `Sequence[str]`), and — for JS callers the types
// cannot stop — non-string entries, commas and empties are the SDK's own
// BadRequestError, and no request goes out for any of them.
function commaList(name: string, values: string | readonly string[]): string {
  const given: unknown = values
  // A string or an array of strings, as Python takes a string or a sequence.
  if (typeof given !== 'string' && !Array.isArray(given)) {
    throw createDaytonaError(`${name} must be a string or a list of strings`, 400, 'BAD_REQUEST', 'SDK')
  }
  const items: readonly unknown[] = typeof given === 'string' ? [given] : given
  // An empty list is not "no filter": the server reads an empty parameter as
  // absent, so `status: []` would silently become an unfiltered search.
  // Refuse it, like ['']; a non-array lands here instead of a raw TypeError.
  if (items.length === 0) {
    throw createDaytonaError(`${name} must name at least one value`, 400, 'BAD_REQUEST', 'SDK')
  }
  for (const value of items) {
    if (typeof value !== 'string' || !value || value.includes(',')) {
      throw createDaytonaError(`${name} entries must be non-empty strings without commas`, 400, 'BAD_REQUEST', 'SDK')
    }
  }
  return items.join(',')
}

// The contract's classes only: SDK inputs are closed.
function searchClasses(values: string | readonly string[]): string {
  const joined = commaList('sandboxClass', values)
  if (joined.split(',').some((item) => !(SandboxClass as readonly string[]).includes(item))) {
    throw createDaytonaError('sandboxClass entries must be "linux" or "macos"', 400, 'BAD_REQUEST', 'SDK')
  }
  return joined
}

/** `labels` must be a plain object (Python: a `Mapping`); a string or an array is rejected before the request. */
function labelsParam(labels: unknown): string {
  assertLabels(labels)
  if (Object.keys(labels).length === 0) {
    throw createDaytonaError('labels must name at least one pair', 400, 'BAD_REQUEST', 'SDK')
  }
  return JSON.stringify(labels)
}

function boolParam(value: unknown, name: string): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'boolean') throw badRequest(`${name} must be a boolean`)
  return value ? 'true' : 'false'
}

function numberParam(value: unknown, name: string): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value)) throw badRequest(`${name} must be a finite number`)
  return value
}

// An int64 both SDKs carry exactly.
function integerParam(value: unknown, name: string): number | undefined {
  if (value === undefined) return undefined
  if (!Number.isSafeInteger(value)) throw badRequest(`${name} must be an integer`)
  return value as number
}

// The contract's values only: SDK inputs are closed.
function choiceParam<T extends string>(value: unknown, name: string, choices: readonly T[]): T | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || !(choices as readonly string[]).includes(value)) throw badRequest(`${name} must be one of ${choices.join(', ')}`)
  return value as T
}

function timestampParam(value: SearchTimestamp | undefined, name: string): string | undefined {
  return value === undefined ? undefined : queryText(searchTimestamp(value), name)
}

export type SearchQueryParams = Record<string, string | number>

/** Wire query for `GET /sandboxes/search`: arrays comma-joined, labels JSON, timestamps RFC 3339, snake_case names. */
export function searchQueryParams(input: SearchSandboxesOptions): SearchQueryParams {
  // JS `null` on any option means absent, like Python's `None`.
  const options = Object.fromEntries(
    Object.entries(input).filter(([, value]) => value != null),
  ) as SearchSandboxesOptions
  const candidates = {
    q: optionalQueryText(options.q, 'q'),
    name_prefix: optionalQueryText(options.namePrefix, 'namePrefix'),
    id_prefix: optionalQueryText(options.idPrefix, 'idPrefix'),
    status: options.states === undefined ? undefined : searchStates(options.states),
    class: options.sandboxClass === undefined ? undefined : queryText(searchClasses(options.sandboxClass), 'sandboxClass'),
    region_ids: options.region === undefined ? undefined : queryText(commaList('region', options.region), 'region'),
    image_ref: optionalQueryText(options.imageRef, 'imageRef'),
    image_ref_prefix: optionalQueryText(options.imageRefPrefix, 'imageRefPrefix'),
    labels: options.labels === undefined ? undefined : labelsParam(options.labels),
    spot: boolParam(options.spot, 'spot'),
    forked_from: optionalQueryText(options.forkedFrom, 'forkedFrom'),
    min_cores: numberParam(options.minCores, 'minCores'),
    max_cores: numberParam(options.maxCores, 'maxCores'),
    min_memory_mib: integerParam(options.minMemoryMib, 'minMemoryMib'),
    max_memory_mib: integerParam(options.maxMemoryMib, 'maxMemoryMib'),
    min_disk_mib: integerParam(options.minDiskMib, 'minDiskMib'),
    max_disk_mib: integerParam(options.maxDiskMib, 'maxDiskMib'),
    created_start_at: timestampParam(options.createdStartAt, 'createdStartAt'),
    created_end_at: timestampParam(options.createdEndAt, 'createdEndAt'),
    updated_start_at: timestampParam(options.updatedStartAt, 'updatedStartAt'),
    updated_end_at: timestampParam(options.updatedEndAt, 'updatedEndAt'),
    last_activity_start_at: timestampParam(options.lastActivityStartAt, 'lastActivityStartAt'),
    last_activity_end_at: timestampParam(options.lastActivityEndAt, 'lastActivityEndAt'),
    sort: choiceParam(options.sort, 'sort', SandboxSearchSort),
    order: choiceParam(options.order, 'order', SortOrder),
    ...pageParams(options.cursor, options.limit),
  }
  const params: SearchQueryParams = {}
  for (const [key, value] of Object.entries(candidates)) {
    if (value !== undefined) params[key] = value
  }
  return params
}

const SEARCH_STATES: Readonly<Record<SandboxState, readonly string[]>> = {
  queued: ['queued'], creating: ['pending', 'assigned'], starting: ['booting'], started: ['running'],
  pausing: ['pausing'], paused: ['paused'], stopping: ['stopping'], stopped: ['stopped'],
  destroying: ['deleting'], destroyed: ['deleted'], error: ['failed', 'lost'], unknown: [],
}

function searchStates(states: SandboxState | readonly SandboxState[]): string {
  const values: readonly SandboxState[] = typeof states === 'string' ? [states] : Array.isArray(states) ? states : []
  if (values.length === 0) throw new BadRequestError('states must name at least one value', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  const wire = new Set<string>()
  for (const state of values) {
    if (state === 'unknown' || !Object.prototype.hasOwnProperty.call(SEARCH_STATES, state)) {
      throw new BadRequestError(`unknown sandbox state ${String(state)}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    for (const value of SEARCH_STATES[state]) wire.add(value)
  }
  return [...wire].join(',')
}

/** @internal */
export interface SearchPageDto {
  readonly sandboxes?: unknown
  readonly next_cursor?: string | null
  readonly total?: number
  readonly total_relation?: string
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * The `GET /sandboxes/search` contract: an object with a `sandboxes` array of
 * sandbox objects (each checked as a detail is), an integer `total`, a string
 * `total_relation` and a string or null `next_cursor`. Anything else is the
 * SDK's own {@link BadRequestError} naming the field, never an empty or
 * truncated page — an `items` array is NOT accepted as a stand-in. Unknown
 * extra fields are tolerated. Mirrors Python's `_core.parse_search_page`.
 * @internal
 */
export function searchPageSandboxes(data: unknown): { readonly sandboxes: SandboxDto[]; readonly total: number; readonly totalRelation: string; readonly nextCursor: string | undefined } {
  if (!isJsonObject(data)) throw badRequest('search response is not a JSON object')
  const sandboxes: unknown = data.sandboxes
  if (!Array.isArray(sandboxes)) throw badRequest('search response is missing the sandboxes array')
  sandboxes.forEach(assertSandboxResponse)
  if (!Number.isInteger(data.total)) throw badRequest(data.total == null ? 'search response is missing its total' : 'search response has a total that is not an integer')
  if (typeof data.total_relation !== 'string') throw badRequest(data.total_relation == null ? 'search response is missing its total_relation' : 'search response has a total_relation that is not a string')
  // A cursor of the wrong type would silently end search() / findByName() early.
  if (data.next_cursor != null && typeof data.next_cursor !== 'string') throw badRequest('search response has a next_cursor that is not a string')
  // SAFETY: every item was checked by assertSandboxResponse above.
  return { sandboxes: sandboxes as SandboxDto[], total: data.total as number, totalRelation: data.total_relation, nextCursor: data.next_cursor ?? undefined }
}
