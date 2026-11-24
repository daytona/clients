// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona, Image } from '@daytona/sdk'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const dynamic = 'force-dynamic'

// Image file helpers load Node-only modules at call time, so the server build has to bundle
// or trace them.
function checkImageFiles(): string | true {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-nextjs-'))
  try {
    fs.mkdirSync(path.join(dir, 'src'))
    fs.writeFileSync(path.join(dir, 'src', 'a.txt'), 'a')
    fs.writeFileSync(path.join(dir, 'src', 'b.txt'), 'b')
    fs.writeFileSync(path.join(dir, 'Dockerfile'), 'FROM alpine\nCOPY src/*.txt /app/\n')
    const copied = Image.fromDockerfile(path.join(dir, 'Dockerfile'))
      .contextList.map((c) => c.archivePath)
      .sort()
    if (JSON.stringify(copied) !== JSON.stringify(['src/a.txt', 'src/b.txt'])) {
      return `fromDockerfile resolved COPY glob to ${JSON.stringify(copied)}`
    }
    return true
  } catch (e) {
    return e instanceof Error ? e.message : String(e)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

export async function GET() {
  const image = Image.base('alpine').env({ FOO: 'bar' })
  const daytona = new Daytona()
  const iter = daytona.list()
  const listOk = typeof (iter as any)[Symbol.asyncIterator] === 'function' && typeof (await iter.next()) === 'object'
  const imageFiles = checkImageFiles()
  return Response.json({
    cwd: process.cwd(),
    imageOk: image.dockerfile.includes('FROM alpine'),
    imageFilesOk: imageFiles === true,
    imageFilesError: imageFiles === true ? undefined : imageFiles,
    listOk,
  })
}
