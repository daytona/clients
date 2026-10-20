// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Verifies a CommonJS app that requires the SDK builds with webpack in production
// mode and that the bundle runs, including the fast-glob path that the SDK loads
// at runtime instead of bundling (Image.fromDockerfile resolving a COPY glob).

const { Daytona, Image } = require('@daytona/sdk')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

async function main() {
  const image = Image.base('alpine').env({ FOO: 'bar' })
  if (!image.dockerfile.includes('FROM alpine')) throw new Error('Image.base failed')
  if (!image.dockerfile.includes('ENV FOO')) throw new Error('Image.env failed')

  const daytona = new Daytona()
  const iter = daytona.list()
  if (typeof iter[Symbol.asyncIterator] !== 'function') {
    throw new Error('list() did not return an async iterator')
  }
  const first = await iter.next()
  if (typeof first !== 'object' || !('done' in first)) {
    throw new Error('list() iterator did not yield a valid result')
  }

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-webpack-cjs-'))
  fs.mkdirSync(path.join(dir, 'src'))
  fs.writeFileSync(path.join(dir, 'src', 'a.txt'), 'a')
  fs.writeFileSync(path.join(dir, 'src', 'b.txt'), 'b')
  fs.writeFileSync(path.join(dir, 'Dockerfile'), 'FROM alpine\nCOPY ["src/*.txt", "/app/"]\n')
  const fromDockerfile = Image.fromDockerfile(path.join(dir, 'Dockerfile'))
  const copied = fromDockerfile.contextList.map((c) => path.basename(c.sourcePath)).sort()
  if (copied.join(',') !== 'a.txt,b.txt') {
    throw new Error(`fromDockerfile resolved COPY glob to [${copied}], expected [a.txt,b.txt]`)
  }

  const withDir = Image.debianSlim('3.12').addLocalDir(path.join(dir, 'src'), '/app/src')
  if (!withDir.dockerfile.includes('/app/src')) throw new Error('addLocalDir failed')

  console.log('PASS')
}

main().catch((e) => {
  console.error('FAIL:', e && e.message ? e.message : e)
  process.exit(1)
})
