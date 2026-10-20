/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * post-build.js rewrites every `require(` in the ESM output of every file that has one to
 * `__esmRequire(` — avoid that literal token in string literals here.
 */

import { DaytonaError } from '../errors/DaytonaError'
import { RUNTIME } from './Runtime'

declare const __webpack_require__: unknown
declare const __non_webpack_require__: NodeRequire

// Webpack and webpack-compatible bundlers (Rspack) fold `typeof __webpack_require__` to a
// constant and drop the literal require, so fast-glob stays out of the bundle graph and loads
// from node_modules at runtime: webpack production builds of CommonJS apps fail in fast-glob's
// braces/picomatch dependencies. Inside such a bundle, fast-glob is resolved from the SDK's own
// install first, so layouts that do not hoist it next to the app (pnpm, Yarn PnP) still find it.
// Plain Node and other bundlers take the literal require.
// Only a failure to resolve fast-glob falls through to the next location; load errors (a broken
// install, a missing transitive dependency) propagate unchanged.
const isNotFound = (err: unknown) => {
  const code = (err as { code?: unknown } | null)?.code
  return code === 'MODULE_NOT_FOUND' || code === 'ERR_PACKAGE_PATH_NOT_EXPORTED'
}

const requireFastGlob = () => {
  if (typeof __webpack_require__ !== 'function') return require('fast-glob')
  const { createRequire } = __non_webpack_require__('module') as typeof import('module')
  for (const sdkName of ['@daytona/sdk', '@daytonaio/sdk']) {
    let sdkRequire: NodeRequire
    let fastGlobPath: string
    try {
      sdkRequire = createRequire(__non_webpack_require__.resolve(`${sdkName}/package.json`))
      fastGlobPath = sdkRequire.resolve('fast-glob')
    } catch (err) {
      if (isNotFound(err)) continue
      throw err
    }
    return sdkRequire(fastGlobPath)
  }
  let fastGlobPath: string
  try {
    fastGlobPath = __non_webpack_require__.resolve('fast-glob')
  } catch (err) {
    if (!isNotFound(err)) throw err
    const msg = (err instanceof Error ? err.message : String(err)).split('\n')[0]
    throw new Error(
      `${msg}. This app was bundled with webpack or Rspack, which load fast-glob from node_modules at runtime: ` +
        'deploy the bundle with @daytona/sdk (or @daytonaio/sdk) installed',
      { cause: err },
    )
  }
  return __non_webpack_require__(fastGlobPath)
}

const loaderMap = {
  'fast-glob': async () => requireFastGlob(),
  '@iarna/toml': async () => require('@iarna/toml'),
  stream: async () => require('stream'),
  tar: async () => require('tar'),
  'expand-tilde': async () => require('expand-tilde'),
  // Stored in a variable + /* @vite-ignore */ so Vite/Rollup don't statically
  // chunk this; otherwise apps bundling the SDK source (e.g. the dashboard)
  // would pull @aws-sdk transitively. Node consumers resolve at runtime.
  ObjectStorage: () => {
    const path = '../ObjectStorage.js'
    return import(/* @vite-ignore */ path)
  },
  fs: async () => require('fs'),
  'form-data': async () => require('form-data'),
  util: async () => require('util'),
}

const requireMap = {
  'fast-glob': requireFastGlob,
  '@iarna/toml': () => require('@iarna/toml'),
  stream: () => require('stream'),
  tar: () => require('tar'),
  'expand-tilde': () => require('expand-tilde'),
  fs: () => require('fs'),
  'form-data': () => require('form-data'),
  buffer: () => require('buffer'),
  busboy: () => require('busboy'),
  '@opentelemetry/api': () => require('@opentelemetry/api'),
  '@opentelemetry/sdk-node': () => require('@opentelemetry/sdk-node'),
  '@opentelemetry/instrumentation-http': () => require('@opentelemetry/instrumentation-http'),
  '@opentelemetry/sdk-trace-base': () => require('@opentelemetry/sdk-trace-base'),
  '@opentelemetry/exporter-trace-otlp-http': () => require('@opentelemetry/exporter-trace-otlp-http'),
  '@opentelemetry/otlp-exporter-base': () => require('@opentelemetry/otlp-exporter-base'),
  '@opentelemetry/semantic-conventions': () => require('@opentelemetry/semantic-conventions'),
  '@opentelemetry/resources': () => require('@opentelemetry/resources'),
}

const validateMap: Record<string, (mod: any) => boolean> = {
  'fast-glob': (mod: any) => typeof mod === 'function' && typeof mod?.sync === 'function',
  '@iarna/toml': (mod: any) => typeof mod.parse === 'function' && typeof mod.stringify === 'function',
  stream: (mod: any) => typeof mod.Readable === 'function' && typeof mod.Writable === 'function',
  tar: (mod: any) => typeof mod.extract === 'function' && typeof mod.create === 'function',
  'expand-tilde': (mod: any) => typeof mod === 'function',
  fs: (mod: any) => typeof mod.createReadStream === 'function' && typeof mod.readFile === 'function',
  'form-data': (mod: any) => typeof mod === 'function',
  util: (mod: any) => typeof mod.promisify === 'function',
}

type ModuleMap = typeof loaderMap

export async function dynamicImport<K extends keyof ModuleMap>(
  name: K,
  errorPrefix?: string,
): Promise<Awaited<ReturnType<ModuleMap[K]>>> {
  const loader = loaderMap[name]
  if (!loader) {
    throw new DaytonaError(`${errorPrefix || ''} Unknown module "${name}"`)
  }

  let mod: any
  try {
    mod = (await loader()) as any
    mod = unwrapInterop(mod)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    throw new DaytonaError(`${errorPrefix || ''} Module "${name}" is not available in the "${RUNTIME}" runtime: ${msg}`)
  }

  if (validateMap[name] && !validateMap[name](mod)) {
    throw new DaytonaError(
      `${errorPrefix || ''} Module "${name}" didn't pass import validation in the "${RUNTIME}" runtime`,
    )
  }

  return mod
}

type RequireMap = typeof requireMap

function unwrapInterop(mod: any): any {
  if (!mod || typeof mod !== 'object' || mod.default === undefined) return mod
  const namedKeys = Object.keys(mod).filter((k) => k !== 'default')
  if (namedKeys.length === 0) return mod.default
  return mod
}

export function dynamicRequire<K extends keyof RequireMap>(name: K, errorPrefix?: string): ReturnType<RequireMap[K]> {
  const loader = requireMap[name]
  if (!loader) {
    throw new DaytonaError(`${errorPrefix || ''} Unknown module "${name}"`)
  }

  let mod: any
  try {
    mod = loader()
    mod = unwrapInterop(mod)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    throw new DaytonaError(`${errorPrefix || ''} Module "${name}" is not available in the "${RUNTIME}" runtime: ${msg}`)
  }

  if (validateMap[name] && !validateMap[name](mod)) {
    throw new DaytonaError(
      `${errorPrefix || ''} Module "${name}" didn't pass import validation in the "${RUNTIME}" runtime`,
    )
  }

  return mod
}

let _packageInfo: { name: string; version: string } | null = null

export function getPackageInfo(): { name: string; version: string } {
  if (_packageInfo) return _packageInfo
  try {
    const pkg = require('../../package.json')
    _packageInfo = { name: pkg.name, version: pkg.version }
  } catch {
    _packageInfo = { name: '@daytona/sdk', version: '0.0.0' }
  }
  return _packageInfo
}
