/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// ASCII digits only (`[0-9]`), as Python's pattern reads them.
const RFC3339 = /^([0-9]{4})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$/

// An RFC 3339 timestamp without a leap second, as Python's `rfc3339_timestamp`
// checks it (search bounds, a create response's token expiry, every toolbox
// timestamp the SDK reads): year 0001-9999, the day
// within its month (Feb 29 only in a leap year; explicit arithmetic, since
// `Date.UTC` maps years 0-99 to 1900-1999), hour 00-23, minute and second
// 00-59 (no leap second: neither a `Date` nor a Python `datetime` can express
// one), an optional fraction, and `Z` or an offset within ±23:59.
/** @internal */
export function isRfc3339(value: string): boolean {
  const match = RFC3339.exec(value)
  if (match === null) return false
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] ?? 0
  return year >= 1 && day <= days
}
