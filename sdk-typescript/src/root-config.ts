/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { DaytonaConfig } from './types.js'
import { assertKnownKeys, assertOptionsObject, badRequest, enumerableKeys, optionKeys, snapshotOptions } from './v2/options.js'
import { SANDBOX_EVENTS_CONFIG_KEYS } from './v2/types.js'
import type { SandboxEventsConfig } from './v2/types.js'

export const V0_API_URL = 'https://app.daytona.io/api'

/**
 * `DAYTONA_V0_API_URL` (dev / non-prod only; blank = unset), else V0_API_URL.
 * Computed-key `globalThis.process` lookup (bundler-safe), read when the v0
 * client is built and passed as `apiUrl`, so the 0.x SDK's own
 * DAYTONA_API_URL / .env lookup never wins. v0 takes its toolbox / proxy URLs
 * from that API's responses, not from the env.
 */
export function v0ApiUrl(): string {
  const name = 'DAYTONA_V0_API_URL'
  const value = (globalThis as { readonly process?: { readonly env?: Readonly<Record<string, string | undefined>> } })
    .process?.env?.[name]
  const trimmed = typeof value === 'string' ? value.trim().replace(/\/+$/, '') : ''
  return trimmed !== '' ? trimmed : V0_API_URL
}

/**
 * Where each root-config field goes. Exhaustive over `DaytonaConfig`, so a new
 * field does not compile until it is routed (and the parity test checks the
 * v0 side). Mirrors Python's `_client.ROOT_CONFIG_ROUTING`.
 */
export const ROOT_CONFIG_ROUTING: { readonly [TKey in keyof Required<DaytonaConfig>]: 'v0' | 'v2' | 'both' } = {
  apiKey: 'both',
  jwtToken: 'both',
  organizationId: 'v0',
  target: 'v0',
  projectId: 'v2',
  requestTimeoutSeconds: 'v2',
  otelEnabled: 'both',
  otelApi: 'v2',
  connectionPoolMaxsize: 'v2',
  sharedHub: 'v2',
  events: 'v2',
}

/** The same text as the Python SDK's DeprecationWarning (parity). */
export const TARGET_DEPRECATION =
  'DaytonaConfig.target is deprecated and applies only to the v0 namespace (Daytona().v0). ' +
  'Pass target per v0 sandbox create (d.v0.create({ target })) or ' +
  'regionId per v0 snapshot create (d.v0.snapshot.create({ regionId })) instead.'

// Process-global (like the error registries), so the ESM and CJS copies of
// the root share one latch.
const TARGET_DEPRECATION_WARNED = Symbol.for('@daytona/sdk/target-deprecation-warned')
const warnedHolder = globalThis as unknown as Record<symbol, boolean | undefined>

/** Once per process, like Python's default DeprecationWarning filter per call site. */
export function warnTargetDeprecated(): void {
  if (warnedHolder[TARGET_DEPRECATION_WARNED] === true) return
  warnedHolder[TARGET_DEPRECATION_WARNED] = true
  const runtime = globalThis as {
    readonly process?: { readonly emitWarning?: (message: string, options: { type: string; code: string }) => void }
  }
  if (typeof runtime.process?.emitWarning === 'function') {
    runtime.process.emitWarning(TARGET_DEPRECATION, { type: 'DeprecationWarning', code: 'DAYTONA_CONFIG_TARGET' })
  } else {
    console.warn(`DeprecationWarning: ${TARGET_DEPRECATION}`)
  }
}

/** Test seam: re-arm the once-per-process warning. */
export function resetTargetDeprecationWarningForTests(): void {
  warnedHolder[TARGET_DEPRECATION_WARNED] = false
}

/** The known keys of the root `DaytonaConfig`. */
export const ROOT_CONFIG_KEYS = optionKeys<DaytonaConfig>()({
  apiKey: true,
  jwtToken: true,
  organizationId: true,
  target: true,
  projectId: true,
  requestTimeoutSeconds: true,
  otelEnabled: true,
  otelApi: true,
  connectionPoolMaxsize: true,
  sharedHub: true,
  events: true,
})

/** The same text as the Python SDK's (parity). */
const CUSTOM_API_URL = 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.'
const REMOVED_URL_FIELDS = ['apiUrl', 'serverUrl', 'toolboxUrl'] as const

function refuseUrlFields(config: DaytonaConfig, fields: Record<string, unknown> = {}): void {
  // A variable bypasses excess-property checking; reject URLs instead of
  // silently sending a custom-endpoint credential to Daytona Cloud. Seen
  // through `in` (prototypes too) and through a Proxy get trap.
  for (const removed of REMOVED_URL_FIELDS) {
    if (removed in fields || removed in config || Reflect.get(config, removed) !== undefined) {
      throw badRequest(`DaytonaConfig does not take '${removed}': ${CUSTOM_API_URL}`)
    }
  }
}

/**
 * The root config, copied once and validated: what `new Daytona(config)` keeps.
 * Every enumerable key (own or inherited) must be a known one; each known key
 * is read exactly once, through prototypes and Proxy get traps, and the nested
 * `events` object is copied the same way, so later changes to the caller's
 * objects never reach the client. Invalid configuration is a `BadRequestError`.
 */
export function snapshotConfig(config: DaytonaConfig): DaytonaConfig {
  assertOptionsObject('DaytonaConfig', config)
  refuseUrlFields(config)
  const keys = enumerableKeys(config)
  const fields: Record<string, unknown> = {}
  for (const key of Object.keys(ROOT_CONFIG_ROUTING)) {
    const value: unknown = Reflect.get(config, key)
    if (value !== undefined) fields[key] = value
  }
  // A getter may add a URL while the config is read; check again.
  refuseUrlFields(config, fields)
  assertKnownKeys('DaytonaConfig', keys, ROOT_CONFIG_KEYS)
  const events = snapshotOptions('SandboxEventsConfig', fields.events as SandboxEventsConfig | undefined, SANDBOX_EVENTS_CONFIG_KEYS)
  if (events !== undefined) fields.events = events
  return fields
}
