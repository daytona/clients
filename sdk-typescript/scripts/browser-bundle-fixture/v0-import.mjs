/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled by the tarball smoke for the browser: a direct client import
// must fail the build (the v0 namespace does not export `Daytona`).
import { Daytona } from '@daytona/sdk/v0'

export const sdk = Daytona
