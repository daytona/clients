/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Python's `str.isspace()` set, exactly: JavaScript's `trim()` also strips
// U+FEFF and keeps U+001C-U+001F and U+0085, so a name made only of those
// would be blank in one SDK and not the other.
const SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
const BLANK = new RegExp(`^[${SPACE}]*$`)
const EDGES = new RegExp(`^[${SPACE}]+|[${SPACE}]+$`, 'g')

/** Python's `not value.strip()`. @internal */
export function isBlank(value: string): boolean {
  return BLANK.test(value)
}

/** Python's `value.strip()`. @internal */
export function strip(value: string): string {
  return value.replace(EDGES, '')
}
