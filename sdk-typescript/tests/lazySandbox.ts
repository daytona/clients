/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Test-only metadata-less sandbox handles. Sandboxes come from create/get/list.
// Unit tests need a handle bound to a stub toolbox without an API round trip, so
// this calls the client's private factory — the one create() uses.
import type { Daytona } from '../src/v2/Daytona'
import type { Sandbox } from '../src/v2/Sandbox'
import type { SandboxConnectOptions, SandboxHandleJSON } from '../src/v2/types'

type SandboxFactory = { buildSandbox(sandboxOrId: string | SandboxHandleJSON, options?: SandboxConnectOptions): Sandbox }

export function lazySandbox(client: Daytona, idOrInfo: string | SandboxHandleJSON, options?: SandboxConnectOptions): Sandbox {
  return (client as unknown as SandboxFactory).buildSandbox(idOrInfo, options)
}
