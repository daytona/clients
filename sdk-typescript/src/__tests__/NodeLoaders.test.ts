/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { dynamicImport, dynamicRequire } from '../utils/Import'
import { nodeLoaders } from '../utils/NodeLoaders'

const settle = () => new Promise((resolve) => setImmediate(resolve))

describe('Node loaders', () => {
  beforeAll(settle)

  it.each(['fs', 'stream', 'buffer', 'busboy', 'expand-tilde', '@iarna/toml', 'tinyglobby'] as const)(
    'loads %s synchronously through its Node loader and passes validation',
    (name) => {
      const loader = nodeLoaders[name]
      expect(loader).toBeDefined()
      expect(() => loader?.()).not.toThrow()
      expect(dynamicRequire(name)).toBeDefined()
    },
  )

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
