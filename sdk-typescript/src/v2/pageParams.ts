/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'
import { hasLoneSurrogate } from './pathSegment.js'

/**
 * A caller string sent as a query value: one with a lone surrogate is
 * `BadRequestError` naming `name`, before any request (axios's
 * `encodeURIComponent` would throw a raw `URIError`), as in Python.
 * @internal
 */
export function queryText(value: string, name: string): string {
  if (hasLoneSurrogate(value)) throw badRequest(`${name} must not contain a lone surrogate`)
  return value
}

/**
 * A caller string sent as a header value: printable ASCII only (the HTTP
 * clients cannot send anything else in both SDKs), else `BadRequestError`
 * naming `name`, before any request, as in Python.
 * @internal
 */
export function headerText(value: string, name: string): string {
  for (let index = 0; index < value.length; index++) {
    const unit = value.charCodeAt(index)
    if (unit < 0x20 || unit > 0x7e) throw badRequest(`${name} must be printable ASCII`)
  }
  return value
}

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
    params.cursor = queryText(cursor, 'cursor')
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
  return queryText(value, name)
}
