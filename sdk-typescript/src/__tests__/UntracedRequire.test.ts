/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError } from '../errors/DaytonaError'
import * as fs from 'fs'
import * as path from 'path'
import { OTEL_OUTPUT_FILE_TRACING_GLOBS, requireUntraced } from '../utils/UntracedRequire'

describe('requireUntraced', () => {
  it('loads an installed package', () => {
    expect(requireUntraced('@opentelemetry/api')).toBe(require('@opentelemetry/api'))
  })

  it('names a missing package and how to include it', () => {
    let error: unknown
    try {
      requireUntraced('@opentelemetry/not-installed')
    } catch (e) {
      error = e
    }

    expect(error).toBeInstanceOf(DaytonaError)
    const message = (error as Error).message
    expect(message).toMatch(
      /^The "@opentelemetry\/not-installed" package could not be loaded at runtime \(Cannot find module '@opentelemetry\/not-installed'/,
    )
    expect(message).toContain("With Next.js `output: 'standalone'`, add them to the output in next.config.js:")
    expect(message).toContain("outputFileTracingIncludes: {\n  '/**': [\n    './node_modules/{@grpc,")
    for (const glob of OTEL_OUTPUT_FILE_TRACING_GLOBS) expect(message).toContain(`    '${glob}',`)
    expect(message).not.toContain('Require stack')
  })

  it('rethrows errors other than a missing module', () => {
    jest.isolateModules(() => {
      const spy = jest.spyOn(process, 'getBuiltinModule').mockReturnValue({
        createRequire: () => () => {
          throw new SyntaxError('broken package')
        },
      } as never)
      try {
        const { requireUntraced: load } = require('../utils/UntracedRequire')
        expect(() => load('@opentelemetry/api')).toThrow(SyntaxError)
      } finally {
        spy.mockRestore()
      }
    })
  })

  // createRequire rejecting a base is skipped; the same error code from the package itself is
  // the package's own failure and must not be reported as a missing package.
  it('reports an ERR_INVALID_* error thrown while the package loads', () => {
    const packageError = Object.assign(new TypeError('bad argument inside the package'), {
      code: 'ERR_INVALID_ARG_TYPE',
    })
    const spy = jest.spyOn(process, 'getBuiltinModule').mockReturnValue({
      createRequire: () => () => {
        throw packageError
      },
    } as never)
    try {
      expect(() => requireUntraced('@opentelemetry/api')).toThrow(packageError)
    } finally {
      spy.mockRestore()
    }
  })

  it('skips a base that createRequire rejects', () => {
    let calls = 0
    const spy = jest.spyOn(process, 'getBuiltinModule').mockReturnValue({
      createRequire: (base: string) => {
        calls++
        if (calls === 1) throw Object.assign(new TypeError(`invalid base ${base}`), { code: 'ERR_INVALID_ARG_VALUE' })
        return () => 'loaded'
      },
    } as never)
    try {
      expect(requireUntraced('@opentelemetry/api')).toBe('loaded')
      expect(calls).toBe(2)
    } finally {
      spy.mockRestore()
    }
  })

  it('falls back to the module API from require without process.getBuiltinModule', () => {
    const spy = jest.spyOn(process, 'getBuiltinModule').mockReturnValue(undefined as never)
    try {
      expect(requireUntraced('@opentelemetry/api')).toBe(require('@opentelemetry/api'))
    } finally {
      spy.mockRestore()
    }
  })

  it('explains the requirement when the module API is unavailable', () => {
    jest.isolateModules(() => {
      jest.doMock('module', () => {
        throw new Error('no module API')
      })
      const spy = jest.spyOn(process, 'getBuiltinModule').mockReturnValue(undefined as never)
      try {
        const { requireUntraced: load } = require('../utils/UntracedRequire')
        expect(() => load('@opentelemetry/api')).toThrow(
          'The "@opentelemetry/api" package can only be loaded in Node.js 20.16, 22.3 or later, Bun or Deno, ' +
            'or with the CommonJS build of @daytona/sdk.',
        )
      } finally {
        spy.mockRestore()
        jest.dontMock('module')
      }
    })
  })

  // The README documents the same include for Next.js standalone output.
  it('documents every glob in the README', () => {
    const readme = fs.readFileSync(path.resolve(__dirname, '..', '..', 'README.md'), 'utf8')
    for (const glob of OTEL_OUTPUT_FILE_TRACING_GLOBS) expect(readme).toContain(`'${glob}',`)
  })

  // Next.js does not trace the dependencies of included files, so the globs must name every
  // package the OpenTelemetry packages load, wherever the install hoists it.
  it('documents globs that cover the installed OpenTelemetry dependency tree', () => {
    const workspaceRoot = path.resolve(__dirname, '..', '..', '..')
    const covered = new Set(
      OTEL_OUTPUT_FILE_TRACING_GLOBS.flatMap((glob) => /\{([^}]+)\}/.exec(glob)?.[1].split(',') ?? []),
    )
    const seen = new Set<string>()
    const uncovered = new Set<string>()
    const find = (from: string, name: string): string | undefined => {
      for (let dir = from; ; dir = path.dirname(dir)) {
        const candidate = path.join(dir, 'node_modules', name)
        if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate
        if (path.dirname(dir) === dir) return undefined
      }
    }
    const walk = (from: string, name: string, optional = false): void => {
      const dir = find(from, name)
      if (!dir) {
        if (!optional) throw new Error(`${name} is not installed`)
        return
      }
      if (seen.has(dir)) return
      seen.add(dir)
      // A nested copy lives under its top-level parent, so its first path segment decides.
      const [first] = path.relative(path.join(workspaceRoot, 'node_modules'), dir).split(path.sep)
      const typeOnly = first === '@types' || first === 'undici-types'
      if (!typeOnly && !covered.has(first)) uncovered.add(first)
      const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'))
      for (const dep of Object.keys(manifest.dependencies ?? {})) walk(dir, dep)
      for (const dep of Object.keys(manifest.optionalDependencies ?? {})) walk(dir, dep, true)
    }
    for (const name of [
      '@opentelemetry/api',
      '@opentelemetry/sdk-node',
      '@opentelemetry/instrumentation-http',
      '@opentelemetry/sdk-trace',
      '@opentelemetry/exporter-trace-otlp-http',
      '@opentelemetry/otlp-exporter-base',
      '@opentelemetry/semantic-conventions',
      '@opentelemetry/resources',
    ]) {
      walk(workspaceRoot, name)
    }

    expect([...uncovered]).toEqual([])
  })
})
