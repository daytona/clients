/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The CommonJS half of node-only.mts: `require` resolves the CJS declarations
// (dist/cjs), checked the same way (no DOM lib, skipLibCheck: false).
import sdk = require('@daytona/sdk')
import v2 = require('@daytona/sdk/v2')

export const client: sdk.Daytona = new sdk.Daytona({ apiKey: 'smoke' })
export async function sandbox(): Promise<v2.Sandbox> {
  return client.v2.create('python:3.12')
}
export const errors = [sdk.DaytonaNotFoundError, v2.NotFoundError, v2.ConnectionError]
