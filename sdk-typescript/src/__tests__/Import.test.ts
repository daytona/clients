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

  function unusableModuleUrlError(): Error {
    // What createRequire(import.meta.url) throws once an ESM build is re-bundled to CommonJS.
    return Object.assign(
      new TypeError(
        "The argument 'filename' must be a file URL object, file URL string, or absolute path string. Received undefined",
      ),
      { code: 'ERR_INVALID_ARG_VALUE' },
    )
  }

  function withMissingTar(run: () => Promise<void>): Promise<void> {
    jest.doMock('tar', () => {
      throw new Error("Cannot find module 'tar'")
    })
    return run().finally(() => jest.dontMock('tar'))
  }

  it("reports the fallback's error from both helpers when the Node loader could not run", () =>
    withMissingTar(async () => {
      mockNodeLoaders.tar = () => {
        throw unusableModuleUrlError()
      }

      expect(() => dynamicRequire('tar', 'prefix:')).toThrow(/prefix: Module "tar" .*: Cannot find module 'tar'$/)
      await expect(dynamicImport('tar', 'prefix:')).rejects.toThrow(
        /prefix: Module "tar" .*: Cannot find module 'tar'$/,
      )
    }))

  it("reports the Node loader's error from both helpers when it ran and failed", () =>
    withMissingTar(async () => {
      mockNodeLoaders.tar = () => {
        throw new Error("Cannot find module 'tar' (Node loader)")
      }

      expect(() => dynamicRequire('tar')).toThrow(/Cannot find module 'tar' \(Node loader\)$/)
      await expect(dynamicImport('tar')).rejects.toThrow(/Cannot find module 'tar' \(Node loader\)$/)
    }))

  it('falls back when an async Node loader rejects', async () => {
    mockNodeLoaders.fs = () => Promise.reject(unusableModuleUrlError())

    expect(await dynamicImport('fs')).toBe(require('fs'))
  })

  it('applies the same policy when an async Node loader and the async fallback both reject', () =>
    withMissingTar(async () => {
      mockNodeLoaders.tar = () => Promise.reject(new Error('Node loader rejected'))
      await expect(dynamicImport('tar')).rejects.toThrow(/Node loader rejected$/)

      mockNodeLoaders.tar = () => Promise.reject(unusableModuleUrlError())
      await expect(dynamicImport('tar')).rejects.toThrow(/Cannot find module 'tar'$/)
    }))
})
