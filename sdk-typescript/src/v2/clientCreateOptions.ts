/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError } from './errors/DaytonaError.js'
import { badRequest } from './options.js'
import { gibToMib } from './resources.js'
export { gibToMib } from './resources.js'
import { Image, isImageBuilder } from './Image.js'
import type { CreateSandboxOptions } from './types.js'

/** @internal */
export function hasText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

/** @internal */
export function imageSourceString(image: unknown): string {
  if (isImageBuilder(image)) {
    throw new TypeError('daytona.create() only accepts an image name or img_… id; use daytona.createFromBuild(builder, options) for ImageBuilder sources')
  }
  if (!hasText(image)) {
    throw new TypeError('daytona.create() only accepts an image name or img_… id')
  }
  return image.trim()
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
  if (imageOrOptions === undefined || imageOrOptions === null) return { source: undefined, options: options ?? {} }
  if (typeof imageOrOptions === 'object' && !isImageBuilder(imageOrOptions) && !(imageOrOptions instanceof Image) && !Array.isArray(imageOrOptions)) {
    if (options !== undefined) throw new TypeError('daytona.create() takes options once: create(options) or create(image, options)')
    if (Object.prototype.hasOwnProperty.call(imageOrOptions, 'image') || Object.prototype.hasOwnProperty.call(imageOrOptions, 'snapshot')) {
      throw new TypeError('daytona.create() takes the image as its first argument: create(image, options), not create({ image })')
    }
    return { source: undefined, options: imageOrOptions }
  }
  return { source: imageSourceString(imageOrOptions), options: options ?? {} }
}

/** @internal */
export function snapshotSourceString(snapshot: string): string {
  if (!hasText(snapshot)) throw new TypeError('snapshot must be a non-empty snapshot name or snap_… id')
  return snapshot.trim()
}

/** @internal */
export function createWaitSeconds(waitSeconds: number | undefined): number {
  const resolved = waitSeconds ?? 60
  if (!Number.isFinite(resolved) || resolved < 0 || !Number.isInteger(resolved)) {
    throw new DaytonaError('waitSeconds must be a non-negative integer number of seconds', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return resolved
}

/** @internal */
export function createTimeoutSeconds(createTimeoutSeconds: number | undefined): number | undefined {
  if (createTimeoutSeconds === undefined) return undefined
  if (!Number.isFinite(createTimeoutSeconds) || !Number.isInteger(createTimeoutSeconds)) {
    throw new DaytonaError('createTimeoutSeconds must be an integer number of seconds', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return createTimeoutSeconds
}

const MAX_INT32 = 2147483647

// Omitted macOS sizes use the API defaults. Supplied sizes must fit the
// supported range; GiB converts to MiB exactly, never rounded.
/** @internal */
export function macosCores(cpu: number | undefined): number | undefined {
  if (cpu === undefined) return undefined
  if (typeof cpu !== 'number' || !Number.isInteger(cpu) || cpu < 4 || cpu > MAX_INT32) {
    throw new DaytonaError('cpu must be an integer between 4 and 2147483647', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return cpu
}

/** @internal */
export function macosMib(gib: number | undefined, axis: 'memory' | 'disk', floorMib: number): number | undefined {
  if (gib === undefined) return undefined
  const mib = gibToMib(gib, axis)
  if (mib < floorMib || mib > MAX_INT32) {
    throw new DaytonaError(`${axis} must be a finite GiB value that converts to an integer MiB value between ${floorMib} and 2147483647`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
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
  if (typeof idempotencyKey !== 'string') throw new DaytonaError('idempotencyKey must be a string', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  return idempotencyKey
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
  const resolved = options.region
  return resolved && resolved.trim().toLowerCase() !== 'local' ? resolved : undefined
}
