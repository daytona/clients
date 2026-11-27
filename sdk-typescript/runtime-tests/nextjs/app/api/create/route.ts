// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona, Image } from '@daytona/sdk'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const dynamic = 'force-dynamic'

// Creating a sandbox from a Dockerfile uploads the COPY context, which loads the SDK's
// object storage module, tar and the AWS SDK at call time. Runs against stub-api.js.
export async function GET() {
  const stubUrl = process.env.DAYTONA_STUB_API_URL
  if (!stubUrl) return Response.json({ createOk: false, error: 'DAYTONA_STUB_API_URL is not set' })

  // Reading /uploads drains it, so this request only sees the uploads it makes itself.
  await fetch(`${stubUrl}/uploads`)

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-nextjs-create-'))
  let error = 'create did not fail at the stub'
  try {
    fs.mkdirSync(path.join(dir, 'src'))
    fs.writeFileSync(path.join(dir, 'src', 'a.txt'), `${process.pid}-${Date.now()}-${Math.random()}`)
    fs.writeFileSync(path.join(dir, 'Dockerfile'), 'FROM alpine\nCOPY src/*.txt /app/\n')
    const daytona = new Daytona({ apiKey: 'stub', apiUrl: `${stubUrl}/api` })
    try {
      await daytona.create({ image: Image.fromDockerfile(path.join(dir, 'Dockerfile')) }, { timeout: 30 })
    } finally {
      await daytona[Symbol.asyncDispose]()
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
  const uploads: { key: string; bytes: number }[] = await (await fetch(`${stubUrl}/uploads`)).json()
  return Response.json({
    createOk:
      error.includes('stub: create reached') && uploads.some((u) => u.key.endsWith('context.tar') && u.bytes > 0),
    error,
    uploads,
  })
}
