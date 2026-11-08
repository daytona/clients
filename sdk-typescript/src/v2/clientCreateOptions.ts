/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest, headerText } from './options.js'
import { selectRegion } from './region.js'
import { gibToMib } from './resources.js'
export { gibToMib } from './resources.js'
import { Image, isImageBuilder } from './Image.js'
import type { CreateSandboxOptions } from './types.js'
import { isBlank, strip } from './pyText.js'

/** @internal */
export function hasText(value: unknown): value is string {
  return typeof value === 'string' && !isBlank(value)
}

/** @internal */
export function imageSourceString(image: unknown, method: 'create' | 'createMacos' = 'create'): string {
  if (isImageBuilder(image)) {
    throw badRequest(method === 'createMacos'
      ? 'daytona.createMacos() only accepts an image name or img_… id; macOS sandboxes do not build images'
      : 'daytona.create() only accepts an image name or img_… id; use daytona.createFromBuild(builder, options) for ImageBuilder sources')
  }
  // A blank string has its own message, as in Python; any other non-string is the wrong type.
  if (typeof image === 'string' && isBlank(image)) throw badRequest('image must be a non-empty string')
  if (!hasText(image)) {
    throw badRequest(`daytona.${method}() only accepts an image name or img_… id`)
  }
  return strip(image)
}

interface LinuxCreateArguments {
  readonly source: string | undefined
  readonly options: CreateSandboxOptions
}

/**
 * Split create's overloads; refuse v0-shaped options instead of silently
 * dropping their image or snapshot and booting the default.
 * @internal
 */
export function linuxCreateArguments(imageOrOptions: string | CreateSandboxOptions | null | undefined, options: CreateSandboxOptions | undefined): LinuxCreateArguments {
  // No image (`undefined`, or `null` as Python's `None`) boots the platform
  // default. Only an omitted options argument defaults; a `null` options
  // argument is refused like any non-object.
  if (imageOrOptions === undefined || imageOrOptions === null) return { source: undefined, options: options === undefined ? {} : options }
  if (typeof imageOrOptions === 'object' && !isImageBuilder(imageOrOptions) && !(imageOrOptions instanceof Image) && !Array.isArray(imageOrOptions)) {
    if (options !== undefined) throw badRequest('daytona.create() takes options once: create(options) or create(image, options)')
    if (Object.prototype.hasOwnProperty.call(imageOrOptions, 'image') || Object.prototype.hasOwnProperty.call(imageOrOptions, 'snapshot')) {
      throw badRequest('daytona.create() takes the image as its first argument: create(image, options), not create({ image })')
    }
    return { source: undefined, options: imageOrOptions }
  }
  return { source: imageSourceString(imageOrOptions), options: options === undefined ? {} : options }
}

/** @internal */
export function snapshotSourceString(snapshot: string): string {
  if (typeof snapshot === 'string' && isBlank(snapshot)) throw badRequest('snapshot must be a non-empty string')
  if (!hasText(snapshot)) throw badRequest('daytona.createFromSnapshot() only accepts a snapshot name or snap_… id')
  return strip(snapshot)
}

/** @internal */
export function createWaitSeconds(waitSeconds: number | undefined): number {
  // Only an omitted value takes the default; `null` is refused, as Python refuses `None`.
  const resolved: unknown = waitSeconds === undefined ? 60 : waitSeconds
  if (typeof resolved !== 'number' || !Number.isFinite(resolved) || resolved < 0 || !Number.isInteger(resolved)) {
    throw badRequest('waitSeconds must be a non-negative integer number of seconds')
  }
  return resolved
}

/**
 * The longest wait a request body carries, in whole seconds: the timer
 * maximum. A wait above it means no limit; both SDKs send this value then.
 * @internal
 */
export const MAX_WIRE_WAIT_SECONDS = 2_147_483

/** @internal */
export function wireWaitSeconds(waitSeconds: number): number {
  return Math.min(waitSeconds, MAX_WIRE_WAIT_SECONDS)
}

/** @internal */
export function createTimeoutSeconds(createTimeoutSeconds: number | undefined): number | undefined {
  // `null` is Python's `None`: the server's default.
  if (createTimeoutSeconds === undefined || createTimeoutSeconds === null) return undefined
  // The server's own budget (an int64 on the wire) that both SDKs carry exactly.
  if (!Number.isSafeInteger(createTimeoutSeconds) || createTimeoutSeconds < -1) {
    throw badRequest('createTimeoutSeconds must be an integer from -1 (the server maximum) to 9007199254740991')
  }
  return createTimeoutSeconds
}

const MAX_INT32 = 2147483647

/** A Linux size in GiB as the contract's int32 MiB. @internal */
export function linuxMib(gib: number, field: string): number {
  const mib = gibToMib(gib, field)
  if (mib > MAX_INT32) throw badRequest(`${field} must convert to at most 2147483647 MiB`)
  return mib
}

// Omitted macOS sizes use the API defaults. Supplied sizes must fit the
// supported range; GiB converts to MiB exactly, never rounded.
/** @internal */
export function macosCores(cpu: number | undefined): number | undefined {
  if (cpu === undefined) return undefined
  if (typeof cpu !== 'number' || !Number.isInteger(cpu) || cpu < 4 || cpu > MAX_INT32) {
    throw badRequest('cpu must be an integer between 4 and 2147483647')
  }
  return cpu
}

/** @internal */
export function macosMib(gib: number | undefined, axis: 'memory' | 'disk', floorMib: number): number | undefined {
  if (gib === undefined) return undefined
  // One message for every bad value (not whole, below the floor, too large), as in Python.
  const mib = typeof gib === 'number' ? gib * 1024 : NaN
  if (!Number.isInteger(mib) || mib < floorMib || mib > MAX_INT32) {
    throw badRequest(`${axis} must be a finite GiB value that converts to an integer MiB value between ${floorMib} and 2147483647`)
  }
  return mib
}

/**
 * The caller's key, verbatim, or undefined. Name-derived/random keys are
 * resolved just before submitting the request, after validation.
 * @internal
 */
export function resolveIdempotencyKey(idempotencyKey: string | undefined): string | undefined {
  if (idempotencyKey == null) return undefined
  if (typeof idempotencyKey !== 'string') throw badRequest('idempotencyKey must be a string')
  return headerText(idempotencyKey, 'idempotencyKey')
}

/**
 * Plain-JS callers must not silently drop v0's `target` and select a default
 * region: checked before the option keys, so the error names the replacement.
 * @internal
 */
export function rejectV0Target(options: { readonly region?: string } | undefined): void {
  if (options !== undefined && options !== null && typeof options === 'object' && 'target' in options) {
    throw badRequest("v2 does not take 'target'; pass 'region' instead")
  }
}

/** @internal */
export function createRegion(options: { readonly region?: string }): string | undefined {
  rejectV0Target(options)
  // `null` is Python's `None`: no region. The selection rule is `selectRegion`'s.
  return selectRegion(options.region)
}
