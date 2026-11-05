/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Compiled to CommonJS by tsc (the Functions Node.js default), so the host
// loads the SDK's CommonJS build.
import { app, type HttpResponseInit } from '@azure/functions'
import * as sdk from '@daytona/sdk'

app.http('sandboxes', {
  methods: ['GET'],
  authLevel: 'anonymous',
  handler: async (): Promise<HttpResponseInit> => {
    const { runChecks } = await import('./runtime-checks.mjs')
    return { jsonBody: await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY }) }
  },
})
