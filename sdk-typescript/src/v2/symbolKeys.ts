/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** An object whose keys the SDK inspects (an options object, a decoded map); `null` ends a prototype chain. */
export type KeyedObject = Readonly<Record<PropertyKey, unknown>>
type PrototypeLevel = KeyedObject | null

/**
 * The first enumerable symbol key of `value`, own or inherited, or
 * `undefined` when there is none. The walk goes up the prototype chain to
 * `stopBefore` (exclusive): `null` walks the whole chain, `Object.prototype`
 * included, as `for...in` does for string keys.
 * @internal
 */
export function enumerableSymbolKey(value: KeyedObject, stopBefore: PrototypeLevel = null): symbol | undefined {
  for (let level: PrototypeLevel = value; level !== null && level !== stopBefore; level = Object.getPrototypeOf(level) as PrototypeLevel) {
    for (const symbol of Object.getOwnPropertySymbols(level)) {
      if (Object.prototype.propertyIsEnumerable.call(level, symbol)) return symbol
    }
  }
  return undefined
}
