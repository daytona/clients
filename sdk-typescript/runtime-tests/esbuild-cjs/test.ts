/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The ES module build re-bundled into one CommonJS file by the application's
// bundler (the shape of a bundled CLI or serverless deploy). run.sh runs the
// bundle from a directory with no node_modules on its resolution path.
import * as sdk from '@daytona/sdk'
import { runChecks } from './runtime-checks.mjs'

runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY }).then(
  (result) => console.log(JSON.stringify(result)),
  (error: unknown) => {
    console.error(error)
    process.exit(1)
  },
)
