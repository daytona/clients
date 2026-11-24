// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona } from '@daytona/sdk'

export const dynamic = 'force-dynamic'

// otelEnabled loads the OpenTelemetry packages at call time.
export async function GET() {
  try {
    const daytona = new Daytona({ apiKey: 'stub', apiUrl: 'http://127.0.0.1:9/api', otelEnabled: true })
    await daytona[Symbol.asyncDispose]()
    return Response.json({ otelOk: true })
  } catch (e) {
    return Response.json({ otelOk: false, error: e instanceof Error ? e.message : String(e) })
  }
}
