/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Sandbox } from '../src/v2/index'

/** Waiting preserves a caller's narrowed handle type. */
export function waitForNarrowedHandle<T extends Sandbox>(sandbox: T): Promise<T> {
  return sandbox.wait({ timeoutSeconds: 0.05 })
}
