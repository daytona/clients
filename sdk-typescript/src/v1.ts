/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `@daytona/sdk/v1` is a versioned alias of the current client API, for code that wants to pin
 * the API version explicitly. It re-exports everything exported by `@daytona/sdk`, so the
 * following imports refer to the same values:
 *
 * ```ts
 * import { Daytona, Sandbox } from '@daytona/sdk'
 * import { Daytona, Sandbox } from '@daytona/sdk/v1'
 * ```
 */
export * from './index'
