/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'

/**
 * The `cursor` / `limit` query of a list: a string cursor and an integer limit
 * from 1 to 200 (the contract's range), else `BadRequestError` naming the
 * parameter, before any request. `null` is omitted, as Python's `None` is.
 * @internal
 */
export function pageParams(cursor: unknown, limit: unknown): { cursor?: string; limit?: number } {
  const params: { cursor?: string; limit?: number } = {}
  if (cursor !== undefined && cursor !== null) {
    if (typeof cursor !== 'string') throw badRequest('cursor must be a string')
    params.cursor = cursor
  }
  if (limit !== undefined && limit !== null) {
    if (typeof limit !== 'number' || !Number.isInteger(limit) || limit < 1 || limit > 200) throw badRequest('limit must be an integer from 1 to 200')
    params.limit = limit
  }
  return params
}

/** A string query filter, or `undefined` (`null` is omitted); anything else is `BadRequestError`. @internal */
export function optionalQueryText(value: unknown, name: string): string | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'string') throw badRequest(`${name} must be a string`)
  return value
}
