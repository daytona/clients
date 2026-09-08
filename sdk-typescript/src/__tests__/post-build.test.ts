/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

const SCRIPT = path.join(__dirname, '..', '..', 'scripts', 'post-build.js')

/**
 * Builds the minimum tree post-build.js reads, runs it, and returns the ESM output.
 *
 * `require` is not defined in an ES module, so every ESM file that calls it needs the
 * shim the script injects. These tests hold the rewrite to "every file that needs it"
 * rather than to a list of filenames.
 */
function runPostBuild(esmFiles: Record<string, string>): { read: (file: string) => string; esmDir: string } {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-post-build-'))
  const distDir = path.join(root, 'dist', 'sdk-typescript')
  const esmDir = path.join(distDir, 'esm')
  const sourceDir = path.join(root, 'sdk-typescript')

  for (const [relative, contents] of Object.entries(esmFiles)) {
    const target = path.join(esmDir, relative)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, contents)
  }

  fs.mkdirSync(path.join(distDir, 'cjs'), { recursive: true })
  fs.writeFileSync(path.join(distDir, 'cjs', 'package.json'), JSON.stringify({ dependencies: { axios: '^1.0.0' } }))

  fs.mkdirSync(sourceDir, { recursive: true })
  fs.writeFileSync(path.join(sourceDir, 'package.json'), JSON.stringify({ name: '@daytona/sdk', version: '9.9.9' }))
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ dependencies: { tslib: '^2.0.0', ws: '^8.0.0' } }))
  for (const name of ['api-client', 'toolbox-api-client', 'analytics-api-client']) {
    const dir = path.join(root, 'dist', name)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ version: '9.9.9' }))
  }

  execFileSync('node', [SCRIPT, distDir, root, sourceDir], { stdio: 'pipe' })

  return { read: (file: string) => fs.readFileSync(path.join(esmDir, file), 'utf8'), esmDir }
}

describe('post-build ESM require shim', () => {
  it('shims every ESM file that calls require, not a named list', () => {
    const { read } = runPostBuild({
      'utils/Import.js': `export const load = () => require('fast-glob')\n`,
      'utils/Runtime.js': `export const readFs = () => require('fs')\n`,
      'nested/deep/Other.js': `export const t = () => require('tar')\n`,
    })

    for (const file of ['utils/Import.js', 'utils/Runtime.js', 'nested/deep/Other.js']) {
      const output = read(file)
      expect(output).toContain('const __esmRequire =')
      // The shim's own error message mentions require(), so only the module body below it
      // is checked for a call that was left un-rewritten.
      const body = output.slice(output.indexOf('})();\n') + '})();\n'.length)
      expect(body).toContain('__esmRequire(')
      expect(body).not.toMatch(/[^_]\brequire\s*\(/)
    }
  })

  it('leaves a file that never calls require untouched', () => {
    const source = `export const value = 1\n`
    const { read } = runPostBuild({ 'utils/Import.js': `export const l = () => require('fs')\n`, 'Pure.js': source })

    expect(read('Pure.js')).toBe(source)
  })

  it('inlines the package.json require rather than shimming it', () => {
    const { read } = runPostBuild({
      'utils/Import.js': `export const meta = require('../../package.json')\n`,
    })

    expect(read('utils/Import.js')).toContain('{"name":"@daytona/sdk","version":"9.9.9"}')
    expect(read('utils/Import.js')).not.toContain('package.json')
  })

  // Without this the rewrite could silently stop matching and publish every guarded
  // require as a no-op.
  it('fails the build when no ESM file needs the shim', () => {
    expect(() => runPostBuild({ 'Pure.js': `export const value = 1\n` })).toThrow()
  })

  it('fails the build when utils/Runtime.js is present but missed', () => {
    expect(() =>
      runPostBuild({
        'utils/Import.js': `export const l = () => require('fs')\n`,
        'utils/Runtime.js': `export const value = 1\n`,
      }),
    ).toThrow()
  })
})
