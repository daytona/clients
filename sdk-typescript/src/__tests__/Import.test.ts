// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { dynamicImport, dynamicRequire } from '../utils/Import'

type WebpackGlobals = typeof globalThis & { __webpack_require__?: unknown; __non_webpack_require__?: unknown }

const notFound = (message: string) => Object.assign(new Error(message), { code: 'MODULE_NOT_FOUND' })

// Models a node_modules tree: `installs` maps a directory to the packages resolvable from it,
// or to the error resolving them throws. The bundle's own require resolves from `/app`.
const fakeWebpack = (installs: Record<string, Array<string | [string, Error]>>) => {
  const fastGlob = Object.assign(() => [], { sync: () => [] })
  const load = jest.fn((path: string) => {
    if (path.endsWith('/fast-glob/out/index.js')) return fastGlob
    throw new Error(`unexpected load ${path}`)
  })
  const requireFrom = (dir: string) => {
    const resolve = jest.fn((id: string) => {
      const entry = (installs[dir] ?? []).find((e) => (typeof e === 'string' ? e : e[0]) === id)
      if (Array.isArray(entry)) throw entry[1]
      if (!entry) throw notFound(`Cannot find module '${id}'\nRequire stack:\n- ${dir}`)
      return id.endsWith('/package.json') ? `${dir}/node_modules/${id}` : `${dir}/node_modules/${id}/out/index.js`
    })
    return Object.assign(
      jest.fn((id: string) => load(id.startsWith('/') ? id : resolve(id))),
      { resolve },
    )
  }
  const sdkRequires: Record<string, ReturnType<typeof requireFrom>> = {}
  const createRequire = jest.fn((manifest: string) => {
    const dir = manifest.replace(/\/package\.json$/, '')
    return (sdkRequires[dir] ??= requireFrom(dir))
  })
  const bundleRequire = requireFrom('/app')
  const runtimeRequire = Object.assign(
    jest.fn((id: string) => (id === 'module' ? { createRequire } : bundleRequire(id))),
    { resolve: bundleRequire.resolve },
  )
  const g = globalThis as WebpackGlobals
  g.__webpack_require__ = () => undefined
  g.__non_webpack_require__ = runtimeRequire
  return { fastGlob, load, createRequire, sdkRequires, bundleResolve: bundleRequire.resolve }
}

describe('fast-glob loading', () => {
  afterEach(() => {
    const g = globalThis as WebpackGlobals
    delete g.__webpack_require__
    delete g.__non_webpack_require__
  })

  it('loads the real fast-glob outside webpack', async () => {
    expect(typeof dynamicRequire('fast-glob').sync).toBe('function')
    expect(typeof (await dynamicImport('fast-glob')).sync).toBe('function')
  })

  it('resolves fast-glob from the SDK install inside a webpack bundle', async () => {
    const { fastGlob, load } = fakeWebpack({
      '/app': ['@daytona/sdk/package.json'],
      '/app/node_modules/@daytona/sdk': ['fast-glob'],
    })

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(await dynamicImport('fast-glob')).toBe(fastGlob)
    expect(load).toHaveBeenCalledWith('/app/node_modules/@daytona/sdk/node_modules/fast-glob/out/index.js')
  })

  it('falls back to the legacy SDK install', () => {
    const { fastGlob, load } = fakeWebpack({
      '/app': ['@daytonaio/sdk/package.json'],
      '/app/node_modules/@daytonaio/sdk': ['fast-glob'],
    })

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(load).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenCalledWith('/app/node_modules/@daytonaio/sdk/node_modules/fast-glob/out/index.js')
  })

  it('falls back to the bundle require when no SDK install resolves fast-glob', () => {
    const { fastGlob, load } = fakeWebpack({
      '/app': ['@daytona/sdk/package.json', 'fast-glob'],
      '/app/node_modules/@daytona/sdk': [],
    })

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(load).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenCalledWith('/app/node_modules/fast-glob/out/index.js')
  })

  it('tells a bundled app to install the SDK when fast-glob is missing', () => {
    fakeWebpack({ '/app': [] })

    expect(() => dynamicRequire('fast-glob')).toThrow(
      /Cannot find module 'fast-glob'\. This app was bundled with webpack or Rspack.*@daytona\/sdk \(or @daytonaio\/sdk\) installed$/,
    )
    expect(() => dynamicRequire('fast-glob')).not.toThrow(/Require stack/)
  })

  it.each([
    ['fails to initialize', new Error('fast-glob init failure')],
    ['misses a dependency', notFound("Cannot find module '@nodelib/fs.stat'")],
  ])('surfaces a resolved fast-glob that %s', (_, loadError) => {
    const { load } = fakeWebpack({
      '/app': ['@daytona/sdk/package.json', 'fast-glob'],
      '/app/node_modules/@daytona/sdk': ['fast-glob'],
    })
    load.mockImplementation(() => {
      throw loadError
    })

    expect(() => dynamicRequire('fast-glob')).toThrow(loadError.message)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('falls through when an SDK install does not export its package.json', () => {
    const { fastGlob, load } = fakeWebpack({
      '/app': [
        [
          '@daytona/sdk/package.json',
          Object.assign(new Error('not exported'), { code: 'ERR_PACKAGE_PATH_NOT_EXPORTED' }),
        ],
        '@daytonaio/sdk/package.json',
      ],
      '/app/node_modules/@daytonaio/sdk': ['fast-glob'],
    })

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(load).toHaveBeenCalledWith('/app/node_modules/@daytonaio/sdk/node_modules/fast-glob/out/index.js')
  })

  it('propagates an SDK install resolution error that is not a miss', () => {
    const { bundleResolve } = fakeWebpack({
      '/app': ['@daytona/sdk/package.json', 'fast-glob'],
      '/app/node_modules/@daytona/sdk': [
        ['fast-glob', Object.assign(new Error('invalid package config'), { code: 'ERR_INVALID_PACKAGE_CONFIG' })],
      ],
    })

    expect(() => dynamicRequire('fast-glob')).toThrow(/invalid package config$/)
    expect(bundleResolve).not.toHaveBeenCalledWith('fast-glob')
  })

  it('propagates a bundle resolution error that is not a miss, without the install hint', () => {
    fakeWebpack({
      '/app': [
        ['fast-glob', Object.assign(new Error('invalid package config'), { code: 'ERR_INVALID_PACKAGE_CONFIG' })],
      ],
    })

    expect(() => dynamicRequire('fast-glob')).toThrow(/invalid package config$/)
  })
})
