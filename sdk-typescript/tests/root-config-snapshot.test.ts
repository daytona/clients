/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'

import { Daytona, resetTargetDeprecationWarningForTests } from '../src/Daytona'
import type { DaytonaConfig } from '../src/types'
import { BadRequestError } from '../src/v2/errors/DaytonaError'

afterEach(() => jest.restoreAllMocks())

describe('root config snapshot', () => {
  it.each([
    ['get-only proxy', () => new Proxy<DaytonaConfig>({}, {
      has: () => false,
      get: (_target, property) => property === 'apiKey' ? 'explicit-key' : undefined,
    })],
    ['getter-defined object', () => ({ get apiKey(): string { return 'explicit-key' } })],
  ] satisfies readonly (readonly [string, () => DaytonaConfig])[])(
    'sends the explicit credential when config is a %s',
    async (_label, config) => {
      // Given: a local API and competing ambient credentials and API URL.
      const seen: { readonly authorization: string | undefined; readonly url: string | undefined }[] = []
      const server = createServer((request, response) => {
        seen.push({ authorization: request.headers.authorization, url: request.url })
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq' }))
      })
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      const address = server.address()
      if (address === null || typeof address === 'string') throw new Error('expected TCP address')
      jest.replaceProperty(process, 'env', {
        ...process.env,
        DAYTONA_API_KEY: 'ambient-key',
        DAYTONA_API_URL: 'http://ambient.invalid',
        DAYTONA_V2_API_URL: `http://127.0.0.1:${address.port}`,
      })
      const root = new Daytona(config())
      try {
        // When: a real SDK request passes through the root snapshot.
        await root.v2.list()

        // Then: the selected API receives the explicit credential, not the ambient one.
        expect(seen).toEqual([{ authorization: 'Bearer explicit-key', url: '/sandboxes/search' }])
      } finally {
        await root.close()
        await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
      }
    },
  )

  it.each(['apiUrl', 'serverUrl', 'toolboxUrl'])('refuses %s exposed only through a Proxy get trap', (field) => {
    // Given: an unsupported URL field that `in` cannot see.
    const config = new Proxy<DaytonaConfig>({}, {
      has: () => false,
      get: (_target, property) => property === field ? 'https://custom.example' : undefined,
    })
    // When / Then: the root still refuses the custom endpoint.
    expect(() => new Daytona(config)).toThrow(BadRequestError)
  })

  it.each(['apiUrl', 'serverUrl', 'toolboxUrl'])('refuses %s when a Proxy exposes it after a credential read', (field) => {
    // Given: reading credentials reveals a URL that the has trap never exposes.
    let credentialRead = false
    const config = new Proxy<DaytonaConfig>({}, {
      has: () => false,
      get: (_target, property) => {
        if (property === 'apiKey') {
          credentialRead = true
          return 'explicit-key'
        }
        return credentialRead && property === field ? 'https://custom.example' : undefined
      },
    })

    // When / Then: construction refuses the URL before building a namespace client.
    expect(() => new Daytona(config)).toThrow(BadRequestError)
  })

  it('reads target once when deciding whether to warn about its snapshotted value', () => {
    // Given: a target getter whose second read would disagree with the snapshot.
    resetTargetDeprecationWarningForTests()
    const emit = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    let reads = 0
    const config: DaytonaConfig = {
      get target(): string | undefined {
        reads++
        return reads === 1 ? 'eu' : undefined
      },
    }

    try {
      // When: the root snapshots config and decides whether target is deprecated.
      new Daytona(config)

      // Then: the getter ran once and the snapshot's defined target triggered a warning.
      expect(reads).toBe(1)
      expect(emit).toHaveBeenCalledTimes(1)
    } finally {
      resetTargetDeprecationWarningForTests()
    }
  })
})
