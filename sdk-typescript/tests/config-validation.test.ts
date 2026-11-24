/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Client configuration checked before any request: boolean options are
// booleans, and get()'s toolbox URL override is an http(s) URL. The Python
// twin is sdk-python/tests/test_config_validation.py.

import { Daytona, setV0ModuleLoaderForTests } from '../src/Daytona'
import type { DaytonaConfig } from '../src/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { PlannedResponse } from './networkPolicyTestServer'

const BAD_REQUEST = { name: 'BadRequestError', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' }

describe('boolean config options', () => {
  // SAFETY: every `as never` is a deliberately invalid plain-JavaScript value.
  it.each([
    ['sharedHub', { sharedHub: 'no' as never }, 'sharedHub must be a boolean'],
    ['sharedHub', { sharedHub: 0 as never }, 'sharedHub must be a boolean'],
    ['otelEnabled', { otelEnabled: 'yes' as never }, 'otelEnabled must be a boolean'],
    ['otelEnabled', { otelEnabled: 1 as never }, 'otelEnabled must be a boolean'],
    ['events.enabled', { events: { enabled: 'no' as never } }, 'events.enabled must be a boolean'],
  ] as const)('refuses a non-boolean %s when the v2 client is built', (_name, config, message) => {
    const root = new Daytona({ apiKey: 'key', ...config } as DaytonaConfig)
    expect(() => root.v2).toThrow(expect.objectContaining({ ...BAD_REQUEST, message }) as Error)
  })

  it.each([true, false])('accepts %s for every boolean option', async (value) => {
    const root = new Daytona({ apiKey: 'key', sharedHub: value, otelEnabled: false, events: { enabled: value } })
    expect(root.v2).toBeDefined()
    await root.close()
  })

  it('refuses a non-boolean otelEnabled before the v0 client is loaded', () => {
    const loader = jest.fn(() => ({}))
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key', otelEnabled: 'yes' as never }), loader)
    expect(() => root.v0).toThrow(expect.objectContaining({ ...BAD_REQUEST, message: 'otelEnabled must be a boolean' }) as Error)
    expect(loader).not.toHaveBeenCalled()
  })
})

describe('get() toolbox URL override', () => {
  const URL_MESSAGE = 'toolboxUrl must be an http(s) URL with a host and no query or fragment'

  it.each([
    ['an unsupported scheme', 'ftp://toolbox.example/sbx', URL_MESSAGE],
    ['a relative URL', 'toolbox.example/sbx', URL_MESSAGE],
    ['text that is no URL', 'not a url', URL_MESSAGE],
    ['a URL without a host', 'https://', URL_MESSAGE],
    ['a scheme without slashes', 'http:toolbox.example', URL_MESSAGE],
    ['a query', 'https://toolbox.example/sbx?x=1', URL_MESSAGE],
    ['an empty query', 'https://toolbox.example/sbx?', URL_MESSAGE],
    ['a fragment', 'https://toolbox.example/sbx#top', URL_MESSAGE],
    ['an invalid port', 'https://toolbox.example:99999/sbx', URL_MESSAGE],
    ['surrounding whitespace', ' https://toolbox.example/sbx', URL_MESSAGE],
    ['whitespace in the path', 'https://toolbox.example/s bx', URL_MESSAGE],
    ['an empty authority', 'https:///toolbox.example/sbx', URL_MESSAGE],
    ['a backslash in the authority', 'https://toolbox.example\\sbx', URL_MESSAGE],
    ['a percent-encoded host', 'http://%41.toolbox.example/sbx', URL_MESSAGE],
    ['a placeholder in the host', 'https://{sandboxId}.toolbox.example/', 'the {sandboxId} placeholder in a toolbox URL is only supported in the path'],
  ] as const)('refuses %s before any request', async (_name, toolboxUrl, message) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      const error = await client.v2.get('sbx', { toolboxUrl }).then(() => undefined, (caught: unknown) => caught)
      expect(error).toMatchObject(BAD_REQUEST)
      expect((error as Error).message).toContain(message)
      expect(api.requests).toHaveLength(0)
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each([
    ['an https URL', 'https://toolbox.example/sandboxes/sbx'],
    ['an upper-case scheme and a port', 'HTTP://127.0.0.1:8080/'],
    ['an IPv6 host', 'http://[::1]:8080/sbx'],
    ['userinfo', 'https://user@toolbox.example/sbx'],
    ['a path placeholder', 'https://toolbox.example/{sandboxId}/tools'],
    ['the snake_case placeholder', 'https://toolbox.example/{sandbox_id}'],
  ] as const)('accepts %s', async (_name, toolboxUrl) => {
    const planned: PlannedResponse[] = []
    const api = await startFakeApi(planned)
    planned.push({ status: 200, body: { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', toolbox_proxy_url: `${api.url}/toolbox` } })
    const client = clientAt(api.url)
    try {
      const sandbox = await client.v2.get('sbx', { toolboxUrl })
      expect(sandbox.id).toBe('sbx')
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  })
})
