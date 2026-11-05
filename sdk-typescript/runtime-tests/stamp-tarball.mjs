/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Restamps an unpacked @daytona/sdk tarball with the version run-all.sh made
// up for this run: package.json and the built SDK_VERSION constant that both
// transports send as X-Daytona-SDK-Version.
//   node stamp-tarball.mjs <unpacked package dir> <current version> <new version>

import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [dir, current, next] = process.argv.slice(2)
if (!dir || !current || !next) {
  console.error('usage: stamp-tarball.mjs <package dir> <current version> <new version>')
  process.exit(2)
}

const manifestPath = join(dir, 'package.json')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
if (manifest.version !== current) {
  console.error(`stamp-tarball: package.json is ${manifest.version}, expected ${current}`)
  process.exit(1)
}
manifest.version = next
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

const escaped = current.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const assignment = new RegExp(`(SDK_VERSION = )(['"])${escaped}\\2`, 'g')
for (const build of ['esm', 'cjs']) {
  const path = join(dir, 'dist', build, 'version.js')
  const source = readFileSync(path, 'utf8')
  if ((source.match(assignment) ?? []).length !== 1) {
    console.error(`stamp-tarball: ${path} does not assign SDK_VERSION = '${current}' exactly once`)
    process.exit(1)
  }
  writeFileSync(path, source.replace(assignment, `$1$2${next}$2`))
}
