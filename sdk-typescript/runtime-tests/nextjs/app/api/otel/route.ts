// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona } from '@daytona/sdk'

export const dynamic = 'force-dynamic'

// otelEnabled loads the OpenTelemetry packages at call time. The client registers
// process-level OpenTelemetry state, so the route creates it once per server.
let otelClient: { daytona?: Daytona; error?: string } | undefined

export async function GET() {
  if (!otelClient) {
    try {
      otelClient = { daytona: new Daytona({ apiKey: 'stub', apiUrl: 'http://127.0.0.1:9/api', otelEnabled: true }) }
    } catch (e) {
      otelClient = { error: e instanceof Error ? e.message : String(e) }
    }
  }
  return otelClient.daytona
    ? Response.json({ otelOk: true })
    : Response.json({ otelOk: false, error: otelClient.error })
}
