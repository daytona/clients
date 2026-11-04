/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked by the tarball smoke as an EXPECTED FAILURE: `@daytona/sdk/v0`
// exports no client, and the compiler must not suggest the private
// names the root reads ("Did you mean '…'?"), which would build a v0 client
// outside the root.
import { Daytona } from '@daytona/sdk/v0'
import type { DaytonaConfig } from '@daytona/sdk/v0'

export type Removed = [typeof Daytona, DaytonaConfig]
