/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Loads the OpenTelemetry packages through a require that bundlers do not analyse. Bundling
 * them pulls in require-in-the-middle and import-in-the-middle, which make webpack warn
 * ("Critical dependency: require function is used in a way in which dependencies cannot be
 * statically extracted") and double the size of every server bundle, whether or not the app
 * enables OpenTelemetry. The packages must therefore be present at runtime.
 *
 * In the ESM copy of this file, post-build.js swaps the CommonJS file name for import.meta.url.
 */

import { DaytonaError } from '../errors/DaytonaError'

type NodeModuleApi = { createRequire(path: string): (id: string) => unknown }

// Next.js `output: 'standalone'` copies only the files it traces, and it does not trace the
// dependencies of files added through outputFileTracingIncludes, so these globs cover the
// installed dependency tree of the OpenTelemetry packages above (type-only packages aside).
// UntracedRequire.test.ts fails when an upgrade adds a package they miss.
export const OTEL_OUTPUT_FILE_TRACING_GLOBS = [
  './node_modules/{@grpc,@js-sdsl,@opentelemetry,@protobufjs}/**/*',
  './node_modules/{acorn,acorn-import-attributes,cjs-module-lexer,es-module-lexer,forwarded-parse}/**/*',
  './node_modules/{import-in-the-middle,module-details-from-path,require-in-the-middle,lodash.camelcase}/**/*',
  './node_modules/{debug,ms,long,protobufjs,yaml,yargs,yargs-parser,y18n,cliui,escalade}/**/*',
  './node_modules/{get-caller-file,require-directory,string-width,strip-ansi,wrap-ansi}/**/*',
  './node_modules/{ansi-regex,ansi-styles,color-convert,color-name,emoji-regex,is-fullwidth-code-point}/**/*',
]

const OUTPUT_FILE_TRACING_SNIPPET = [
  'outputFileTracingIncludes: {',
  "  '/**': [",
  ...OTEL_OUTPUT_FILE_TRACING_GLOBS.map((glob) => `    '${glob}',`),
  '  ],',
  '},',
].join('\n')

function ownLocation(): string | undefined {
  return typeof __filename === 'string' && __filename ? __filename : undefined
}

function errorCode(error: unknown): unknown {
  return (error as { code?: unknown } | null)?.code
}

function isModuleNotFound(error: unknown): boolean {
  const code = errorCode(error)
  return code === 'MODULE_NOT_FOUND' || code === 'ERR_MODULE_NOT_FOUND'
}

function isUnusableBase(error: unknown): boolean {
  const code = errorCode(error)
  return typeof code === 'string' && code.startsWith('ERR_INVALID_')
}

function missingPackageError(id: string, cause: unknown): DaytonaError {
  const message = (cause as { message?: unknown } | null)?.message
  const detail = typeof message === 'string' ? message.split('\n')[0] : String(cause)
  return new DaytonaError(
    `The "${id}" package could not be loaded at runtime (${detail}). ` +
      'The SDK loads the OpenTelemetry packages at runtime, so bundlers do not copy them into the build output. ' +
      'Install them next to your app. ' +
      "With Next.js (`output: 'standalone'` and other traced deployments), add them to the output in next.config.js; " +
      'this is needed even with serverExternalPackages, because output tracing cannot follow this runtime load:\n' +
      OUTPUT_FILE_TRACING_SNIPPET,
  )
}

// Node's `module` API. process.getBuiltinModule (Node.js 20.16, 22.3 and later, Bun, Deno)
// gives it without a require that bundlers would follow; the CommonJS build can also require
// it on older Node.js versions. post-build.js turns the require below into the ESM shim,
// which needs process.getBuiltinModule there too.
function nodeModuleApi(): NodeModuleApi | undefined {
  const builtin = (
    globalThis as { process?: { getBuiltinModule?: (name: string) => unknown } }
  ).process?.getBuiltinModule?.('module') as NodeModuleApi | undefined
  if (builtin?.createRequire) return builtin
  try {
    return require('module') as NodeModuleApi
  } catch {
    return undefined
  }
}

/**
 * Loads `id` without exposing it to bundlers. It is resolved from this file first, then from
 * the working directory and the entry script, so a bundled server finds the package in the
 * node_modules deployed next to it.
 *
 * @throws {DaytonaError} If the package cannot be found, naming it and how to include it.
 */
export function requireUntraced(id: string): unknown {
  const nodeModule = nodeModuleApi()
  if (!nodeModule?.createRequire) {
    throw new DaytonaError(
      `The "${id}" package can only be loaded in Node.js 20.16, 22.3 or later, Bun or Deno, ` +
        'or with the CommonJS build of @daytona/sdk.',
    )
  }

  const process = globalThis.process
  const bases = [ownLocation(), `${process.cwd()}/`, process.argv?.[1]]
  let notFound: unknown
  for (const base of bases) {
    if (!base) continue
    let requireFrom: (id: string) => unknown
    try {
      requireFrom = nodeModule.createRequire(base)
    } catch (error) {
      // An unusable base, e.g. an empty import.meta.url after re-bundling to CommonJS.
      if (isUnusableBase(error)) continue
      throw error
    }
    try {
      return requireFrom(id)
    } catch (error) {
      if (!isModuleNotFound(error)) throw error
      notFound ??= error
    }
  }
  throw missingPackageError(id, notFound ?? new Error(`Cannot find module '${id}'`))
}
