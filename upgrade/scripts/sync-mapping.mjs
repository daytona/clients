#!/usr/bin/env node
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Copy the single source of truth, upgrade/mapping/mapping.json, into
// both packages. `--check` fails on drift
// instead of writing; each package's test suite runs the same comparison.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const upgrade = join(here, '..')
const source = readFileSync(join(upgrade, 'mapping', 'mapping.json'), 'utf8')
const targets = [
  join(upgrade, 'typescript', 'src', 'mapping.json'),
  join(upgrade, 'python', 'src', 'daytona_upgrade', 'mapping.json'),
]
const check = process.argv.includes('--check')
let stale = 0
for (const target of targets) {
  let current = null
  try {
    current = readFileSync(target, 'utf8')
  } catch {
    current = null
  }
  if (current === source) continue
  if (check) {
    console.error(`stale: ${target} differs from mapping/mapping.json (run: node upgrade/scripts/sync-mapping.mjs)`)
    stale += 1
  } else {
    writeFileSync(target, source)
    console.log(`wrote ${target}`)
  }
}
process.exit(stale === 0 ? 0 : 1)
