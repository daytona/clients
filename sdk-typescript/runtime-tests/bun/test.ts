/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as sdk from '@daytona/sdk'
import { runChecks } from './runtime-checks.mjs'

console.log(JSON.stringify(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY })))
