/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Allowlisted diagnostic views, including nested Node inspection with showHidden.
 * Symbol.for keeps this browser-safe and shared by the ESM and CJS builds.
 * This prevents accidental logging, not deliberate access to public credentials.
 */
export function credentialSafeInspection<T extends object, V extends object>(target: T, view: () => V): T {
  if (typeof Symbol !== 'undefined' && typeof Symbol.for === 'function') {
    Object.defineProperty(target, Symbol.for('nodejs.util.inspect.custom'), { value: view })
  }
  return target
}

/**
 * Turn `target`'s `fields` into non-enumerable own properties (a missing one
 * is defined as `undefined`), so object spread, `Object.keys` / `entries`,
 * `JSON.stringify` and deep-clone or error-reporting walkers skip them. A later
 * assignment keeps the property non-enumerable; the SDK still reads and writes
 * the field by name. Used for credentials and for fields that hold them.
 */
export function concealFields<T extends object>(target: T, fields: readonly string[]): T {
  for (const field of fields) {
    Object.defineProperty(target, field, {
      value: Reflect.get(target, field),
      writable: true,
      configurable: true,
      enumerable: false,
    })
  }
  return target
}

export function credentialSafeDiagnostics<T extends object, V extends object>(target: T, view: () => V): T {
  credentialSafeInspection(target, view)
  // Sandbox and other serializable handles keep their documented wire format.
  if (!('toJSON' in target)) Object.defineProperty(target, 'toJSON', { value: view })
  return target
}
