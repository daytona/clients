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
 * `requestTimeout`, `otelApi`, `connectionPoolMaxsize`, `sharedHub` and `events`
 * are v2-only.
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
  /** v2 only. */
  readonly requestTimeout?: number
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
