/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * post-build.js rewrites every `require(` in the ESM output of every file that has one to
 * `__esmRequire(` — avoid that literal token in string literals here.
 */

import { DaytonaError } from '../errors/DaytonaError'
import { nodeImporters, nodeLoaders } from './NodeLoaders'
import { RUNTIME } from './Runtime'
import { requireUntraced } from './UntracedRequire'

const loaderMap = {
  tinyglobby: async () => require('tinyglobby'),
  '@iarna/toml': async () => require('@iarna/toml'),
  stream: async () => require('stream'),
  tar: async () => require('tar'),
  'expand-tilde': async () => require('expand-tilde'),
  // Node loads ObjectStorage through NodeLoaders; other runtimes have no object storage.
  ObjectStorage: async (): Promise<typeof import('../ObjectStorage')> => {
    throw new Error('object storage needs Node.js')
  },
  fs: async () => require('fs'),
  'form-data': async () => require('form-data'),
  util: async () => require('util'),
}

const requireMap = {
  tinyglobby: () => require('tinyglobby'),
  '@iarna/toml': () => require('@iarna/toml'),
  stream: () => require('stream'),
  tar: () => require('tar'),
  'expand-tilde': () => require('expand-tilde'),
  fs: () => require('fs'),
  'form-data': () => require('form-data'),
  buffer: () => require('buffer'),
  busboy: () => require('busboy'),
  '@opentelemetry/api': (): any => requireUntraced('@opentelemetry/api'),
  '@opentelemetry/sdk-node': (): any => requireUntraced('@opentelemetry/sdk-node'),
  '@opentelemetry/instrumentation-http': (): any => requireUntraced('@opentelemetry/instrumentation-http'),
  '@opentelemetry/sdk-trace': (): any => requireUntraced('@opentelemetry/sdk-trace'),
  '@opentelemetry/exporter-trace-otlp-http': (): any => requireUntraced('@opentelemetry/exporter-trace-otlp-http'),
  '@opentelemetry/otlp-exporter-base': (): any => requireUntraced('@opentelemetry/otlp-exporter-base'),
  '@opentelemetry/semantic-conventions': (): any => requireUntraced('@opentelemetry/semantic-conventions'),
  '@opentelemetry/resources': (): any => requireUntraced('@opentelemetry/resources'),
}

const validateMap: Record<string, (mod: any) => boolean> = {
  tinyglobby: (mod: any) => typeof mod?.globSync === 'function' && typeof mod?.isDynamicPattern === 'function',
  '@iarna/toml': (mod: any) => typeof mod.parse === 'function' && typeof mod.stringify === 'function',
  stream: (mod: any) => typeof mod.Readable === 'function' && typeof mod.Writable === 'function',
  tar: (mod: any) => typeof mod.extract === 'function' && typeof mod.create === 'function',
  'expand-tilde': (mod: any) => typeof mod === 'function',
  fs: (mod: any) => typeof mod.createReadStream === 'function' && typeof mod.readFile === 'function',
  'form-data': (mod: any) => typeof mod === 'function',
  util: (mod: any) => typeof mod.promisify === 'function',
}

type ModuleMap = typeof loaderMap

// Codes of Node loader errors that say nothing about the module itself, because the loader
// never tried to resolve it: Node's createRequire rejecting an unusable module URL (e.g. an
// empty `import.meta.url` in an ESM build re-bundled to CommonJS), or a module not loaded yet.
const UNUSABLE_MODULE_URL_CODES = new Set([
  'ERR_DAYTONA_NODE_LOADER_NOT_READY',
  'ERR_INVALID_ARG_VALUE',
  'ERR_INVALID_ARG_TYPE',
  'ERR_INVALID_URL',
  'ERR_INVALID_URL_SCHEME',
  'ERR_INVALID_FILE_URL_PATH',
  'ERR_INVALID_FILE_URL_HOST',
])

/**
 * Picks the error to report when both the Node loader and the fallback failed: the Node
 * loader's, unless it could not run at all, in which case the fallback's.
 */
function loadError(nodeLoaderError: unknown, fallbackError: unknown): unknown {
  const code = (nodeLoaderError as { code?: unknown } | null)?.code
  return typeof code === 'string' && UNUSABLE_MODULE_URL_CODES.has(code) ? fallbackError : nodeLoaderError
}

/**
 * Loads `name` through its Node loader when there is one, so bundlers can see the module,
 * and through `fallback` otherwise. The fallback also runs when the Node loader fails;
 * if both fail, {@link loadError} picks the error to report.
 */
function loadWithNodeLoader(name: string, fallback: () => unknown): unknown {
  const nodeLoader = nodeLoaders[name]
  if (!nodeLoader) return fallback()

  try {
    return nodeLoader()
  } catch (nodeLoaderError) {
    try {
      return fallback()
    } catch (fallbackError) {
      throw loadError(nodeLoaderError, fallbackError)
    }
  }
}

/**
 * {@link loadWithNodeLoader} for dynamicImport, with the same error policy. It also uses the
 * asynchronous Node loaders, which dynamicRequire must not: they return promises.
 */
async function loadWithNodeLoaderAsync(name: string, fallback: () => unknown): Promise<unknown> {
  const nodeLoader = nodeImporters[name] ?? nodeLoaders[name]
  if (!nodeLoader) return await fallback()

  try {
    return await nodeLoader()
  } catch (nodeLoaderError) {
    try {
      return await fallback()
    } catch (fallbackError) {
      throw loadError(nodeLoaderError, fallbackError)
    }
  }
}

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
    mod = (await loadWithNodeLoaderAsync(name, loader)) as any
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
    mod = loadWithNodeLoader(name, loader)
    mod = unwrapInterop(mod)
  } catch (err) {
    if (err instanceof DaytonaError) throw new DaytonaError(`${errorPrefix || ''}${err.message}`)
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
