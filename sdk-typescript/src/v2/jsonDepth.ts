/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The deepest nesting of arrays and objects a JSON response may have, the
 * same in both SDKs (Python's `JSON_MAX_DEPTH`, whose decoder has a limit of
 * its own that depends on the Python version and the thread's stack). Deep
 * enough for every contract shape: a 256-level accessibility tree is 516
 * levels of JSON.
 * @internal
 */
export const JSON_MAX_DEPTH = 600

/**
 * Whether arrays and objects in a decoded JSON `value` nest more than `limit`
 * deep (a top-level one is depth 1). Iterative, visiting containers only.
 * @internal
 */
export function nestedDeeperThan(value: unknown, limit: number = JSON_MAX_DEPTH): boolean {
  const pending: Array<readonly [unknown, number]> = [[value, 1]]
  for (let entry = pending.pop(); entry !== undefined; entry = pending.pop()) {
    const [item, depth] = entry
    if (typeof item !== 'object' || item === null) continue
    if (depth > limit) return true
    const children: unknown[] = Array.isArray(item) ? item : Object.values(item)
    for (const child of children) {
      if (typeof child === 'object' && child !== null) pending.push([child, depth + 1])
    }
  }
  return false
}
