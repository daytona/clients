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
 * behaviour. Every public options type therefore declares its known keys once:
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

/**
 * Throw `BadRequestError` (status 400, code `BAD_REQUEST`, source `SDK`) when
 * `options` has an own key `known` does not list. A renamed key names its
 * replacement. `undefined` / `null` options pass (callers default them).
 */
export function assertKnownOptions<T>(typeName: string, options: T | null | undefined, known: OptionKeys<T>): void {
  if (options === undefined || options === null || typeof options !== 'object') return
  for (const key of Object.keys(options)) {
    if (Object.prototype.hasOwnProperty.call(known, key)) continue
    const renamed = RENAMED_OPTIONS[key]
    const message = renamed !== undefined && Object.prototype.hasOwnProperty.call(known, renamed)
      ? `${typeName}: \`${key}\` was renamed to \`${renamed}\` (seconds)`
      : `${typeName} has no option \`${key}\``
    throw new BadRequestError(message, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

/**
 * A duration option in seconds: `undefined` stays unset; anything but a
 * finite, non-negative number is a `BadRequestError` naming the option.
 */
export function optionalSeconds(name: string, value: number | undefined): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new BadRequestError(`${name} must be a non-negative number of seconds`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value
}
