// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Bundled like test.ts, then run from a directory without node_modules. The local-file
// helpers' modules are in the bundle (except tinyglobby for COPY globs, whose ESM entry cannot
// run in a CommonJS bundle); OpenTelemetry is loaded at runtime, so otelEnabled must explain
// what is missing rather than fail with a bare module-not-found error.
import { Daytona, Image } from '@daytona/sdk'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-esbuild-cjs-isolated-'))
try {
  const reqPath = path.join(dir, 'requirements.txt')
  fs.writeFileSync(reqPath, 'requests==2.31.0\n')
  const pyprojectPath = path.join(dir, 'pyproject.toml')
  fs.writeFileSync(pyprojectPath, '[project]\nname = "app"\ndependencies = ["httpx"]\n')
  fs.mkdirSync(path.join(dir, 'src'))
  fs.writeFileSync(path.join(dir, 'src', 'a.txt'), 'a')

  const image = Image.debianSlim('3.12')
    .pipInstallFromRequirements(reqPath)
    .pipInstallFromPyproject(pyprojectPath)
    .addLocalFile(reqPath, '/app/requirements.txt')
    .addLocalDir(path.join(dir, 'src'), '/app/src')
  for (const [helper, expected] of [
    ['pipInstallFromRequirements', 'pip install -r /.requirements.txt'],
    ['pipInstallFromPyproject', 'pip install -- httpx'],
    ['addLocalFile', '/app/requirements.txt'],
    ['addLocalDir', '/app/src'],
  ]) {
    if (!image.dockerfile.includes(expected)) throw new Error(`${helper} did not produce the expected dockerfile`)
  }
} finally {
  fs.rmSync(dir, { recursive: true, force: true })
}

let message = ''
try {
  new Daytona({ apiKey: 'stub', apiUrl: 'http://127.0.0.1:9/api', otelEnabled: true })
} catch (e) {
  message = e instanceof Error ? e.message : String(e)
}
for (const expected of [
  'OpenTelemetry instrumentation is not supported: The "@opentelemetry/api" package could not be loaded at runtime',
  'outputFileTracingIncludes',
]) {
  if (!message.includes(expected)) {
    console.error(
      `FAIL: expected the otelEnabled error to contain ${JSON.stringify(expected)}, got: ${message || 'no error'}`,
    )
    process.exit(1)
  }
}
console.log('PASS (isolated)')
