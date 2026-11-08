/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Freeze an SDK-owned data value at run time, plain objects and arrays at
 * every depth (JSON fields such as `Volume.options` included). Class
 * instances it reaches, such as live `Sandbox` handles, are left as they are.
 * @internal
 */
export function deepFrozen<T>(value: T): T {
  if (typeof value !== 'object' || value === null || Object.isFrozen(value)) return value
  // A plain object's prototype is some realm's Object.prototype (whose own
  // prototype is null), or null; a class instance's is its class prototype.
  const proto: unknown = Object.getPrototypeOf(value)
  const plain = proto === null || Object.getPrototypeOf(proto) === null
  if (!Array.isArray(value) && !plain) return value
  for (const key of Reflect.ownKeys(value)) deepFrozen(Reflect.get(value, key))
  return Object.freeze(value)
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
