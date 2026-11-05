/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled with esbuild into one CommonJS file, the deployment shape of a
// Node.js Lambda function.
import * as sdk from '@daytona/sdk'
import { runChecks } from './runtime-checks.mjs'

export const handler = async () => ({
  statusCode: 200,
  body: JSON.stringify(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY })),
})
