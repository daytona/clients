/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../../src/index'
import type { DaytonaConfig } from '../../src/index'

export function clientAt(url: string, config: DaytonaConfig = {}): Daytona {
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = url
  try {
    const client = new Daytona({ apiKey: 'test-key', events: { enabled: false }, ...config })
    void client.v2
    return client
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}
