/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { strictUriEncode } from './http.js'
import { badRequest } from './options.js'

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
 * A toolbox id or name as one percent-encoded request-path segment (see
 * {@link refuseDotSegment}). A lone surrogate has no UTF-8 form
 * (`encodeURIComponent` would throw a `URIError`), so it is refused too, as
 * Python's `toolbox_path_id` does; astral characters are kept.
 * @internal
 */
export function toolboxPathId(value: string, name: string): string {
  refuseDotSegment(value, name)
  if (hasLoneSurrogate(value)) throw badRequest(`${name} must not contain a lone surrogate`)
  return strictUriEncode(value)
}

/** Whether `value` holds a UTF-16 surrogate without its pair. @internal */
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
