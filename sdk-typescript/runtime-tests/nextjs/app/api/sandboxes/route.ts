/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as sdk from '@daytona/sdk'
import { runChecks } from '../../../runtime-checks.mjs'

export const dynamic = 'force-dynamic'

export async function GET(): Promise<Response> {
  return Response.json(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY }))
}
