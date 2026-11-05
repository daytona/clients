/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

function isKnownValue<const T extends readonly string[]>(value: string, known: T): value is T[number] {
  // SAFETY: `known` is already the exact readonly tuple of allowed strings.
  // Widening it to `readonly string[]` enables `.includes()` only; it does not change the values.
  return (known as readonly string[]).includes(value)
}

export function knownOrUnknown<const T extends readonly string[]>(value: string, known: T): T[number] | 'unknown' {
  return isKnownValue(value, known) ? value : 'unknown'
}
