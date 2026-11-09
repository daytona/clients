/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, nonOptionsDescription } from './errors/DaytonaError.js'

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
 * Options renamed when every SDK duration moved to seconds, and the 0.x
 * client configuration's `requestTimeoutMs`. An old name that is not a known
 * key of the options type is reported with its replacement.
 */
export const RENAMED_OPTIONS: Readonly<Record<string, string>> = Object.freeze({
  timeout: 'timeoutSeconds',
  executionTimeout: 'executionTimeoutSeconds',
  escalateAfter: 'escalateAfterSeconds',
  requestTimeout: 'requestTimeoutSeconds',
  requestTimeoutMs: 'requestTimeoutSeconds',
  reconcileIntervalMs: 'reconcileIntervalSeconds',
  idleCloseMs: 'idleCloseSeconds',
})

/** The SDK's own invalid-input error: status 400, code `BAD_REQUEST`, source `SDK`. */
export function badRequest(message: string): BadRequestError {
  return new BadRequestError(message, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

/**
 * A caller string sent as a header value: non-empty printable ASCII with no
 * leading or trailing whitespace, else `BadRequestError` naming `name`, before
 * any request. It is never stripped: an idempotency key or an ETag the SDK
 * altered would not be the caller's value. Python applies the same rule
 * (`header_text`), so both SDKs refuse the same values: neither sends header
 * bytes the other would not, and neither fails with a transport error (Python's
 * HTTP client refuses padded header values) for one. `region` goes through
 * `createRegion`, which first takes a blank value or `local` as the default
 * region (not sent).
 * @internal
 */
export function headerText(value: string, name: string): string {
  if (value.length === 0) throw badRequest(`${name} must be a non-empty string`)
  for (let index = 0; index < value.length; index++) {
    const unit = value.charCodeAt(index)
    if (unit < 0x20 || unit > 0x7e) throw badRequest(`${name} must be printable ASCII with no leading or trailing whitespace`)
  }
  if (value.startsWith(' ') || value.endsWith(' ')) throw badRequest(`${name} must be printable ASCII with no leading or trailing whitespace`)
  return value
}

export interface IfMatchOptions {
  readonly ifMatch: string
}

const IF_MATCH_KEYS = optionKeys<IfMatchOptions>()({ ifMatch: true })

/** @internal */
export function assertIfMatchOptions(options: IfMatchOptions | undefined): void {
  assertKnownOptions('IfMatchOptions', options, IF_MATCH_KEYS)
  if (typeof options?.ifMatch !== 'string' || options.ifMatch.length === 0) {
    throw badRequest('ifMatch must be a non-empty string (the etag of the last read)')
  }
  // It is sent as the If-Match header.
  headerText(options.ifMatch, 'ifMatch')
}

/**
 * Whether `value` is a plain object (prototype `Object.prototype` or `null`)
 * whose enumerable keys are all its own and whose values are all strings. A
 * `Map`, a class instance or inherited keys would otherwise be dropped silently.
 * @internal
 */
export function isPlainStringRecord(value: unknown): value is Readonly<Record<string, string>> {
  if (!isPlainRecord(value)) return false
  for (const key in value) {
    if (typeof Reflect.get(value, key) !== 'string') return false
  }
  return true
}

/**
 * Whether `value` is a plain object (prototype `Object.prototype` or `null`)
 * whose enumerable keys are all its own: JavaScript's counterpart of a Python
 * mapping argument. A `Map`, a `Date`, a class instance or inherited keys
 * would otherwise be read as empty.
 * @internal
 */
export function isPlainRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const prototype: unknown = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) return false
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) return false
  }
  return true
}

/** @internal */
export function assertLabels(labels: unknown): asserts labels is Readonly<Record<string, string>> {
  if (!isPlainStringRecord(labels)) throw badRequest('labels must be a plain object of string to string')
}

/**
 * Every enumerable string key of `options`, own or inherited (`for...in`), so
 * an unknown key on a prototype is reported like an own one. No options type
 * declares a symbol key: an enumerable symbol key, own or inherited, is a
 * `BadRequestError` naming `typeName`.
 */
export function enumerableKeys<T>(typeName: string, options: NonNullable<T>): string[] {
  for (let level: object | null = options; level !== null && level !== Object.prototype; level = Object.getPrototypeOf(level) as object | null) {
    for (const symbol of Object.getOwnPropertySymbols(level)) {
      if (Object.prototype.propertyIsEnumerable.call(level, symbol)) throw badRequest(`${typeName} has no option \`${String(symbol)}\``)
    }
  }
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
  assertKnownKeys(typeName, enumerableKeys(typeName, options), known, renamed)
}

/**
 * Throw `BadRequestError` unless `options` is an options object: an object
 * literal, a class instance or a prototype-based object. `null`, primitives,
 * functions, arrays and other built-ins (`Date`, `Map`, `Set`, `RegExp`, a
 * typed array, ...: their contents are not enumerable keys, so they would
 * read as `{}`) are refused.
 */
export function assertOptionsObject(typeName: string, options: unknown): asserts options is object {
  const given = nonOptionsDescription(options)
  if (given !== undefined) throw badRequest(`${typeName} must be an options object, got ${given}`)
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
  assertKnownKeys(typeName, enumerableKeys(typeName, options), known)
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
 * The deadline of a wait budget in seconds, the same rule as Python's
 * `wait_budget_seconds` for every wait of the client, lifecycle and platform
 * services: `undefined` (no deadline) for an omitted budget, `0`, or one above
 * the timer maximum (about 24.8 days); otherwise `now + seconds`.
 * @internal
 */
export function waitDeadline(seconds: number | undefined, now: number = Date.now()): number | undefined {
  if (seconds === undefined || seconds === 0 || seconds * 1000 > MAX_TIMER_DELAY_MS) return undefined
  return now + seconds * 1000
}

/**
 * A request-timeout budget in milliseconds as a timer delay: `0` (and any
 * value above {@link MAX_TIMER_DELAY_MS}, about 24.8 days) means no bound.
 */
export function boundedTimerDelay(timeoutMs: number): number {
  return Number.isFinite(timeoutMs) && timeoutMs > 0 && timeoutMs <= MAX_TIMER_DELAY_MS ? timeoutMs : 0
}
