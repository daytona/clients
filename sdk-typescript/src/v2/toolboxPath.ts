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

/** A toolbox id or name as one percent-encoded request-path segment (see {@link refuseDotSegment}). @internal */
export function toolboxPathId(value: string, name: string): string {
  refuseDotSegment(value, name)
  return strictUriEncode(value)
}
