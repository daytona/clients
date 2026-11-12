/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError, createDaytonaError } from './errors/DaytonaError.js'

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
 * Remembers, for one client and one kind (sandboxes, images, snapshots), that
 * the deployment does not serve search, so later pages and calls go straight
 * to the list route instead of sending a search request that fails first.
 * Same as Python's `_SearchProbe` and the CLI's `searchProbe`.
 * @internal
 */
export class SearchProbe {
  notServed = false
}

/**
 * One page from the search route (`search`), or from the list route (`list`,
 * the same query) where search is not served. A deployment with search serves
 * its lists from the search route (its list route answers 410 `GONE`); one
 * without it answers the search route with 501 `SEARCH_NOT_CONFIGURED` (an
 * older API with 404). Only those fall back, and `probe` remembers it: the
 * client's later calls for that kind skip the search request. Every other
 * error, a 503 `SEARCH_UNAVAILABLE` included, is thrown. Same as Python's
 * `_search_or_list`.
 * @internal
 */
export async function searchOrList<T>(search: () => Promise<T>, list: () => Promise<T>, probe: SearchProbe): Promise<T> {
  if (!probe.notServed) {
    try {
      return await search()
    } catch (error) {
      if (!searchNotServed(error)) throw error
      probe.notServed = true
    }
  }
  return list()
}

/**
 * Refuses `'search'` as an image or snapshot reference: `GET /images/search`
 * and `GET /snapshots/search` are the search routes, so that name cannot be
 * read by name (the API reserves it for new rows). Same as Python's
 * `_refuse_search_name`.
 * @internal
 */
export function refuseSearchName(ref: string, kind: 'image' | 'snapshot'): void {
  if (ref === 'search') {
    throw createDaytonaError(`'search' cannot be looked up by name; it is reserved for the search route — use the ${kind} ID`, 400, 'BAD_REQUEST', 'SDK')
  }
}
