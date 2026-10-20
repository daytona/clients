// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { dynamicImport, dynamicRequire } from '../utils/Import'

type WebpackGlobals = typeof globalThis & { __webpack_require__?: unknown; __non_webpack_require__?: unknown }

describe('fast-glob loading', () => {
  const g = globalThis as WebpackGlobals

  afterEach(() => {
    delete g.__webpack_require__
    delete g.__non_webpack_require__
  })

  it('loads the real fast-glob outside webpack', async () => {
    expect(typeof dynamicRequire('fast-glob').sync).toBe('function')
    expect(typeof (await dynamicImport('fast-glob')).sync).toBe('function')
  })

  it('loads fast-glob through the runtime require inside a webpack bundle', async () => {
    const fastGlob = Object.assign(() => [], { sync: () => [] })
    const runtimeRequire = jest.fn(() => fastGlob)
    g.__webpack_require__ = () => undefined
    g.__non_webpack_require__ = runtimeRequire

    expect(dynamicRequire('fast-glob')).toBe(fastGlob)
    expect(await dynamicImport('fast-glob')).toBe(fastGlob)
    expect(runtimeRequire).toHaveBeenCalledWith('fast-glob')
  })
})
