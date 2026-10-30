// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Shared by the CommonJS (test.js) and ESM (test.mjs) entries, so webpack bundles both the
// cjs/ and esm/ builds of the SDK.

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

module.exports = async function check({ Daytona, Image }) {
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
  try {
    fs.mkdirSync(path.join(dir, 'src'))
    fs.writeFileSync(path.join(dir, 'src', 'a.txt'), 'a')
    fs.writeFileSync(path.join(dir, 'src', 'b.txt'), 'b')
    fs.writeFileSync(path.join(dir, 'Dockerfile'), 'FROM alpine\nCOPY ["src/*.txt", "/app/"]\n')
    const fromDockerfile = Image.fromDockerfile(path.join(dir, 'Dockerfile'))
    const copied = JSON.stringify(
      fromDockerfile.contextList
        .map((c) => [path.relative(dir, c.sourcePath), c.archivePath])
        .sort(([a], [b]) => a.localeCompare(b)),
    )
    const expected = JSON.stringify([
      [path.join('src', 'a.txt'), 'src/a.txt'],
      [path.join('src', 'b.txt'), 'src/b.txt'],
    ])
    if (copied !== expected) {
      throw new Error(`fromDockerfile resolved COPY glob to ${copied}, expected ${expected}`)
    }

    const withDir = Image.debianSlim('3.12').addLocalDir(path.join(dir, 'src'), '/app/src')
    if (!withDir.dockerfile.includes('/app/src')) throw new Error('addLocalDir failed')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }

  console.log('PASS')
}
