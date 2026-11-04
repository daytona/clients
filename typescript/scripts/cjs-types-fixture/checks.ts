/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked against the packed tarball under node10 (`module: commonjs`, no
// moduleResolution) and bundler resolution; esm.mts / index.cts cover node16.
import { Daytona, DaytonaError, DaytonaNotFoundError, type DaytonaV0Client, type DaytonaV2Client } from '@daytona/sdk'
import * as v2 from '@daytona/sdk/v2'
import { NotFoundError, type Sandbox } from '@daytona/sdk/v2'
import * as v0 from '@daytona/sdk/v0'

// The v0 namespace does not export its client classes: SDK 2.x builds
// a v0 client only through the root.
type NoV0Client = [Extract<'Daytona' | 'DaytonaConfig', keyof typeof v0>] extends [never] ? true : never
const noV0Client: NoV0Client = true
// Nor does the v2 namespace; the root's DaytonaV2Client types a stored client.
type NoV2Client = [Extract<'Daytona' | 'DaytonaV2' | 'DaytonaConfig', keyof typeof v2>] extends [never] ? true : never
const noV2Client: NoV2Client = true

export async function checks(): Promise<unknown[]> {
  const d = new Daytona({ apiKey: 'smoke' })
  const v2Client: DaytonaV2Client = d.v2
  const sandbox: Sandbox = await v2Client.create('python:3.12')
  // The root's DaytonaV0Client types stored 0.x client and service references.
  const client: DaytonaV0Client = d.v0
  const volumes: DaytonaV0Client['volume'] = d.v0.volume
  // d.v0 keeps v0's overloads.
  const fromSnapshot = await d.v0.create({ snapshot: 'base' })
  const withTimeout = await d.v0.create({ snapshot: 'base' }, { timeout: 60 })
  const fromImage = await d.v0.create({ image: 'python:3.12' }, { onSnapshotCreateLogs: () => undefined })
  const bare = await d.v0.create()
  const listed: unknown[] = []
  for await (const s of d.v0.list()) listed.push(s)
  const got = await d.v0.get('id')
  const page = await d.v0.snapshot.list({ page: 1, limit: 10 })
  const positional = await d.v0.snapshot.list(1, 10)
  // The shared root errors type the fields both namespaces set.
  try {
    await d.v2.get('missing')
  } catch (e) {
    if (e instanceof DaytonaError) console.log(e.statusCode, e.code, e.source)
    if (e instanceof DaytonaNotFoundError) console.log(e.statusCode)
  }
  return [client, volumes, sandbox, fromSnapshot, withTimeout, fromImage, bare, listed, got, page, positional, new NotFoundError('x'), typeof v0.DaytonaNotFoundError, noV0Client, noV2Client]
}
