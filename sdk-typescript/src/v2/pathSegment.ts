/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { strictUriEncode } from './http.js'
import { badRequest } from './options.js'
import { isBlank } from './pyText.js'

/**
 * A caller's id or name as one request-path segment: a string that is not
 * blank by Python's whitespace rules, else `BadRequestError` naming `name`,
 * before any request (as Python's `_path`).
 * @internal
 */
export function pathSegment(value: unknown, name: string): string {
  if (typeof value !== 'string' || isBlank(value)) throw badRequest(`${name} must be a non-empty string`)
  // A lone surrogate has no UTF-8 form (encodeURIComponent would throw URIError).
  if (hasLoneSurrogate(value)) throw badRequest(`${name} must not contain a lone surrogate`)
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
