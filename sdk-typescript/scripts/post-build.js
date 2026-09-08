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
// The generated deps only contain packages imported directly from source, so
// they miss runtime deps that strict resolvers (Yarn PnP, pnpm) require to be
// declared:
//   - tslib: tsconfig.base.json sets "importHelpers": true, so emitted JS
//     imports helpers from tslib at runtime
//   - ws: isomorphic-ws declares ws as a peer dependency and require()s it in
//     Node; an ancestor (this package) must provide it
for (const name of ['tslib', 'ws']) {
  if (!rootDeps[name]) throw new Error(`${name} must be declared in the workspace root dependencies`)
}
pkg.dependencies = { tslib: rootDeps.tslib, ws: rootDeps.ws, ...generatedDeps }
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

// The rewrite is textual, so a `require(` inside a string or a comment is rewritten too.
// Source files under src/utils must not contain that literal outside a real call.
const requireCall = /\brequire\s*\(/g
const shimmedFiles = []
for (const file of fs.existsSync(esmDir) ? jsFilesIn(esmDir) : []) {
  const original = fs.readFileSync(file, 'utf8')
  if (!requireCall.test(original)) continue
  requireCall.lastIndex = 0
  const rewritten = original
    .replace(
      /require\s*\(\s*['"]\.\.\/\.\.\/package\.json['"]\s*\)/g,
      JSON.stringify({ name: pkg.name, version: pkg.version }),
    )
    .replace(requireCall, '__esmRequire(')
  fs.writeFileSync(file, esmRequireShim + rewritten)
  shimmedFiles.push(path.relative(esmDir, file))
}

// A build that shims nothing means the scan above stopped matching, which would leave
// every guarded require a no-op. Fail the build instead.
if (shimmedFiles.length === 0) {
  throw new Error('post-build: no ESM file required the require() shim; the rewrite has stopped matching')
}

// utils/Runtime.js reads dotenv files through require, and what it finds decides which
// host the SDK will talk to, so it must never be published unshimmed. Named explicitly so
// that reordering the build, or moving the reads, fails here rather than in a release.
const runtimeJs = path.join('utils', 'Runtime.js')
if (fs.existsSync(path.join(esmDir, runtimeJs)) && !shimmedFiles.includes(runtimeJs)) {
  throw new Error(`post-build: ${runtimeJs} did not receive the require() shim`)
}

writeJson(path.join(distDir, 'package.json'), pkg)
for (const file of ['README.md', 'LICENSE']) {
  const src = path.join(sourceDir, file)
  if (fs.existsSync(src)) fs.copyFileSync(src, path.join(distDir, file))
}
