/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Deno resolves the package from node_modules (installed by npm), the way a
// Deno project with a package.json does.
import * as sdk from '@daytona/sdk'
import { runChecks } from './runtime-checks.mjs'

console.log(JSON.stringify(await runChecks(sdk, { apiKey: Deno.env.get('DAYTONA_API_KEY') })))
