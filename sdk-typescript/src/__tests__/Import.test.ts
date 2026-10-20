// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { dynamicImport, dynamicRequire } from '../utils/Import'

type WebpackGlobals = typeof globalThis & { __webpack_require__?: unknown; __non_webpack_require__?: unknown }

describe('fast-glob loading', () => {
  const g = globalThis as WebpackGlobals
  const fastGlob = Object.assign(() => [], { sync: () => [] })

  afterEach(() => {
    delete g.__webpack_require__
    delete g.__non_webpack_require__
  })

  const fakeWebpack = (resolveSdkManifest: (id: string) => string) => {
    const sdkRequire = jest.fn(() => fastGlob)
    const createRequire = jest.fn(() => sdkRequire)
    const runtimeRequire = Object.assign(
      jest.fn((id: string) => (id === 'module' ? { createRequire } : fastGlob)),
      { resolve: jest.fn(resolveSdkManifest) },
    )
    g.__webpack_require__ = () => undefined
    g.__non_webpack_require__ = runtimeRequire
    return { runtimeRequire, createRequire, sdkRequire }
  }

  it('loads the real fast-glob outside webpack', async () => {
    expect(typeof dynamicRequire('fast-glob').sync).toBe('function')
    expect(typeof (await dynamicImport('fast-glob')).sync).toBe('function')
  })

  it('resolves fast-glob from the SDK install inside a webpack bundle', async () => {
    const { runtimeRequire, createRequire, sdkRequire } = fakeWebpack((id) => `/app/node_modules/${id}`)

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(await dynamicImport('fast-glob')).toBe(fastGlob)
    expect(createRequire).toHaveBeenCalledWith('/app/node_modules/@daytona/sdk/package.json')
    expect(sdkRequire).toHaveBeenCalledWith('fast-glob')
    expect(runtimeRequire).not.toHaveBeenCalledWith('fast-glob')
  })

  it('falls back to the legacy package name, then to the bundle require', () => {
    const legacy = fakeWebpack((id) => {
      if (id.startsWith('@daytonaio/sdk/')) return `/app/node_modules/${id}`
      throw new Error('not found')
    })
    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(legacy.createRequire).toHaveBeenCalledWith('/app/node_modules/@daytonaio/sdk/package.json')
    expect(legacy.sdkRequire).toHaveBeenCalledWith('fast-glob')
    expect(legacy.runtimeRequire).not.toHaveBeenCalledWith('fast-glob')

    const unresolvable = fakeWebpack(() => {
      throw new Error('not found')
    })
    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(unresolvable.createRequire).not.toHaveBeenCalled()
    expect(unresolvable.runtimeRequire).toHaveBeenCalledWith('fast-glob')
  })

  it('tells a bundled app to install the SDK when fast-glob is missing', () => {
    const { runtimeRequire, createRequire } = fakeWebpack(() => {
      throw new Error('not found')
    })
    runtimeRequire.mockImplementation((id: string) => {
      if (id === 'module') return { createRequire }
      throw new Error(`Cannot find module '${id}'\nRequire stack:\n- /app/main.js`)
    })

    expect(() => dynamicRequire('fast-glob')).toThrow(
      /Cannot find module 'fast-glob'\. This app was bundled with webpack or Rspack.*@daytona\/sdk installed$/,
    )
    expect(() => dynamicRequire('fast-glob')).not.toThrow(/Require stack/)
  })

  it('falls back to the bundle require when the SDK install cannot load fast-glob', () => {
    const { runtimeRequire, sdkRequire } = fakeWebpack((id) => `/app/node_modules/${id}`)
    sdkRequire.mockImplementation(() => {
      throw new Error('not found')
    })

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(sdkRequire).toHaveBeenCalledTimes(2)
    expect(runtimeRequire).toHaveBeenCalledWith('fast-glob')
  })
})
