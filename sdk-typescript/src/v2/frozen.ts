/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

function plainData(value: unknown): value is object {
  if (typeof value !== 'object' || value === null) return false
  // A plain object's prototype is some realm's Object.prototype (whose own
  // prototype is null), or null; a class instance's is its class prototype.
  const proto: unknown = Object.getPrototypeOf(value)
  return Array.isArray(value) || proto === null || Object.getPrototypeOf(proto) === null
}

/**
 * Freeze an SDK-owned data value at run time, plain objects and arrays at
 * every depth (JSON fields such as `Volume.options` included), the children
 * of one that is already frozen included. Class instances it reaches, such as
 * live `Sandbox` handles, are left as they are. Iterative, so a deeply nested
 * response cannot exhaust the stack; each object is visited once, so a cycle
 * ends.
 * @internal
 */
export function deepFrozen<T>(value: T): T {
  const visited = new WeakSet<object>()
  const pending: unknown[] = [value]
  while (pending.length > 0) {
    const item = pending.pop()
    if (!plainData(item) || visited.has(item)) continue
    visited.add(item)
    Object.freeze(item)
    for (const key of Reflect.ownKeys(item)) pending.push(Reflect.get(item, key))
  }
  return value
}

/**
 * A frozen page envelope over frozen items (handles stay live; data items are
 * frozen by their mappers).
 * @internal
 */
export function frozenPage<P extends { readonly items: readonly unknown[] }>(page: P): P {
  Object.freeze(page.items)
  return Object.freeze(page)
}
