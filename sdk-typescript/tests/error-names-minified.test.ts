/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createRequire } from 'node:module'
import { join } from 'node:path'
import { buildSync } from 'esbuild'
import * as root from '../src/index'
import * as v2 from '../src/v2/index'

type ErrorClass = new (...args: never[]) => Error
type Bundle = { readonly root: Record<string, unknown>; readonly v2: Record<string, unknown> }

const packageRoot = join(__dirname, '..')

/** The root and v2 entry points bundled and minified as an application build would, then loaded. */
function minifiedBundle(): { readonly bundle: Bundle; readonly code: string } {
  const result = buildSync({
    stdin: {
      contents: "export * as root from './src/index.ts'\nexport * as v2 from './src/v2/index.ts'\n",
      resolveDir: packageRoot,
      loader: 'ts',
    },
    bundle: true,
    minify: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    external: ['#v0-loader'],
    write: false,
    logLevel: 'silent',
  })
  const code = result.outputFiles[0]?.text ?? ''
  const module = { exports: {} as Bundle }
  const load = new Function('require', 'module', 'exports', code) as (require: NodeRequire, module: { exports: Bundle }, exports: Bundle) => void
  load(createRequire(join(packageRoot, 'package.json')), module, module.exports)
  return { bundle: module.exports, code }
}

function errorClasses(namespace: Record<string, unknown>): ReadonlyArray<readonly [string, ErrorClass]> {
  return Object.entries(namespace).filter(
    (entry): entry is [string, ErrorClass] => typeof entry[1] === 'function' && entry[1].prototype instanceof Error,
  )
}

/** The export name each class is published under, minus the suffixed aliases (`DaytonaErrorV2`). */
function canonical(namespace: Record<string, unknown>): ReadonlyArray<readonly [string, ErrorClass]> {
  return errorClasses(namespace).filter(([name, errorClass]) => !errorClasses(namespace).some(([other, same]) => same === errorClass && other !== name && name.startsWith(other)))
}

describe('error names in a minified bundle', () => {
  const { bundle, code } = minifiedBundle()

  it('is actually minified', () => {
    expect(code).not.toMatch(/class NotFoundError\b/)
    expect(code).not.toMatch(/class DaytonaNotFoundError\b/)
  })

  it('keeps every exported error class name', () => {
    const rootClasses = canonical(bundle.root)
    const v2Classes = canonical(bundle.v2)
    expect(rootClasses.length).toBe(13)
    expect(v2Classes.length).toBeGreaterThan(100)
    for (const [name, errorClass] of [...rootClasses, ...v2Classes]) expect([name, errorClass.name]).toEqual([name, name])
  })

  it('names errors by their class, with instanceof and user subclasses intact', () => {
    const NotFoundError = bundle.v2.NotFoundError as typeof v2.NotFoundError
    const ConnectionError = bundle.v2.ConnectionError as typeof v2.ConnectionError
    const BuildLogInterruptedError = bundle.v2.BuildLogInterruptedError as typeof v2.BuildLogInterruptedError
    const DaytonaNotFoundError = bundle.root.DaytonaNotFoundError as typeof root.DaytonaNotFoundError
    const DaytonaError = bundle.root.DaytonaError as typeof root.DaytonaError

    const notFound = new NotFoundError('missing', { statusCode: 404 })
    expect(notFound.name).toBe('NotFoundError')
    expect(String(notFound)).toBe('NotFoundError: missing')
    expect(notFound.stack?.split('\n')[0]).toBe('NotFoundError: missing')
    expect(notFound).toBeInstanceOf(DaytonaNotFoundError)
    expect(notFound).toBeInstanceOf(DaytonaError)
    expect(new ConnectionError('Failed to fetch').name).toBe('ConnectionError')
    expect(new BuildLogInterruptedError('cut').name).toBe('BuildLogInterruptedError')
    expect(new DaytonaNotFoundError('root').name).toBe('DaytonaNotFoundError')

    class AppNotFoundError extends NotFoundError {}
    const mine = new AppNotFoundError('mine')
    expect(mine.name).toBe('AppNotFoundError')
    expect(mine).toBeInstanceOf(NotFoundError)
    expect(mine).toBeInstanceOf(DaytonaNotFoundError)
  })

  it('matches the unbundled names', () => {
    for (const [name, errorClass] of [...canonical(root), ...canonical(v2)]) expect([name, errorClass.name]).toEqual([name, name])
  })
})
