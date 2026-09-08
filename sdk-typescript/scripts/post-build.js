#!/usr/bin/env node
// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

const fs = require('fs')
const path = require('path')

const [distDir, workspaceRoot, sourceDir] = process.argv.slice(2)
const esmDir = path.join(distDir, 'esm')
const cjsDir = path.join(distDir, 'cjs')

const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf8'))
const writeJson = (p, data) => fs.writeFileSync(p, JSON.stringify(data, null, 2))

const generatedDeps = readJson(path.join(cjsDir, 'package.json')).dependencies ?? {}

const pkg = readJson(path.join(sourceDir, 'package.json'))
const rootDeps = readJson(path.join(workspaceRoot, 'package.json')).dependencies
// The generated deps only contain packages the scanner sees imported directly
// from source, so they miss runtime deps that strict resolvers (Yarn PnP, pnpm)
// require to be declared:
//   - tslib: tsconfig.base.json sets "importHelpers": true, so emitted JS
//     imports helpers from tslib at runtime
//   - ws: isomorphic-ws declares ws as a peer dependency and require()s it in
//     Node; an ancestor (this package) must provide it
//   - dotenv: utils/Runtime.ts loads it through a helper rather than a literal
//     require('dotenv'), which is what the scanner matches on. Without this the
//     package publishes with no dotenv, and on a clean install the endpoint
//     checks that depend on it silently do nothing.
const forcedDeps = ['tslib', 'ws', 'dotenv']
for (const name of forcedDeps) {
  if (!rootDeps[name]) throw new Error(`${name} must be declared in the workspace root dependencies`)
}
pkg.dependencies = { ...Object.fromEntries(forcedDeps.map((n) => [n, rootDeps[n]])), ...generatedDeps }
for (const name of ['api-client', 'toolbox-api-client', 'analytics-api-client']) {
  const distPkg = readJson(path.join(workspaceRoot, 'dist', name, 'package.json'))
  pkg.dependencies[`@daytona/${name}`] = distPkg.version
}

for (const buildDir of [esmDir, cjsDir]) {
  const srcDir = path.join(buildDir, 'src')
  if (fs.existsSync(srcDir)) {
    for (const entry of fs.readdirSync(srcDir)) {
      fs.cpSync(path.join(srcDir, entry), path.join(buildDir, entry), { recursive: true, force: true })
    }
    fs.rmSync(srcDir, { recursive: true, force: true })
  }
}

writeJson(path.join(esmDir, 'package.json'), { type: 'module' })
writeJson(path.join(cjsDir, 'package.json'), { type: 'commonjs' })

// `require` does not exist in an ES module. A bare call is a ReferenceError, and - worse -
// a call behind a `typeof require !== 'undefined'` test is silently skipped, turning the
// guarded code into a no-op that no unit test running against the CommonJS build can see.
// Every ESM file that calls `require` therefore gets a `createRequire` shim, chosen by
// scanning the output rather than by naming files, so that a caller cannot be missed.
//
// Named `__esmRequire` (not `require`) to avoid shadowing the host CJS `require` when a
// bundler re-compiles this ESM output to CommonJS.
const esmRequireShim =
  `const __esmRequire = (() => {\n` +
  `  try { if (typeof require !== 'undefined') return require; } catch {}\n` +
  `  try {\n` +
  `    const builtinModule = globalThis.process?.getBuiltinModule?.('module');\n` +
  `    if (builtinModule?.createRequire) return builtinModule.createRequire(import.meta.url);\n` +
  `  } catch {}\n` +
  `  return (id) => { throw new Error(\n` +
  `    'cannot require("' + id + '"): no CommonJS require available. ' +\n` +
  `    'If re-bundling @daytona/sdk to CJS, ensure createRequire or the host require is accessible.'\n` +
  `  ); };\n` +
  `})();\n`

const jsFilesIn = (dir) =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(dir, entry.name)
    if (entry.isDirectory()) return jsFilesIn(entryPath)
    return entry.name.endsWith('.js') ? [entryPath] : []
  })

// Comments are dropped before deciding whether a file *needed* the shim, so that a
// `require(` written in prose cannot stand in for a real call and make the guards below
// vacuous. Stripping is a heuristic, not a parser, and a `//` inside a string literal will
// take the rest of the line with it - so it is used only for that accounting. The rewrite
// itself still runs over the whole file, where over-inclusion is harmless.
const withoutComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')

const hasRequireCall = (source) => /\brequire\s*\(/.test(source)
const shimmedFiles = []
for (const file of fs.existsSync(esmDir) ? jsFilesIn(esmDir) : []) {
  const original = fs.readFileSync(file, 'utf8')
  if (!hasRequireCall(original)) continue
  const rewritten = original
    .replace(
      /require\s*\(\s*['"]\.\.\/\.\.\/package\.json['"]\s*\)/g,
      JSON.stringify({ name: pkg.name, version: pkg.version }),
    )
    .replace(/\brequire\s*\(/g, '__esmRequire(')
  fs.writeFileSync(file, esmRequireShim + rewritten)
  if (hasRequireCall(withoutComments(original))) shimmedFiles.push(path.relative(esmDir, file))
}

// A build that shims nothing means the scan above stopped matching, which would leave
// every guarded require a no-op. Fail the build instead.
if (shimmedFiles.length === 0) {
  throw new Error('post-build: no ESM file required the require() shim; the rewrite has stopped matching')
}

// utils/Runtime.js reads dotenv files, and what it finds decides which host the SDK will
// talk to, so it must reach dotenv through a working require in the published build.
// Asserting the rewritten call - outside comments - rather than "the file was processed"
// keeps this meaningful if the reads are ever moved or renamed.
const runtimeJs = path.join('utils', 'Runtime.js')
const runtimeJsPath = path.join(esmDir, runtimeJs)
if (fs.existsSync(runtimeJsPath)) {
  if (!shimmedFiles.includes(runtimeJs)) {
    throw new Error(`post-build: ${runtimeJs} did not receive the require() shim`)
  }
  // The specifier is passed through a helper, so the emitted call is `__esmRequire(id)`
  // and the name only appears at the call site. Checking that the name is still there
  // catches the load being dropped; that it resolves at runtime is covered by dotenv
  // being in the published dependencies above.
  const runtimeSource = withoutComments(fs.readFileSync(runtimeJsPath, 'utf8'))
  if (!/['"]dotenv['"]/.test(runtimeSource)) {
    throw new Error(`post-build: ${runtimeJs} no longer loads dotenv`)
  }
}

writeJson(path.join(distDir, 'package.json'), pkg)
for (const file of ['README.md', 'LICENSE']) {
  const src = path.join(sourceDir, file)
  if (fs.existsSync(src)) fs.copyFileSync(src, path.join(distDir, file))
}
