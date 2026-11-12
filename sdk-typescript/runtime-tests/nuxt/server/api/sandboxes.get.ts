/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as sdk from '@daytona/sdk'
import { runChecks } from '../../runtime-checks.mjs'

export default defineEventHandler(async () => runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY }))
