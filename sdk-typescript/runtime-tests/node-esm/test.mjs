/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// import must resolve the ES module build.
import * as sdk from '@daytona/sdk'
import { runChecks } from './runtime-checks.mjs'

const entry = import.meta.resolve('@daytona/sdk')
if (!/\/dist\/esm\/index\.js$/.test(entry)) throw new Error(`import resolved ${entry}, not the ES module build`)
console.log(JSON.stringify(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY })))
