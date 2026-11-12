/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The ESM side of the same check.
import { Daytona } from '@daytona/sdk'
import * as v2 from '@daytona/sdk/v2'
import { NotFoundError } from '@daytona/sdk/v2'
import * as v0 from '@daytona/sdk/v0'

type NoV0Client = 'Daytona' extends keyof typeof v0 ? never : true
const noV0Client: NoV0Client = true
type NoV2Client = [Extract<'Daytona' | 'DaytonaV2', keyof typeof v2>] extends [never] ? true : never
const noV2Client: NoV2Client = true

export const checks = [new Daytona({ apiKey: 'smoke' }).v2, new NotFoundError('x'), typeof v0.DaytonaNotFoundError, noV0Client, noV2Client]

export async function v0Overloads(): Promise<unknown[]> {
  const d = new Daytona({ apiKey: 'smoke' })
  return [await d.v0.create({ snapshot: 'base' }), await d.v0.snapshot.list({ page: 1, limit: 10 })]
}
