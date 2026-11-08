/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, createDaytonaError } from './errors/DaytonaError.js'
import type { SandboxDto, SandboxState, SearchSandboxesOptions, SearchTimestamp } from './types.js'
import { assertLabels } from './options.js'

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
  if (typeof value === 'string') return value
  throw createDaytonaError('timestamp bounds must be a valid Date or an RFC 3339 string', 400, 'BAD_REQUEST', 'SDK')
}

// Input-boundary checks mirror Python's `_core.search_params` byte for byte:
// a bare string is one entry (the option types are `string | readonly
// string[]`, like Python's `Sequence[str]`), and — for JS callers the types
// cannot stop — non-string entries, commas and empties are the SDK's own
// BadRequestError, and no request goes out for any of them.
function commaList(name: string, values: string | readonly string[]): string {
  const items: readonly unknown[] = typeof values === 'string' ? [values] : Array.isArray(values) ? values : []
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

/** `labels` must be a plain object (Python: a `Mapping`); a string or an array is rejected before the request. */
function labelsParam(labels: unknown): string {
  assertLabels(labels)
  if (Object.keys(labels).length === 0) {
    throw createDaytonaError('labels must name at least one pair', 400, 'BAD_REQUEST', 'SDK')
  }
  return JSON.stringify(labels)
}

function boolParam(value: boolean | undefined): string | undefined {
  return value === undefined ? undefined : value ? 'true' : 'false'
}

function timestampParam(value: SearchTimestamp | undefined): string | undefined {
  return value === undefined ? undefined : searchTimestamp(value)
}

export type SearchQueryParams = Record<string, string | number>

/** Wire query for `GET /sandboxes/search`: arrays comma-joined, labels JSON, timestamps RFC 3339, snake_case names. */
export function searchQueryParams(input: SearchSandboxesOptions): SearchQueryParams {
  // JS `null` on any option means absent, like Python's `None`.
  const options = Object.fromEntries(
    Object.entries(input).filter(([, value]) => value != null),
  ) as SearchSandboxesOptions
  const candidates = {
    q: options.q,
    name_prefix: options.namePrefix,
    id_prefix: options.idPrefix,
    status: options.states === undefined ? undefined : searchStates(options.states),
    class: options.sandboxClass === undefined ? undefined : commaList('sandboxClass', options.sandboxClass),
    region_ids: options.region === undefined ? undefined : commaList('region', options.region),
    image_ref: options.imageRef,
    image_ref_prefix: options.imageRefPrefix,
    labels: options.labels === undefined ? undefined : labelsParam(options.labels),
    spot: boolParam(options.spot),
    forked_from: options.forkedFrom,
    min_cores: options.minCores,
    max_cores: options.maxCores,
    min_memory_mib: options.minMemoryMib,
    max_memory_mib: options.maxMemoryMib,
    min_disk_mib: options.minDiskMib,
    max_disk_mib: options.maxDiskMib,
    created_start_at: timestampParam(options.createdStartAt),
    created_end_at: timestampParam(options.createdEndAt),
    updated_start_at: timestampParam(options.updatedStartAt),
    updated_end_at: timestampParam(options.updatedEndAt),
    last_activity_start_at: timestampParam(options.lastActivityStartAt),
    last_activity_end_at: timestampParam(options.lastActivityEndAt),
    sort: options.sort,
    order: options.order,
    limit: options.limit,
    cursor: options.cursor,
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
 * The `GET /sandboxes/search` contract is an object
 * with a `sandboxes` array: a non-object body, or a missing / non-array `sandboxes`, is the
 * SDK's own {@link BadRequestError} rather than an empty page — an `items`
 * array is NOT accepted as a stand-in. Unknown extra fields are tolerated.
 * Mirrors Python's `_core.parse_search_page`.
 * @internal
 */
export function searchPageSandboxes(data: unknown): SandboxDto[] {
  if (!isJsonObject(data)) {
    throw createDaytonaError('expected JSON object', 400, 'BAD_REQUEST', 'SDK')
  }
  const sandboxes = (data as SearchPageDto).sandboxes
  if (!Array.isArray(sandboxes)) {
    throw createDaytonaError('search response is missing the sandboxes array', 400, 'BAD_REQUEST', 'SDK')
  }
  for (const item of sandboxes) {
    if (!isJsonObject(item)) throw createDaytonaError('expected JSON object', 400, 'BAD_REQUEST', 'SDK')
    // The contract requires `class` on every sandbox object.
    if (typeof item.class !== 'string' || item.class === '') throw createDaytonaError('search response has a sandbox without its class', 400, 'BAD_REQUEST', 'SDK')
  }
  return sandboxes as SandboxDto[]
}
