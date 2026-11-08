/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { inspect } from 'node:util'
import { Daytona } from '../src/Daytona'
import { ImagesService } from '../src/v2/Images'
import { MacModelsService } from '../src/v2/MacModels'
import { PortsService } from '../src/v2/Ports'
import { ReservationsService } from '../src/v2/Reservations'
import { Sandbox } from '../src/v2/Sandbox'
import { SecretService } from '../src/v2/Secret'
import { SnapshotsService } from '../src/v2/Snapshots'
import { WaitHub } from '../src/v2/events'
import { createHttpClient, createPlatformTokenProvider, HttpTransport } from '../src/v2/http'

it.each(['DAYTONA_V2_API_URL'])('omits credential-bearing URLs from diagnostic views when configured via %s', async (variable) => {
  // Given a configured endpoint containing userinfo, a query credential and a fragment.
  const url = 'https://private-user:private-password@api.invalid/path?token=private-query#private-fragment'
  const previous = { ...process.env }
  delete process.env.DAYTONA_V2_API_URL
  process.env[variable] = url
  const root = new Daytona({ apiKey: 'key' })
  const client = createHttpClient(url)
  const transport = new HttpTransport(url)
  const provider = createPlatformTokenProvider({ baseURL: url, jwtToken: 'jwt', projectId: 'project' })
  const sandbox = new Sandbox('sandbox', client, undefined, 'key', undefined, { toolboxUrl: url })
  const owners = [
    root, Reflect.get(root, 'config'), root.v2,
    client, client.defaults, client.defaults.headers, transport, provider,
    ...Object.getOwnPropertySymbols(client).map(symbol => Reflect.get(client, symbol)),
    sandbox, new ImagesService(client), new SnapshotsService(client), new ReservationsService(client),
    new MacModelsService(client), new SecretService(client), new PortsService(transport, client, 'sandbox'),
    new WaitHub(url, 'key', {}, () => { throw new Error('unexpected socket') }, client),
  ]
  try {
    // When ordinary or nested diagnostics are rendered.
    const output = [inspect(owners, { depth: null, showHidden: true }), JSON.stringify(owners)].join('\n')
    // Then none of the URL's credential-bearing components are disclosed.
    for (const secret of ['private-user', 'private-password', 'private-query', 'private-fragment']) {
      expect(output).not.toContain(secret)
    }
    expect(client.defaults.baseURL).toBe(url)
  } finally {
    await root.close()
    process.env = previous
  }
})

it('omits credential-bearing URLs from issued-value inspection but preserves explicit JSON', async () => {
  // Given server-issued values with a credential-bearing preview URL.
  const url = 'https://private-user:private-password@preview.invalid/path?token=private-query#private-fragment'
  const client = createHttpClient('https://api.invalid')
  client.defaults.adapter = async (config) => ({
    config, status: 200, statusText: 'OK', headers: {},
    data: config.url?.endsWith('/access-token')
      ? { access_token: 'issued-token', expires_at: '2099-01-01T00:00:00Z' }
      : { url, token: 'issued-token', port: 8080, public: false },
  })
  const sandbox = new Sandbox('sandbox', client, undefined, 'key')
  // When obtaining the credential-return DTOs.
  const values = await Promise.all([
    sandbox.createAccessToken(), sandbox.ports.previewLink(8080), sandbox.ports.signedPreviewLink(8080),
  ])
  // Then diagnostics omit URLs while the explicitly serializable values retain them.
  const output = inspect(values, { depth: null, showHidden: true })
  for (const secret of ['private-user', 'private-password', 'private-query', 'private-fragment']) {
    expect(output).not.toContain(secret)
  }
  expect(values[1].url).toBe(url)
  expect(values[2].url).toBe(url)
  expect(JSON.stringify(values)).toContain(url)
})
