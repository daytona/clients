/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError } from './errors/DaytonaError.js'

/**
 * Whether an error from a search route means the deployment does not serve
 * search: 501 `SEARCH_NOT_CONFIGURED`, or 404 from an API that predates the
 * route. A 503 `SEARCH_UNAVAILABLE` (search configured but down) is not.
 * @internal
 */
export function searchNotServed(error: unknown): boolean {
  if (!(error instanceof DaytonaError)) return false
  if (error.statusCode === 404) return true
  return error.statusCode === 501 && error.code === 'SEARCH_NOT_CONFIGURED'
}

/**
 * One page from the search route (`search`), or from the list route (`list`,
 * the same query) where search is not served. A deployment with search serves
 * its lists from the search route (its list route answers 410 `GONE`); one
 * without it answers the search route with 501 `SEARCH_NOT_CONFIGURED` (an
 * older API with 404). Only those fall back; every other error is thrown.
 * Same as Python's `_search_or_list`.
 * @internal
 */
export async function searchOrList<T>(search: () => Promise<T>, list: () => Promise<T>): Promise<T> {
  try {
    return await search()
  } catch (error) {
    if (!searchNotServed(error)) throw error
  }
  return list()
}
