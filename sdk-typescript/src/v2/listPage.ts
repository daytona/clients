/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'

/**
 * The entries and cursor of a list response, checked against the contract's
 * page shape: an object whose `field` is an array of objects and whose
 * `next_cursor`, when present, is a string or null. Anything else is the SDK's
 * own `BadRequestError` naming the field (the malformed-response rule), never
 * a raw `TypeError` or an empty page. Unknown extra fields are tolerated.
 * @internal
 */
export function listPage<T>(data: unknown, field: string, what: string): { readonly items: T[]; readonly nextCursor: string | undefined } {
  if (typeof data !== 'object' || data === null || Array.isArray(data)) throw badRequest(`${what} response is not a JSON object`)
  const entries: unknown = Reflect.get(data, field)
  if (!Array.isArray(entries)) throw badRequest(`${what} response is missing the ${field} array`)
  for (const entry of entries) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) throw badRequest(`${what} response has a ${field} entry that is not an object`)
  }
  const cursor: unknown = Reflect.get(data, 'next_cursor')
  if (cursor !== undefined && cursor !== null && typeof cursor !== 'string') throw badRequest(`${what} response has a next_cursor that is not a string`)
  // SAFETY: the envelope is checked above; entry fields are read by each mapper.
  return { items: entries as T[], nextCursor: typeof cursor === 'string' ? cursor : undefined }
}
