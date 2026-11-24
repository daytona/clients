// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Bundled like test.ts, then run from a directory without node_modules: an SDK helper whose
// Node-only module is missing must report that module, not the empty import.meta.url that
// re-bundling the ESM build to CommonJS leaves behind.
import { Image } from '@daytona/sdk'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-esbuild-cjs-isolated-'))
let message = ''
try {
  const reqPath = path.join(dir, 'requirements.txt')
  fs.writeFileSync(reqPath, 'requests==2.31.0\n')
  Image.debianSlim('3.12').pipInstallFromRequirements(reqPath)
} catch (e) {
  message = e instanceof Error ? e.message : String(e)
} finally {
  fs.rmSync(dir, { recursive: true, force: true })
}

if (!message.includes("Cannot find module 'expand-tilde'") || message.includes('file URL')) {
  console.error(`FAIL: expected a missing expand-tilde error, got: ${message || 'no error'}`)
  process.exit(1)
}
console.log('PASS (isolated)')
