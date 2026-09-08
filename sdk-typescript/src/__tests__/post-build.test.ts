/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

const SCRIPT = path.join(__dirname, '..', '..', 'scripts', 'post-build.js')

const createdRoots: string[] = []

afterEach(() => {
  while (createdRoots.length) fs.rmSync(createdRoots.pop() as string, { recursive: true, force: true })
})

/**
 * Builds the minimum tree post-build.js reads, runs it, and returns the ESM output.
 *
 * `require` is not defined in an ES module, so every ESM file that calls it needs the
 * shim the script injects. These tests hold the rewrite to "every file that needs it"
 * rather than to a list of filenames.
 */
function runPostBuild(
  esmFiles: Record<string, string>,
  rootDeps: Record<string, string | undefined> = {},
): {
  read: (file: string) => string
  esmDir: string
  published: any
} {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-post-build-'))
  // Recorded before anything can throw, so a failing run is cleaned up as well as a
  // passing one. execFileSync below throws by design in the guard tests.
  createdRoots.push(root)
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
  const dependencies: Record<string, string | undefined> = {
    tslib: '^2.0.0',
    ws: '^8.0.0',
    dotenv: '^17.0.0',
    ...rootDeps,
  }
  for (const [name, version] of Object.entries(dependencies)) if (version === undefined) delete dependencies[name]
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ dependencies }))
  for (const name of ['api-client', 'toolbox-api-client', 'analytics-api-client']) {
    const dir = path.join(root, 'dist', name)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ version: '9.9.9' }))
  }

  execFileSync('node', [SCRIPT, distDir, root, sourceDir], { stdio: 'pipe' })

  return {
    read: (file: string) => fs.readFileSync(path.join(esmDir, file), 'utf8'),
    esmDir,
    published: JSON.parse(fs.readFileSync(path.join(distDir, 'package.json'), 'utf8')),
  }
}

describe('post-build ESM require shim', () => {
  it('shims every ESM file that calls require, not a named list', () => {
    const { read } = runPostBuild({
      'utils/Import.js': `export const load = () => require('fast-glob')\n`,
      'utils/Runtime.js': `export const read = () => [require('fs'), require('dotenv')]\n`,
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

  // Whichever host the SDK talks to depends on what Runtime.js reads out of dotenv files,
  // so losing that load is not a build detail.
  it('fails the build when utils/Runtime.js no longer loads dotenv', () => {
    expect(() => runPostBuild({ 'utils/Runtime.js': `export const read = () => require('fs')\n` })).toThrow(
      /no longer loads dotenv/,
    )
  })

  // The scan is textual, so prose or a message mentioning require() must not stand in for
  // a real call and satisfy the guards on its own.
  it.each([
    ['a comment', `// historically this called require('dotenv')\nexport const value = 1\n`],
    ['a string', `export const help = "you must require('dotenv') yourself"\n`],
    ['a template with no interpolation', 'export const help = `you must require(dotenv) yourself`\n'],
  ])('does not count a require named only in %s as a real call', (_label, contents) => {
    expect(() => runPostBuild({ 'utils/Runtime.js': contents })).toThrow()
  })

  // Masking a whole template would hide this and fail the build on valid output.
  it('counts a require written inside a template interpolation', () => {
    const { read } = runPostBuild({
      'utils/Runtime.js': "export const v = `${require('dotenv')}`\n",
    })

    expect(read('utils/Runtime.js')).toContain("__esmRequire('dotenv')")
  })

  // A quoted `dotenv` on its own is not a load: the guard has to see it being required.
  it('fails the build when utils/Runtime.js only names dotenv in a message', () => {
    expect(() =>
      runPostBuild({
        'utils/Runtime.js': `export const read = () => require('fs')\nexport const label = 'dotenv'\n`,
      }),
    ).toThrow(/no longer loads dotenv/)
  })
})

describe('post-build published dependencies', () => {
  const withRequire = { 'utils/Runtime.js': `export const read = () => [require('fs'), require('dotenv')]\n` }

  // The dependency list is generated by scanning source for literal require() and import
  // specifiers. utils/Runtime.ts reaches dotenv through a helper, so the scanner does not
  // see it and it has to be declared explicitly. Publishing without it leaves the endpoint
  // checks with nothing to parse on a clean install, which is the condition they exist for.
  it('declares dotenv even when no source file names it literally', () => {
    expect(runPostBuild(withRequire).published.dependencies.dotenv).toBe('^17.0.0')
  })

  it('still declares tslib and ws', () => {
    const { published } = runPostBuild(withRequire)

    expect(published.dependencies.tslib).toBe('^2.0.0')
    expect(published.dependencies.ws).toBe('^8.0.0')
  })

  it('fails the build when a forced dependency is absent from the workspace root', () => {
    expect(() => runPostBuild(withRequire, { dotenv: undefined })).toThrow()
  })
})
