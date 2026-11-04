/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked by the tarball smoke: a CommonJS consumer (module node16) must
// resolve CJS declarations for every subpath through `require`.
import sdk = require('@daytona/sdk')
import v2 = require('@daytona/sdk/v2')
import v0 = require('@daytona/sdk/v0')

const client: sdk.Daytona = new sdk.Daytona({ apiKey: 'smoke' })
const v2Client: sdk.DaytonaV2Client = client.v2
const error: sdk.DaytonaError = new v2.NotFoundError('x')
type NoV0Client = 'Daytona' extends keyof typeof v0 ? never : true
const noV0Client: NoV0Client = true
type NoV2Client = [Extract<'Daytona' | 'DaytonaV2' | 'DaytonaConfig', keyof typeof v2>] extends [never] ? true : never
const noV2Client: NoV2Client = true
export const checks = [client, v2Client, error, typeof v0.DaytonaNotFoundError, noV0Client, noV2Client]
