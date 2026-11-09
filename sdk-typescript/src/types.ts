/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { OtelApiLike } from './v2/http.js'
import type { SandboxEventsConfig } from './v2/types.js'

/**
 * Configuration shared by both namespaces. Routing per field (also
 * `ROOT_CONFIG_ROUTING`): `apiKey`, `jwtToken`, `otelEnabled` reach v0 and v2;
 * `organizationId` and the deprecated `target` are v0-only; `projectId`,
 * `requestTimeoutSeconds`, `maxResponseBytes`, `otelApi`,
 * `connectionPoolMaxsize`, `sharedHub` and `events` are v2-only.
 *
 * `new Daytona(config)` copies the fields: changing the object afterwards has
 * no effect on the client. An unknown field is a `BadRequestError`, and a
 * renamed one names its replacement (`requestTimeout` → `requestTimeoutSeconds`).
 * There is no API URL field: `DAYTONA_V2_API_URL` (a regional endpoint or a test environment)
 * selects the v2 endpoint; `DAYTONA_API_URL` belongs to the 0.x SDK and SDK 2.x
 * never reads it.
 */
export type DaytonaConfig = {
  readonly apiKey?: string
  readonly jwtToken?: string
  /** v0 only: scopes a JWT to an organization. */
  readonly organizationId?: string
  /**
   * The v0 client's default region (`v2` takes a per-create `region`).
   * @deprecated Applies only to the v0 namespace. Pass `target` per v0 sandbox
   * create (`d.v0.create({ target })`) or `regionId` per v0 snapshot create
   * instead. Setting it still works and emits a runtime deprecation warning.
   */
  readonly target?: string
  /** v2 only: scopes a JWT to a project. */
  readonly projectId?: string
  /**
   * v2 only: the total time one ordinary request may take, in seconds
   * (default 60; `0` = no limit). Waits and long polls use their own budget,
   * streams are bounded only until their response headers arrive, and file
   * transfers have no total bound but fail once no bytes move for this long
   * (with `0`, transfers are not bounded at all).
   */
  readonly requestTimeoutSeconds?: number
  /**
   * v2 only: the cap on a response body the SDK holds in memory whole, in
   * bytes (an integer, `0` = no limit; default 1 GiB). See the v2
   * `DaytonaConfig.maxResponseBytes`.
   */
  readonly maxResponseBytes?: number | null
  /** Both namespaces. */
  readonly otelEnabled?: boolean
  /** v2 only. */
  readonly otelApi?: OtelApiLike
  /** v2 only (v0 at the pinned release has no pool-size setting). */
  readonly connectionPoolMaxsize?: number
  /** v2 only: share the sandbox-state events hub with matching clients (default `true`). */
  readonly sharedHub?: boolean
  /** v2 only: sandbox-state events hub settings (`SandboxEventsConfig` from `@daytona/sdk/v2`). */
  readonly events?: SandboxEventsConfig
}
