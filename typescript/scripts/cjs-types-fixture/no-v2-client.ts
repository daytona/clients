/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked by the tarball smoke as an EXPECTED FAILURE: `@daytona/sdk/v2`
// exports no client, not even as a type. `keyof typeof v2` cannot see
// type-only exports, so this file imports each name; tsc must report TS2305 for
// all three, and the smoke fails if any of them resolves.
import type { Daytona } from '@daytona/sdk/v2'
import type { DaytonaV2 } from '@daytona/sdk/v2'
import type { DaytonaConfig } from '@daytona/sdk/v2'

export type Removed = [Daytona, DaytonaV2, DaytonaConfig]
