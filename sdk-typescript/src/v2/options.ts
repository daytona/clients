/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from './errors/DaytonaError.js'

/**
 * Runtime validation of the options objects the SDK defines.
 *
 * TypeScript callers get a compile error for an unknown option; plain
 * JavaScript callers (and callers that cast) would otherwise have it ignored
 * without a word, which turns a renamed option into a silently different
 * behaviour. Every public options type therefore declares its known keys once
 * (the client configuration objects do today; the per-call options follow area
 * by area):
 *
 * ```ts
 * const CREATE_OPTION_KEYS = optionKeys<CreateOptions>()({ name: true, labels: true })
 * // ...
 * assertKnownOptions('CreateOptions', options, CREATE_OPTION_KEYS)
 * ```
 *
 * `optionKeys<T>()` checks at compile time that the declaration lists every
 * key of `T` and nothing else, so adding an option without listing it fails
 * the typecheck. The check applies to the options objects themselves, never
 * to pass-through maps inside them (`env`, `labels`, ...).
 */

/** The known keys of an options type: every key of `T`, each mapped to `true`. */
export type OptionKeys<T> = { readonly [K in keyof Required<T>]: true }

type ExactKeys<T, D> = D & { readonly [K in Exclude<keyof D, keyof T>]: never }

/**
 * Declare the known keys of options type `T`. Curried so `T` is given
 * explicitly while the declaration's own keys are inferred: a missing key, an
 * extra key or a value other than `true` is a compile error.
 */
export function optionKeys<T>(): <const D extends OptionKeys<T>>(keys: ExactKeys<T, D>) => OptionKeys<T> {
  return (keys) => {
    const declared: OptionKeys<T> = keys
    return Object.freeze(declared)
  }
}

/**
 * Options renamed when every SDK duration moved to seconds. An old name that
 * is not a known key of the options type is reported with its replacement.
 */
export const RENAMED_OPTIONS: Readonly<Record<string, string>> = Object.freeze({
  timeout: 'timeoutSeconds',
  executionTimeout: 'executionTimeoutSeconds',
  escalateAfter: 'escalateAfterSeconds',
  requestTimeout: 'requestTimeoutSeconds',
  reconcileIntervalMs: 'reconcileIntervalSeconds',
  idleCloseMs: 'idleCloseSeconds',
})

/** The SDK's own invalid-input error: status 400, code `BAD_REQUEST`, source `SDK`. */
export function badRequest(message: string): BadRequestError {
  return new BadRequestError(message, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

export interface IfMatchOptions {
  readonly ifMatch: string
}

const IF_MATCH_KEYS = optionKeys<IfMatchOptions>()({ ifMatch: true })

/** @internal */
export function assertIfMatchOptions(options: IfMatchOptions | undefined): void {
  assertKnownOptions('IfMatchOptions', options, IF_MATCH_KEYS)
  if (typeof options?.ifMatch !== 'string' || options.ifMatch.length === 0) {
    throw badRequest('ifMatch must be a non-empty string')
  }
}

/**
 * Whether `value` is a plain object (prototype `Object.prototype` or `null`)
 * whose enumerable keys are all its own and whose values are all strings. A
 * `Map`, a class instance or inherited keys would otherwise be dropped silently.
 * @internal
 */
export function isPlainStringRecord(value: unknown): value is Readonly<Record<string, string>> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const prototype: unknown = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) return false
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key) || typeof Reflect.get(value, key) !== 'string') return false
  }
  return true
}

/** @internal */
export function assertLabels(labels: unknown): asserts labels is Readonly<Record<string, string>> {
  if (!isPlainStringRecord(labels)) throw badRequest('labels must be a plain object of string to string')
}

/**
 * Every enumerable key of `options`, own or inherited (`for...in`), so an
 * unknown key on a prototype is reported like an own one.
 */
export function enumerableKeys<T>(options: NonNullable<T>): string[] {
  const keys: string[] = []
  for (const key in options) keys.push(key)
  return keys
}

/**
 * Throw `BadRequestError` (status 400, code `BAD_REQUEST`, source `SDK`) for
 * the first key `known` does not list. A renamed key names its replacement.
 */
export function assertKnownKeys<T>(typeName: string, keys: Iterable<string>, known: OptionKeys<T>, renamed: Readonly<Record<string, string>> = RENAMED_OPTIONS): void {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(known, key)) continue
    const replacement = renamed[key]
    throw badRequest(replacement !== undefined && Object.prototype.hasOwnProperty.call(known, replacement)
      ? `${typeName}: \`${key}\` was renamed to \`${replacement}\` (seconds)`
      : `${typeName} has no option \`${key}\``)
  }
}

/**
 * Throw `BadRequestError` unless `options` is `undefined` or a plain options
 * object whose enumerable keys (own or inherited) `known` all lists. Anything
 * else (`null`, a boolean, a string, an array, a function: a positional
 * argument from an older signature) names the type and is refused too.
 */
export function assertKnownOptions<T>(typeName: string, options: T | undefined, known: OptionKeys<T>, renamed: Readonly<Record<string, string>> = RENAMED_OPTIONS): void {
  if (options === undefined) return
  assertOptionsObject(typeName, options)
  assertKnownKeys(typeName, enumerableKeys(options), known, renamed)
}

/**
 * Throw `BadRequestError` unless `options` is an options object: an object
 * literal, a class instance or a prototype-based object. `null`, primitives,
 * functions, arrays and other built-ins (`Date`, `Map`, `Set`, `RegExp`, a
 * typed array, ...: their contents are not enumerable keys, so they would
 * read as `{}`) are refused.
 */
export function assertOptionsObject(typeName: string, options: unknown): asserts options is object {
  if (options === null || typeof options !== 'object' || Array.isArray(options)) {
    const given = options === null ? 'null' : Array.isArray(options) ? 'an array' : `a ${typeof options}`
    throw badRequest(`${typeName} must be an options object, got ${given}`)
  }
  const tag = Object.prototype.toString.call(options).slice(8, -1)
  if (tag !== 'Object') throw badRequest(`${typeName} must be an options object, got a built-in ${tag}`)
}

/**
 * A validated copy of a nested options object: its enumerable keys are
 * checked once, then each known key is read once (through prototypes and
 * getters), so later changes to the caller's object do not reach the copy.
 * `undefined` stays `undefined`; anything but an object is a
 * `BadRequestError`.
 */
export function snapshotOptions<T>(typeName: string, options: T | undefined, known: OptionKeys<T>): T | undefined {
  if (options === undefined) return undefined
  assertOptionsObject(typeName, options)
  assertKnownKeys(typeName, enumerableKeys(options), known)
  const copy: Record<string, unknown> = {}
  for (const key of Object.keys(known)) {
    const value: unknown = Reflect.get(options, key)
    if (value !== undefined) copy[key] = value
  }
  return Object.freeze(copy) as T
}

/**
 * A duration option in seconds: `undefined` stays unset; anything but a
 * finite, non-negative number is a `BadRequestError` naming the option.
 */
export function optionalSeconds(name: string, value: number | undefined): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw badRequest(`${name} must be a non-negative number of seconds`)
  }
  return value
}

/**
 * The largest delay a JavaScript timer can arm: a larger one fires after
 * about 1 ms instead (Node clamps it, browsers overflow).
 */
export const MAX_TIMER_DELAY_MS = 2_147_483_647

/**
 * A request-timeout budget in milliseconds as a timer delay: `0` (and any
 * value above {@link MAX_TIMER_DELAY_MS}, about 24.8 days) means no bound.
 */
export function boundedTimerDelay(timeoutMs: number): number {
  return Number.isFinite(timeoutMs) && timeoutMs > 0 && timeoutMs <= MAX_TIMER_DELAY_MS ? timeoutMs : 0
}
