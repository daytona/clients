/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// remix-serve installs Remix's fetch polyfill (@remix-run/web-fetch) as the
// global fetch, so the SDK's fetch transport runs on it here.
import { json } from '@remix-run/node'
import * as sdk from '@daytona/sdk'
import { runChecks } from '../../runtime-checks.mjs'

export async function loader() {
  return json(await runChecks(sdk, { apiKey: process.env.DAYTONA_API_KEY }))
}
