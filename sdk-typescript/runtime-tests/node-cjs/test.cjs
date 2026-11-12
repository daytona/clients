/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// require() must resolve the CommonJS build.
const sdk = require('@daytona/sdk')

async function main() {
  const entry = require.resolve('@daytona/sdk')
  if (!/[\\/]dist[\\/]cjs[\\/]index\.js$/.test(entry)) throw new Error(`require() resolved ${entry}, not the CommonJS build`)
  const { runChecks } = await import('./runtime-checks.mjs')
  console.log(JSON.stringify(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY })))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
