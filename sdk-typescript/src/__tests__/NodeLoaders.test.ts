/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { dynamicImport, dynamicRequire } from '../utils/Import'
import { nodeLoaders } from '../utils/NodeLoaders'

describe('Node loaders', () => {
  it.each(['fs', 'stream', 'buffer', 'busboy', 'expand-tilde', '@iarna/toml', 'tinyglobby'] as const)(
    'loads %s synchronously through its Node loader and passes validation',
    (name) => {
      const loader = nodeLoaders[name]
      expect(loader).toBeDefined()
      expect(() => loader?.()).not.toThrow()
      expect(dynamicRequire(name)).toBeDefined()
    },
  )

  // A COPY glob resolved while the app's modules are still evaluating must not depend on the
  // background import having settled.
  it('loads tinyglobby synchronously right after the SDK is imported', () => {
    jest.isolateModules(() => {
      const fresh = require('../utils/NodeLoaders') as typeof import('../utils/NodeLoaders')
      const mod = fresh.nodeLoaders.tinyglobby?.() as { globSync?: unknown }
      expect(typeof mod.globSync).toBe('function')
    })
  })

  // dynamicRequire is synchronous, so it must never return an asynchronous loader's promise.
  it.each(['tar', 'form-data'] as const)('returns %s itself from dynamicRequire, not a promise', (name) => {
    const mod = dynamicRequire(name)
    expect(typeof (mod as { then?: unknown }).then).not.toBe('function')
    expect(mod).toBeDefined()
  })

  it('returns the functions that expand-tilde and busboy export', () => {
    expect(typeof dynamicRequire('expand-tilde')).toBe('function')
    expect(typeof dynamicRequire('busboy')).toBe('function')
    expect(dynamicRequire('expand-tilde')('~/x')).not.toContain('~')
  })

  it.each(['fs', 'stream', 'util', 'tar', 'form-data', 'ObjectStorage'] as const)(
    'loads %s through dynamicImport and passes validation',
    async (name) => {
      await expect(dynamicImport(name)).resolves.toBeDefined()
    },
  )

  it('returns the FormData class from form-data', async () => {
    const FormData = await dynamicImport('form-data')
    expect(typeof FormData).toBe('function')
    expect(new (FormData as any)().getBoundary()).toEqual(expect.any(String))
  })

  it('returns the ObjectStorage class', async () => {
    expect(typeof (await dynamicImport('ObjectStorage')).ObjectStorage).toBe('function')
  })
})
