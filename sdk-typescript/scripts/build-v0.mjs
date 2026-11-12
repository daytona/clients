/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = new URL('../', import.meta.url)
const v0Entry = new URL('../src/v0/index.ts', import.meta.url)

if (!existsSync(v0Entry)) {
  // Local and source builds skip the v0 namespace; the publish workflow sets
  // DAYTONA_REQUIRE_V0_BUNDLE=1 so a missing bundle fails the build instead
  // of shipping a package whose d.v0 always throws NOT_IMPLEMENTED.
  if (process.env.DAYTONA_REQUIRE_V0_BUNDLE === '1') {
    console.error('build:v0: src/v0 is absent but DAYTONA_REQUIRE_V0_BUNDLE=1 (run scripts/bundle-v0.sh first)')
    process.exit(1)
  }
  // Emit the stub as the `./v0` entry, so the advertised export resolves:
  // `d.v0` throws the 501 NotSupportedError, and the root's declarations
  // type-check for strict consumers of packed/linked source builds.
  const tsc = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url))
  const stub = fileURLToPath(new URL('../src/v0-unbundled.ts', import.meta.url))
  for (const [module, dir, declaration] of [['commonjs', 'cjs', true], ['es2022', 'esm', false]]) {
    const out = fileURLToPath(new URL(`../dist/${dir}/v0`, import.meta.url))
    const args = [tsc, stub, '--module', module, '--target', 'es2022', '--lib', 'es2022', '--skipLibCheck', '--outDir', out]
    if (declaration) args.push('--declaration')
    const run = spawnSync(process.execPath, args, { stdio: 'inherit' })
    if (run.status !== 0) process.exit(run.status ?? 1)
    for (const ext of declaration ? ['.js', '.d.ts'] : ['.js']) renameSync(join(out, `v0-unbundled${ext}`), join(out, `index${ext}`))
  }
  console.log('build:v0: src/v0 absent; emitted the unbundled stub as dist/*/v0 (d.v0 throws NotSupportedError)')
  process.exit(0)
}

const tscCli = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url))
function tsc(config) {
  const result = spawnSync(process.execPath, [tscCli, '-p', fileURLToPath(new URL(`../${config}`, import.meta.url))], {
    cwd: fileURLToPath(root),
    stdio: 'inherit',
  })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

// dist/cjs/v0: what Node loads (`./v0`'s default condition, and daytona.v0).
tsc('tsconfig.v0.json')

// The bundle exports v0's client class only as the private `RootOnlyClientV0`.
// A 0.x-style `new (require('@daytona/sdk/v0').Daytona)()` gets an error naming
// the root form instead of a TypeError. A stub function, not a throwing getter,
// so jest automocking can read it. The descriptor must start with
// `configurable`: Node's CommonJS export detection (cjs-module-lexer)
// recognises `defineProperty` descriptors that start with `value` or
// `enumerable`, so this one stays invisible to ESM, and
// `import { Daytona } from '@daytona/sdk/v0'` fails at link time.
{
  const cjsIndex = fileURLToPath(new URL('../dist/cjs/v0/index.js', import.meta.url))
  const message =
    "`Daytona` is not exported from '@daytona/sdk/v0': the v0 client is built only through the root client. " +
    "Use `import { Daytona } from '@daytona/sdk'`, then `new Daytona().v0`. " +
    'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.'
  writeFileSync(
    cjsIndex,
    readFileSync(cjsIndex, 'utf8') +
      '\n// Added by scripts/build-v0.mjs: SDK 2.x builds the v0 client only through the root.\n' +
      `Object.defineProperty(exports, 'Daytona', { configurable: true, writable: true, value: function Daytona() { throw new Error(${JSON.stringify(message)}) } })\n`,
  )
}

// dist/esm/v0: what browser bundlers load (`./v0`'s "browser" condition), built
// the way the 0.x SDK builds its own ESM output. v0 calls `require()` behind
// runtime checks (Node-only modules, loaded lazily); v0's post-build
// rewrites every such call to a shim so bundlers do not try to bundle those
// modules, and so do we (same shim, same blanket rewrite: v0 avoids the literal
// token in strings for this reason).
tsc('tsconfig.v0.esm.json')
const ESM_REQUIRE_SHIM =
  'const __esmRequire = (() => {\n' +
  "  try { if (typeof require !== 'undefined') return require; } catch {}\n" +
  '  try {\n' +
  "    const builtinModule = globalThis.process?.getBuiltinModule?.('module');\n" +
  '    if (builtinModule?.createRequire) return builtinModule.createRequire(import.meta.url);\n' +
  '  } catch {}\n' +
  '  return (id) => { throw new Error(\n' +
  '    \'cannot require("\' + id + \'"): no CommonJS require available. \' +\n' +
  "    'If re-bundling @daytona/sdk to CJS, ensure createRequire or the host require is accessible.'\n" +
  '  ); };\n' +
  '})();\n'
const REQUIRE_CALL = /(?<![\w$.])require\s*\(/g
function jsFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return jsFiles(path)
    return entry.name.endsWith('.js') ? [path] : []
  })
}
const esmDir = fileURLToPath(new URL('../dist/esm/v0', import.meta.url))
let shimmed = 0
for (const file of jsFiles(esmDir)) {
  const source = readFileSync(file, 'utf8')
  if (!REQUIRE_CALL.test(source)) continue
  REQUIRE_CALL.lastIndex = 0
  writeFileSync(file, ESM_REQUIRE_SHIM + source.replace(REQUIRE_CALL, '__esmRequire('))
  shimmed++
}
if (shimmed === 0) {
  console.error('build:v0: no require() call found in the ESM v0 output; the browser build would be unguarded')
  process.exit(1)
}
console.log(`build:v0: ESM v0 build for browsers, require() shimmed in ${shimmed} files`)

// v0's sources import relatively without extensions (`./Daytona`). Inside this
// "type": "module" package webpack resolves ES modules strictly
// (`fullySpecified`) and fails on every one of them, so the root's browser
// `daytona.v0` would not bundle with webpack. Point each relative specifier at
// its emitted file (`./Daytona.js`, `./utils/index.js`); one that resolves to
// no file fails the build.
const RELATIVE_SPECIFIER = /(\bfrom\s*|\bimport\s*\(?\s*)(['"])(\.{1,2}\/[^'"]*)\2/g
let specified = 0
for (const file of jsFiles(esmDir)) {
  const source = readFileSync(file, 'utf8')
  const rewritten = source.replace(RELATIVE_SPECIFIER, (match, head, quote, specifier) => {
    const target = join(file, '..', specifier)
    if (/\.(?:js|json)$/.test(specifier)) {
      if (existsSync(target)) return match
      console.error(`build:v0: ${file}: relative import '${specifier}' resolves to no emitted file`)
      process.exit(1)
    }
    const resolved = existsSync(`${target}.js`) ? `${specifier}.js` : existsSync(join(target, 'index.js')) ? `${specifier}/index.js` : undefined
    if (resolved === undefined) {
      console.error(`build:v0: ${file}: relative import '${specifier}' resolves to no emitted file`)
      process.exit(1)
    }
    specified++
    return `${head}${quote}${resolved}${quote}`
  })
  if (rewritten !== source) writeFileSync(file, rewritten)
}
console.log(`build:v0: ${specified} relative imports in the ESM v0 build fully specified (webpack)`)

// The package.json "browser" map stubs Node-only modules (crypto, fs, the AWS
// and OpenTelemetry Node SDKs, …) with an empty module. Bundlers that check
// named imports against the stub (Rollup: `"createHmac" is not exported by
// empty.js`) fail on `import { x } from 'crypto'`, and the root's browser
// `#v0-loader` imports this build statically. A namespace import is
// valid against an empty module, so rewrite each named import from a stubbed
// module to one; the names stay `undefined` in browsers, as with the stub.
const browserStubs = new Set(
  Object.entries(JSON.parse(readFileSync(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8')).browser ?? {})
    .filter(([, target]) => target === false)
    .map(([name]) => name),
)
const NAMED_IMPORT = /^import\s*\{([^}]*)\}\s*from\s*(['"])([^'"]+)\2;?$/gm
let namespaced = 0
for (const file of jsFiles(esmDir)) {
  const source = readFileSync(file, 'utf8')
  const rewritten = source.replace(NAMED_IMPORT, (match, names, quote, specifier) => {
    if (!browserStubs.has(specifier)) return match
    const binding = `__browserStub${namespaced++}`
    const bindings = names
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => name.replace(/\s+as\s+/, ': '))
    return `import * as ${binding} from ${quote}${specifier}${quote};\nconst { ${bindings.join(', ')} } = ${binding};`
  })
  if (rewritten !== source) writeFileSync(file, rewritten)
}
console.log(`build:v0: ${namespaced} named imports from browser-stubbed modules made namespace imports (Rollup)`)

// v0's own LICENSE and NOTICE (copied into src/v0 by bundle-v0) next to both builds.
for (const name of ['LICENSE', 'NOTICE']) {
  const origin = fileURLToPath(new URL(`../src/v0/${name}`, import.meta.url))
  if (!existsSync(origin)) {
    console.error(`build:v0: src/v0/${name} is missing (re-run scripts/bundle-v0.sh)`)
    process.exit(1)
  }
  for (const dir of ['cjs', 'esm']) copyFileSync(origin, fileURLToPath(new URL(`../dist/${dir}/v0/${name}`, import.meta.url)))
}
