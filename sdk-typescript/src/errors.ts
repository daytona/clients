/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

type RegisteredErrorClass = (abstract new (...args: never[]) => Error) & Function

/**
 * The shared-category registry lives on `globalThis` under a registered
 * symbol, keyed by a stable category name (never `class.name`, which bundlers
 * may mangle). The package ships the root as ESM and CJS but `./v0` as CJS
 * only, so a module-local registry would split between copies; the bundled v0
 * entry registers its classes into this same map when it loads (appended by
 * scripts/bundle-v0.mjs), whether through `daytona.v0` or an import of v0's
 * types and errors.
 */
export const ERROR_REGISTRY_KEY = Symbol.for('@daytona/sdk/error-registry')

type ErrorRegistry = Map<string, Function[]>

function errorRegistry(): ErrorRegistry {
  const holder = globalThis as unknown as Record<symbol, ErrorRegistry | undefined>
  let registry = holder[ERROR_REGISTRY_KEY]
  if (registry === undefined) {
    registry = new Map()
    holder[ERROR_REGISTRY_KEY] = registry
  }
  return registry
}

const categoryKeys = new WeakMap<Function, string>()

function nativeHasInstance(target: Function, value: unknown): boolean {
  return Function.prototype[Symbol.hasInstance].call(target, value) as boolean
}

function registeredHasInstance(target: Function, value: unknown): boolean {
  const key = categoryKeys.get(target)
  if (key === undefined) return false
  return (errorRegistry().get(key) ?? []).some((member) => nativeHasInstance(member, value))
}

export function registerErrorClasses(root: RegisteredErrorClass, members: readonly RegisteredErrorClass[]): void {
  const key = categoryKeys.get(root)
  if (key === undefined) throw new TypeError('registerErrorClasses: not a shared root error class')
  const registry = errorRegistry()
  const next = [...(registry.get(key) ?? [])]
  for (const member of members) {
    if (!next.includes(member)) next.push(member)
  }
  registry.set(key, next)
}

export class DaytonaError extends Error {
  // Fields both namespaces' error classes set (v0's and v2's). Declared, not
  // emitted: the root classes stay stateless markers; an instance from either
  // namespace carries the values.
  declare readonly statusCode?: number
  declare readonly code?: string
  declare readonly source?: string

  constructor(message = '') {
    super(message)
    this.name = new.target.name
    Object.setPrototypeOf(this, new.target.prototype)
  }

  static [Symbol.hasInstance](value: unknown): boolean {
    return nativeHasInstance(this, value) || registeredHasInstance(this, value)
  }
}

export class DaytonaBadRequestError extends DaytonaError {}
export class DaytonaAuthenticationError extends DaytonaError {}
export class DaytonaForbiddenError extends DaytonaError {}
export class DaytonaNotFoundError extends DaytonaError {}
export class DaytonaTimeoutError extends DaytonaError {}
export class DaytonaConflictError extends DaytonaError {}
export class DaytonaGoneError extends DaytonaError {}
export class DaytonaUnprocessableEntityError extends DaytonaError {}
export class DaytonaRateLimitError extends DaytonaError {}
export class DaytonaInternalServerError extends DaytonaError {}
export class DaytonaServiceUnavailableError extends DaytonaError {}
export class DaytonaConnectionError extends DaytonaError {}

// The category keys the registry is indexed by: also the names v0 exports.
for (const [key, category] of [
  ['DaytonaError', DaytonaError],
  ['DaytonaBadRequestError', DaytonaBadRequestError],
  ['DaytonaAuthenticationError', DaytonaAuthenticationError],
  ['DaytonaForbiddenError', DaytonaForbiddenError],
  ['DaytonaNotFoundError', DaytonaNotFoundError],
  ['DaytonaTimeoutError', DaytonaTimeoutError],
  ['DaytonaConflictError', DaytonaConflictError],
  ['DaytonaGoneError', DaytonaGoneError],
  ['DaytonaUnprocessableEntityError', DaytonaUnprocessableEntityError],
  ['DaytonaRateLimitError', DaytonaRateLimitError],
  ['DaytonaInternalServerError', DaytonaInternalServerError],
  ['DaytonaServiceUnavailableError', DaytonaServiceUnavailableError],
  ['DaytonaConnectionError', DaytonaConnectionError],
] as const) {
  categoryKeys.set(category, key)
  // The root classes join their own category, so a root marker built by the
  // ESM copy matches the CJS copy's class (and vice versa).
  registerErrorClasses(category, [category])
}
