/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { isBuiltin } from 'node:module'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const [, , sourceDirArg, v0Version, tag, rootPackagePathArg] = process.argv

if (!sourceDirArg || !v0Version || !tag || !rootPackagePathArg) {
  console.error('usage: node scripts/bundle-v0.mjs <v0-sdk-typescript-src-dir> <v0-version> <tag> <v0-root-package.json>')
  process.exit(1)
}

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sourceDir = resolve(sourceDirArg)
const rootPackagePath = resolve(rootPackagePathArg)
const destinationDir = join(packageRoot, 'src', 'v0')
const packageJsonPath = join(packageRoot, 'package.json')

const REQUIRED_V0_ERROR_EXPORTS = [
  'DaytonaError',
  'DaytonaBadRequestError',
  'DaytonaAuthenticationError',
  'DaytonaForbiddenError',
  'DaytonaNotFoundError',
  'DaytonaTimeoutError',
  'DaytonaConflictError',
  'DaytonaGoneError',
  'DaytonaUnprocessableEntityError',
  'DaytonaRateLimitError',
  'DaytonaInternalServerError',
  'DaytonaServiceUnavailableError',
  'DaytonaConnectionError',
]

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function writeJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`)
}

function copyWithoutTests(from, to) {
  rmSync(to, { recursive: true, force: true })
  mkdirSync(to, { recursive: true })
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (entry.name === '__tests__') continue
    const sourcePath = join(from, entry.name)
    // cpSync would dereference a symlink and copy bytes from outside the release.
    if (entry.isSymbolicLink()) throw new Error(`bundle-v0: refusing to vendor the symlink ${sourcePath}`)
    const destinationPath = join(to, entry.name)
    if (entry.isDirectory()) {
      copyWithoutTests(sourcePath, destinationPath)
      continue
    }
    cpSync(sourcePath, destinationPath, { force: true })
  }
}

function walkFiles(root) {
  const files = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const entryPath = join(root, entry.name)
    if (entry.isDirectory()) files.push(...walkFiles(entryPath))
    else files.push(entryPath)
  }
  return files
}

function basePackageName(specifier) {
  if (specifier.startsWith('@')) {
    const [scope, name] = specifier.split('/')
    return scope && name ? `${scope}/${name}` : specifier
  }
  return specifier.split('/')[0]
}

function parseExportBlocks(indexPath) {
  const source = readFileSync(indexPath, 'utf8')
  const blocks = []
  const pattern = /export\s*\{([\s\S]*?)\}\s*from\s*['"]([^'"]+)['"]/g
  for (const match of source.matchAll(pattern)) {
    // Strip comments from the whole block first: a `// …` line sits in front
    // of a name inside the same comma-separated item.
    const names = match[1]
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => item.split(/\s+as\s+/i).at(-1))
      .filter(Boolean)
    blocks.push({ source: match[2], names })
  }
  return blocks
}

function bareImports(root) {
  const imports = new Set()
  const patterns = [
    /\bimport\s+(?!type\b)(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/g,
    /\bexport\s+(?!type\b)[^'"]*?\s+from\s+['"]([^'"]+)['"]/g,
    /\brequire\(\s*['"]([^'"]+)['"]\s*\)/g,
    /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g,
  ]

  for (const file of walkFiles(root)) {
    if (!file.endsWith('.ts')) continue
    const source = readFileSync(file, 'utf8')
    for (const pattern of patterns) {
      for (const match of source.matchAll(pattern)) {
        const specifier = match[1]
        if (!specifier || specifier.startsWith('.') || specifier.startsWith('/') || specifier.startsWith('node:')) continue
        const name = basePackageName(specifier)
        if (isBuiltin(name)) continue
        imports.add(name)
      }
    }
  }

  return [...imports].sort()
}

copyWithoutTests(sourceDir, destinationDir)

writeFileSync(
  join(destinationDir, '_bundle.ts'),
  `export const BUNDLED_FROM = 'daytona/clients@${tag}'\nexport const BUNDLED_VERSION = '${v0Version}'\n`,
)

// v0 reads its SDK version (X-Daytona-SDK-Version, User-Agent, OTel
// service.version) from `require('../../package.json')`. Bundled one directory
// deeper, that path is dist/cjs/package.json ({"type":"commonjs"}), so the
// version came out undefined. Point getPackageInfo at the bundled release, as
// bundle_v0_python.py does for Python's importlib.metadata lookup.
{
  const importPath = join(destinationDir, 'utils', 'Import.ts')
  const source = readFileSync(importPath, 'utf8')
  const pattern = /export function getPackageInfo\(\)[^{]*\{[\s\S]*?\n\}\n/
  if (!pattern.test(source)) {
    console.error('v0 bundle: utils/Import.ts no longer defines getPackageInfo(); update the version rewrite')
    process.exit(1)
  }
  writeFileSync(
    importPath,
    source.replace(
      pattern,
      [
        '// Rewritten by scripts/bundle-v0.sh: the bundled release, not a package.json lookup.',
        "export function getPackageInfo(): { name: string; version: string } {",
        `  return { name: '@daytona/sdk', version: '${v0Version}' }`,
        '}',
        '',
      ].join('\n'),
    ),
  )
}

// v0's dotenv warning tells the user to pass `apiUrl` to the Daytona
// constructor or set DAYTONA_API_URL. Under the 2.x root neither works (the root
// rejects apiUrl, and nothing in SDK 2.x reads DAYTONA_API_URL, daytona.v0
// included), and the root is the only way to build a v0 client, so
// the remedy is rewritten for it (bundle_v0_python.py does the same).
{
  const runtimePath = join(destinationDir, 'utils', 'Runtime.ts')
  const source = readFileSync(runtimePath, 'utf8')
  const remedy = String.raw` To change the endpoint, pass \`apiUrl\` to the Daytona${'`'} +
          ${'`'} constructor or set \`${'${'}name}\` in the environment of the process.${'`'}`
  if (source.split(remedy).length !== 2) {
    console.error("v0 bundle: utils/Runtime.ts's dotenv warning text changed; update the remedy rewrite")
    process.exit(1)
  }
  const replacement = String.raw` The root's \`daytona.v0\` ignores \`DAYTONA_API_URL\` (use \`DAYTONA_V0_API_URL\` for${'`'} +
          ${'`'} non-production endpoints). SDK 2.x targets Daytona Cloud. For a custom API URL,${'`'} +
          ${'`'} stay on SDK 0.x.${'`'}`
  writeFileSync(runtimePath, source.replace(remedy, () => replacement))
}

// v0 builds a Buffer at module load (utils/FileTransfer.ts). Browsers have no
// global Buffer, and the root's browser `#v0-loader` imports v0 statically, so
// that one line would crash every browser app importing the root, v2-only ones
// included. Build it on first use instead (only Node file transfers reach it).
{
  const transferPath = join(destinationDir, 'utils', 'FileTransfer.ts')
  const source = readFileSync(transferPath, 'utf8')
  const declaration = "const HEADER_SEPARATOR = Buffer.from('\\r\\n\\r\\n')\n"
  if (source.split(declaration).length !== 2) {
    console.error('v0 bundle: utils/FileTransfer.ts no longer declares HEADER_SEPARATOR at module load; update the lazy-Buffer rewrite')
    process.exit(1)
  }
  const uses = source.replace(declaration, '').split(/\bHEADER_SEPARATOR\b/).length - 1
  if (uses === 0) {
    console.error('v0 bundle: utils/FileTransfer.ts declares HEADER_SEPARATOR but never uses it; update the lazy-Buffer rewrite')
    process.exit(1)
  }
  const lazy =
    '// Rewritten by scripts/bundle-v0.sh: built on first use, not at module load (browsers have no Buffer).\n' +
    'let headerSeparatorBuffer: Buffer | undefined\n' +
    "const headerSeparator = (): Buffer => (headerSeparatorBuffer ??= Buffer.from('\\r\\n\\r\\n'))\n"
  writeFileSync(transferPath, source.replace(declaration, lazy).replace(/\bHEADER_SEPARATOR\b/g, 'headerSeparator()'))
}

const v0Index = join(destinationDir, 'index.ts')

// v0's client classes leave the namespace's public surface: SDK 2.x
// builds a v0 client only through the root's `daytona.v0`, which reads the
// class under the private name (src/Daytona.ts). Each name must be re-exported
// exactly once, in a value or type `export { … } from` list.
// Private name -> public name. Deliberately unlike the public names, so a
// compiler or bundler never suggests them for a failed `import { Daytona }`
// ("Did you mean '_Daytona'?"): the root's `daytona.v0` is the only way to
// build a v0 client. @internal — read only by src/Daytona.ts.
const HIDDEN_V0_CLIENTS = { Daytona: 'RootOnlyClientV0', DaytonaConfig: 'RootOnlyConfigV0' }
{
  let source = readFileSync(v0Index, 'utf8')
  for (const [name, privateName] of Object.entries(HIDDEN_V0_CLIENTS)) {
    const pattern = new RegExp(String.raw`(export\s+(?:type\s+)?\{[^}]*?)(?<![\w$])${name}(?![\w$]|\s+as\b)`, 'g')
    const matches = source.match(pattern) ?? []
    if (matches.length !== 1) {
      console.error(`v0 bundle: ${name} is re-exported ${matches.length} times from ${v0Index}; update the client-hiding rewrite`)
      process.exit(1)
    }
    source = source.replace(pattern, (_match, head) => `${head}${name} as ${privateName}`)
  }
  writeFileSync(v0Index, source)
}

const v0Blocks = parseExportBlocks(v0Index)
if (Object.keys(HIDDEN_V0_CLIENTS).some((name) => v0Blocks.some((block) => block.names.includes(name)))) {
  console.error(`v0 bundle: ${v0Index} still exports a hidden client class (${Object.keys(HIDDEN_V0_CLIENTS).join(', ')})`)
  process.exit(1)
}
const v0Names = new Set(v0Blocks.flatMap((block) => block.names))
const missingRequiredV0Exports = REQUIRED_V0_ERROR_EXPORTS.filter((name) => !v0Names.has(name))
if (missingRequiredV0Exports.length > 0) {
  console.error(`v0 bundle is missing required root error exports: ${missingRequiredV0Exports.join(', ')}`)
  process.exit(1)
}
const v2Names = new Set(parseExportBlocks(join(packageRoot, 'src', 'v2', 'index.ts')).flatMap((block) => block.names))
const aliasLines = []
const sharedNames = []

for (const block of v0Blocks) {
  const shared = block.names.filter((name) => v2Names.has(name) && !name.endsWith('V0') && !name.endsWith('V2'))
  if (shared.length === 0) continue
  sharedNames.push(...shared)
  const aliases = shared.map((name) => `${name} as ${name}V0`).join(', ')
  aliasLines.push(`export { ${aliases} } from '${block.source}'`)
}

// Parity with the Python bundler's SHARED_WITH_V0 check: every shared value
// export gets a hand-written `…V2` alias in src/v2/index.ts and an appended
// `…V0` alias here. A pin bump that changes the shared set must update the
// v2 list too, or v0 would ship `FooV0` with no `FooV2` (or the reverse). The
// hidden client classes get no suffixed alias in either namespace.
{
  const shared = [...new Set(sharedNames)].sort()
  const v2Aliases = [...v2Names].filter((name) => name.endsWith('V2')).map((name) => name.slice(0, -2)).sort()
  if (shared.join(',') !== v2Aliases.join(',')) {
    const onlyV0 = shared.filter((name) => !v2Aliases.includes(name))
    const onlyV2 = v2Aliases.filter((name) => !shared.includes(name))
    console.error(
      'v0 bundle: shared value exports and the …V2 aliases in src/v2/index.ts disagree' +
        (onlyV0.length ? `; add …V2 aliases for: ${onlyV0.join(', ')}` : '') +
        (onlyV2.length ? `; …V2 aliases with no shared v0 export: ${onlyV2.join(', ')}` : ''),
    )
    process.exit(1)
  }
}

// Register v0's error classes into the root's shared-category registry when
// the v0 entry loads, so the root classes (`instanceof DaytonaNotFoundError`
// from '@daytona/sdk') catch v0's errors imported from '@daytona/sdk/v0'
// without any prior d.v0 access. The registry is keyed on a registered symbol
// on globalThis, shared by the ESM root and this CJS-only entry
// (src/errors.ts, ERROR_REGISTRY_KEY); each category key is the v0 export name.
const errorSource = v0Blocks.find((block) => block.names.includes('DaytonaNotFoundError'))?.source
if (!errorSource) {
  console.error('v0 bundle: cannot find the module v0 re-exports its error classes from')
  process.exit(1)
}
// Self-check: the module the registration imports must define every class it
// registers, or the registration would silently skip it.
{
  const errorModule = join(destinationDir, `${errorSource.replace(/^\.\//, '')}.ts`)
  const errorText = existsSync(errorModule) ? readFileSync(errorModule, 'utf8') : ''
  const undefinedClasses = REQUIRED_V0_ERROR_EXPORTS.filter((name) => !new RegExp(`^export class ${name}\\b`, 'm').test(errorText))
  if (undefinedClasses.length > 0) {
    console.error(`v0 bundle: ${errorModule} does not define ${undefinedClasses.join(', ')}; the error registration would skip them`)
    process.exit(1)
  }
}
const registrationLines = [
  '// --- added by scripts/bundle-v0.sh: register v0 errors with the root hierarchy.',
  `import * as __daytonaV0Errors from '${errorSource}'`,
  '{',
  "  const __holder = globalThis as unknown as Record<symbol, Map<string, Function[]> | undefined>",
  "  const __key = Symbol.for('@daytona/sdk/error-registry')",
  '  const __registry = (__holder[__key] ??= new Map<string, Function[]>())',
  `  for (const __name of ${JSON.stringify(REQUIRED_V0_ERROR_EXPORTS)}) {`,
  '    const __member = (__daytonaV0Errors as unknown as Record<string, unknown>)[__name]',
  "    if (typeof __member !== 'function') continue",
  '    const __members = __registry.get(__name) ?? []',
  '    if (!__members.includes(__member)) __registry.set(__name, [...__members, __member])',
  '  }',
  '}',
]

{
  const original = readFileSync(v0Index, 'utf8').trimEnd()
  writeFileSync(v0Index, `${original}\n\n${[...aliasLines, '', ...registrationLines].join('\n')}\n`)
  // Read it back: exactly one registration block, after the aliases.
  const written = readFileSync(v0Index, 'utf8')
  if (written.split("Symbol.for('@daytona/sdk/error-registry')").length !== 2) {
    console.error(`v0 bundle: ${v0Index} does not carry exactly one error registration block`)
    process.exit(1)
  }
}

const rootPackage = readJson(rootPackagePath)
const localPackage = readJson(packageJsonPath)
const availableVersions = {
  ...(rootPackage.dependencies ?? {}),
  ...(rootPackage.devDependencies ?? {}),
}
const runtimeDependencies = new Set([
  ...bareImports(destinationDir),
  'tslib',
  'ws',
  // The root's `daytona.v0` type reaches v0's declarations, which import
  // isomorphic-ws -> ws; without its types a consumer compiling with
  // skipLibCheck: false fails on the root import (TS7016).
  '@types/ws',
  '@daytona/api-client',
  '@daytona/toolbox-api-client',
  '@daytona/analytics-api-client',
])

// Runtime imports v0's manifests leave undeclared (its own publish picks
// their versions up transitively, e.g. @opentelemetry/resources) are pinned to
// the single version v0's yarn.lock resolves, as the published package
// declares them; an ambiguous lockfile fails the bundle.
function lockedVersion(dependency) {
  const lockPath = join(dirname(rootPackagePath), 'yarn.lock')
  if (!existsSync(lockPath)) return undefined
  const versions = new Set()
  const lines = readFileSync(lockPath, 'utf8').split('\n')
  for (let i = 0; i < lines.length; i++) {
    const header = lines[i]
    if (!header.startsWith('"') || !header.split(',').some((key) => key.replace(/^[\s"]+/, '').startsWith(`${dependency}@`))) continue
    for (let j = i + 1; j < lines.length && lines[j].startsWith(' '); j++) {
      const match = /^\s+version: (\S+)$/.exec(lines[j])
      if (match) versions.add(match[1])
    }
  }
  if (versions.size > 1) {
    console.error(`v0 bundle: ${dependency} resolves to several versions in ${lockPath}: ${[...versions].join(', ')}`)
    process.exit(1)
  }
  return versions.size === 1 ? [...versions][0] : undefined
}

const resolvedDependencies = { ...(localPackage.dependencies ?? {}) }
const skippedDependencies = []
for (const dependency of [...runtimeDependencies].sort()) {
  if (dependency.startsWith('@daytona/')) {
    resolvedDependencies[dependency] = v0Version
    continue
  }
  // @types/ws is not in v0's manifests (v0 compiles with skipLibCheck); pin
  // the types line matching v0's ws ^8.
  const version = availableVersions[dependency] ?? lockedVersion(dependency) ?? (dependency === '@types/ws' ? '^8.5.0' : undefined)
  if (!version) {
    skippedDependencies.push(dependency)
    continue
  }
  resolvedDependencies[dependency] = version
}

localPackage.dependencies = resolvedDependencies

// v0 runs in browsers through the stubs its package.json "browser" map
// declares (fs, tar, the OpenTelemetry Node SDK, …). This package commits the
// same map so the root's `daytona.v0` bundles for the browser; a pin
// bump whose map changed must update it, so fail instead of shipping a stale one.
const v0Browser = readJson(join(dirname(sourceDir), 'package.json')).browser ?? {}
const committedBrowser = localPackage.browser ?? {}
const browserKeys = (map) => JSON.stringify(Object.entries(map).sort(([a], [b]) => a.localeCompare(b)))
if (browserKeys(v0Browser) !== browserKeys(committedBrowser)) {
  console.error(
    `v0 bundle: package.json "browser" must equal v0's own map at ${tag}:\n` +
      `  v0:        ${JSON.stringify(v0Browser)}\n` +
      `  committed: ${JSON.stringify(committedBrowser)}`,
  )
  process.exit(1)
}

writeJson(packageJsonPath, localPackage)

// The v0 code ships under its own license terms: carry the 0.x SDK's LICENSE
// and the repository's NOTICE (build-v0 copies them next to both v0 builds).
for (const [name, origin] of [
  ['LICENSE', join(dirname(sourceDir), 'LICENSE')],
  ['NOTICE', join(dirname(rootPackagePath), 'NOTICE')],
]) {
  if (!existsSync(origin)) {
    console.error(`v0 bundle: ${origin} is missing; the bundle must carry v0's ${name}`)
    process.exit(1)
  }
  cpSync(origin, join(destinationDir, name))
}

console.log(`bundled ${relative(packageRoot, sourceDir)} -> ${relative(packageRoot, destinationDir)}`)
console.log(`wrote ${relative(packageRoot, join(destinationDir, '_bundle.ts'))}`)
console.log(`hidden client classes: ${Object.values(HIDDEN_V0_CLIENTS).join(', ')}`)
console.log(`shared aliases: ${[...new Set(sharedNames)].sort().join(', ') || 'none'}`)
console.log(`merged dependencies: ${[...runtimeDependencies].sort().join(', ')}`)
if (skippedDependencies.length > 0)
  console.log(
    `not declared (no version in v0's manifests or yarn.lock): ${skippedDependencies.join(', ')}`,
  )
