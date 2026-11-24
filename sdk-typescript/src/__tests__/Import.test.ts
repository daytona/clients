/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const mockNodeLoaders: Partial<Record<string, () => unknown>> = {}

jest.mock('../utils/NodeLoaders', () => ({ nodeLoaders: mockNodeLoaders }))

import { dynamicImport, dynamicRequire } from '../utils/Import'

describe('Import with Node loaders', () => {
  afterEach(() => {
    for (const name of Object.keys(mockNodeLoaders)) delete mockNodeLoaders[name]
  })

  it('loads a module through its Node loader', async () => {
    const fakeExpandTilde = jest.fn((p: string) => p)
    mockNodeLoaders['expand-tilde'] = () => fakeExpandTilde

    expect(dynamicRequire('expand-tilde')).toBe(fakeExpandTilde)
    expect(await dynamicImport('expand-tilde')).toBe(fakeExpandTilde)
  })

  it('falls back to the general loader when the Node loader fails', async () => {
    mockNodeLoaders.fs = () => {
      throw new TypeError('The argument must be a file URL. Received undefined')
    }

    expect(dynamicRequire('fs')).toBe(require('fs'))
    expect(await dynamicImport('fs')).toBe(require('fs'))
  })

  it('falls back to the general loader when there is no Node loader', () => {
    expect(dynamicRequire('@iarna/toml')).toBe(require('@iarna/toml'))
  })

  it("reports the Node loader's error when both loaders fail", () => {
    mockNodeLoaders.busboy = () => {
      throw new Error("Cannot find module 'busboy' from the Node loader")
    }
    jest.doMock('busboy', () => {
      throw new Error('fallback failed')
    })

    try {
      expect(() => dynamicRequire('busboy', 'prefix:')).toThrow(
        /prefix: Module "busboy" is not available in the "node" runtime: Cannot find module 'busboy' from the Node loader/,
      )
    } finally {
      jest.dontMock('busboy')
    }
  })
})
