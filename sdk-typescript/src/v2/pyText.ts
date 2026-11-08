/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Python's `str.isspace()` set, exactly: JavaScript's `trim()` also strips
// U+FEFF and keeps U+001C-U+001F and U+0085, so a name made only of those
// would be blank in one SDK and not the other.
const SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
  0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
])

function isSpace(value: string, index: number): boolean {
  return SPACE.has(value.charCodeAt(index))
}

/** Python's `not value.strip()`. @internal */
export function isBlank(value: string): boolean {
  for (let index = 0; index < value.length; index++) if (!isSpace(value, index)) return false
  return true
}

/** Python's `value.strip()`, in linear time. @internal */
export function strip(value: string): string {
  let start = 0
  let end = value.length
  while (start < end && isSpace(value, start)) start++
  while (end > start && isSpace(value, end - 1)) end--
  return value.slice(start, end)
}
