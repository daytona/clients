/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as sdk from '@daytona/sdk'
import { runChecks } from '../runtime-checks.mjs'

type Env = { readonly DAYTONA_API_KEY: string }

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (new URL(request.url).pathname !== '/checks') return new Response('ok')
    return Response.json(await runChecks(sdk, { apiKey: env.DAYTONA_API_KEY }))
  },
}
