/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Runs in headless Chromium: a browser bundle of the root, with no Node
// built-ins. test-browser.mjs injects the configuration before this loads.
import * as sdk from '@daytona/sdk'
import { runChecks } from '../runtime-checks.mjs'

declare global {
  interface Window {
    __DAYTONA_API_KEY__?: string
  }
}

try {
  const result = await runChecks(sdk, { apiKey: window.__DAYTONA_API_KEY__ })
  document.body.dataset.result = JSON.stringify(result)
} catch (error) {
  document.body.dataset.result = JSON.stringify({ error: error instanceof Error ? `${error.name}: ${error.message}` : String(error) })
}
