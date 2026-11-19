/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DesktopEnvironment as DESKTOP_ENVIRONMENTS } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { checkResponse, nested } from './responseFields.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import type * as api from './gen/api-types.js'

/** Desktop environment selected at creation. */
export type DesktopEnvironment = typeof DESKTOP_ENVIRONMENTS[number]

/** Desktop dimensions in pixels. Supported sizes: 1024x768, 1280x720, 1920x1080. */
export interface DesktopResolution {
  readonly width: number
  readonly height: number
}

/**
 * Opt into a desktop on image/default Linux creates, including createFromBuild.
 * An empty object is sent unchanged; the server defaults to XFCE at 1280x720.
 * The server returns 403 FEATURE_NOT_ENABLED when desktop is disabled.
 * It returns 400 BAD_REQUEST when an environment or resolution is invalid,
 * an object has unknown members, or a supplied desktop/environment/resolution/dimension
 * is explicitly null.
 * Boot LIMITS (`resources.cpuMax` / `resources.memoryMax`, or server defaults),
 * not resource guarantees, must be at least 1 CPU / 2048 MiB
 * for XFCE, or 2 CPUs / 8192 MiB for GNOME; lower limits also return 400.
 * Snapshot creates and forks do not accept this option.
 */
export interface DesktopConfig {
  readonly environment?: DesktopEnvironment
  readonly resolution?: DesktopResolution
}

/**
 * Resolved create-time configuration, not live desktop status. Restarting
 * desktop services inside the guest may change the live settings; read
 * `sandbox.computerUse.display.info()` for the live display size. Absent when no
 * desktop was requested; batch-wait snapshots omit it even for enabled desktops.
 */
export interface DesktopSettings {
  readonly environment: DesktopEnvironment | 'unknown'
  readonly rawEnvironment: string
  readonly resolution: DesktopResolution
}

const DESKTOP_KEYS = optionKeys<DesktopConfig>()({ environment: true, resolution: true })
const RESOLUTION_KEYS = optionKeys<DesktopResolution>()({ width: true, height: true })
const DESKTOP_FIELDS = {
  environment: 'string', resolution: nested({ width: 'int32', height: 'int32' }),
} as const

/** @internal */
export function assertDesktopOptions(options: DesktopConfig | undefined): void {
  assertKnownOptions('DesktopConfig', options, DESKTOP_KEYS)
  assertKnownOptions('DesktopResolution', options?.resolution, RESOLUTION_KEYS)
}

/** @internal */
export function checkDesktopResponse(value: unknown): api.DesktopSettings | undefined {
  if (value === undefined || value === null) return undefined
  const desktop = checkResponse<api.DesktopSettings>(value, DESKTOP_FIELDS, 'sandbox desktop')
  for (const axis of ['width', 'height'] as const) {
    if (desktop.resolution[axis] < 0) throw badRequest(`sandbox desktop response has a resolution.${axis} that is negative`)
  }
  return desktop
}

/** @internal */
export function parseDesktop(value: unknown): DesktopSettings | undefined {
  const desktop = checkDesktopResponse(value)
  if (desktop === undefined) return undefined
  return Object.freeze({
    environment: knownOrUnknown(desktop.environment, DESKTOP_ENVIRONMENTS),
    rawEnvironment: desktop.environment,
    resolution: Object.freeze({ width: desktop.resolution.width, height: desktop.resolution.height }),
  })
}
