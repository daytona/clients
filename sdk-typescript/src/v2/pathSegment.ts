/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { strictUriEncode } from './http.js'
import { badRequest } from './options.js'
import { isBlank } from './pyText.js'

/**
 * `.` and `..` are no ids: as a request-path segment, URL resolution removes
 * them, so the request would reach another route. Throws `BadRequestError`
 * naming `name` (as the Python SDK's `refuse_dot_segment`).
 * @internal
 */
export function refuseDotSegment(value: string, name: string): void {
  if (value === '.' || value === '..') throw badRequest(`${name} must not be '.' or '..'`)
}

/**
 * Every id or name a call puts into a request path (client, lifecycle,
 * platform services and toolbox alike) as one percent-encoded segment: a
 * string (else "must be a string") that is not blank by Python's whitespace
 * rules (else "must be a non-empty string"), is not `.` or `..`
 * (see {@link refuseDotSegment}) and has no surrogate code point (no UTF-8
 * form; `encodeURIComponent` would throw a `URIError`; in a JavaScript string
 * that is an unpaired UTF-16 surrogate, as a paired one is an astral
 * character, which is kept), else `BadRequestError` naming `name`, before any
 * request (as Python's `path_segment`).
 * @internal
 */
export function pathSegment(value: unknown, name: string): string {
  if (typeof value !== 'string') throw badRequest(`${name} must be a string`)
  if (isBlank(value)) throw badRequest(`${name} must be a non-empty string`)
  refuseDotSegment(value, name)
  if (hasLoneSurrogate(value)) throw badRequest(`${name} must not contain a surrogate code point`)
  return strictUriEncode(value)
}

/** Whether `value` holds a UTF-16 surrogate without its pair (it has no UTF-8 form). @internal */
export function hasLoneSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index++) {
    const unit = value.charCodeAt(index)
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (next >= 0xdc00 && next <= 0xdfff) {
        index++
        continue
      }
      return true
    }
    if (unit >= 0xdc00 && unit <= 0xdfff) return true
  }
  return false
}
