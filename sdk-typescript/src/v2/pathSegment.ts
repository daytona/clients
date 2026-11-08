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
  return strictUriEncode(value)
}
