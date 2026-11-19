/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { SearchTotalRelation as SEARCH_TOTAL_RELATIONS } from './gen/api-enums.js'
import { deepFrozen } from './frozen.js'
import { knownOrUnknown } from './knownValue.js'
import { optionalQueryText, pageParams } from './pageParams.js'
import { searchTimestamp } from './clientSearch.js'
import { badRequest } from './options.js'
import type { SearchOrder, SearchResult, SearchTimestamp } from './types.js'

export interface CommonResourceSearchOptions {
  readonly q?: string
  readonly name?: string
  readonly namePrefix?: string
  readonly createdStartAt?: SearchTimestamp
  readonly createdEndAt?: SearchTimestamp
  readonly updatedStartAt?: SearchTimestamp
  readonly updatedEndAt?: SearchTimestamp
  readonly sort?: string
  readonly order?: SearchOrder
  readonly cursor?: string
  readonly limit?: number
}

export function commaList(name: string, values: string | readonly string[], choices: readonly string[]): string {
  const given: unknown = values
  if (typeof given !== 'string' && !Array.isArray(given)) throw badRequest(`${name} must be a string or an array of strings`)
  const items: readonly unknown[] = typeof given === 'string' ? [given] : given
  if (items.length === 0) throw badRequest(`${name} must name at least one value`)
  for (const item of items) {
    if (typeof item !== 'string' || item === '' || item.includes(',')) throw badRequest(`${name} entries must be non-empty strings without commas`)
    if (!choices.includes(item)) throw badRequest(`${name} entries must be one of ${choices.join(', ')}`)
  }
  return items.join(',')
}

export function resourceSearchParams(options: CommonResourceSearchOptions, sortChoices: readonly string[]): Record<string, string | number> {
  const params: Record<string, string | number> = {}
  const values = {
    q: optionalQueryText(options.q, 'q'),
    name: optionalQueryText(options.name, 'name'),
    name_prefix: optionalQueryText(options.namePrefix, 'namePrefix'),
    created_start_at: options.createdStartAt === undefined ? undefined : searchTimestamp(options.createdStartAt),
    created_end_at: options.createdEndAt === undefined ? undefined : searchTimestamp(options.createdEndAt),
    updated_start_at: options.updatedStartAt === undefined ? undefined : searchTimestamp(options.updatedStartAt),
    updated_end_at: options.updatedEndAt === undefined ? undefined : searchTimestamp(options.updatedEndAt),
    sort: choice(options.sort, 'sort', sortChoices),
    order: choice(options.order, 'order', ['asc', 'desc']),
    ...pageParams(options.cursor, options.limit),
  }
  for (const [key, value] of Object.entries(values)) if (value !== undefined) params[key] = value
  return params
}

export function resourceSearchResult<T>(data: unknown, field: string, mapItem: (value: unknown) => T): SearchResult<T> {
  if (!isJsonObject(data)) throw badRequest('search response is not a JSON object')
  const page = data
  const items = page[field]
  if (!Array.isArray(items)) throw badRequest(`search response is missing the ${field} array`)
  const total = page.total
  if (typeof total !== 'number' || !Number.isInteger(total) || total < 0) throw badRequest('search response has an invalid total')
  if (typeof page.total_relation !== 'string') throw badRequest('search response has an invalid total_relation')
  if (page.next_cursor !== null && page.next_cursor !== undefined && typeof page.next_cursor !== 'string') throw badRequest('search response has an invalid next_cursor')
  return deepFrozen({
    items: items.map(mapItem),
    nextCursor: page.next_cursor ?? undefined,
    total,
    totalRelation: knownOrUnknown(page.total_relation, SEARCH_TOTAL_RELATIONS),
    rawTotalRelation: page.total_relation,
  })
}

function isJsonObject(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function choice(value: unknown, name: string, choices: readonly string[]): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || !choices.includes(value)) throw badRequest(`${name} must be one of ${choices.join(', ')}`)
  return value
}
