/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios, { type AxiosInstance } from 'axios'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { HttpTransport } from '../src/v2/http.js'
import { VolumesService } from '../src/v2/Volume.js'
import { SecretsService } from '../src/v2/Secret.js'
import { ReservationsService } from '../src/v2/Reservations.js'
import { MacModelsService } from '../src/v2/MacModels.js'
import { PortsService } from '../src/v2/Ports.js'
import { DockerService } from '../src/v2/Docker.js'
import { SandboxVolumesService } from '../src/v2/SandboxVolumes.js'
import { INTERNAL } from '../src/v2/internal'

const extra = { futureOption: true }
const cases: readonly { readonly name: string; readonly run: (http: AxiosInstance) => Promise<object | void> }[] = [
  { name: 'volume get', run: (http) => new VolumesService(INTERNAL, http).get('volume', { ...extra, create: true }) },
  { name: 'volume create', run: (http) => new VolumesService(INTERNAL, http).create({ ...extra, name: 'data' }) },
  { name: 'volume delete', run: (http) => new VolumesService(INTERNAL, http).delete('volume', { ...extra, idOnly: true }) },
  { name: 'volume list', run: (http) => new VolumesService(INTERNAL, http).list({ ...extra, limit: 1 }) },
  { name: 'volume iter', run: (http) => new VolumesService(INTERNAL, http).iter({ ...extra, limit: 1 })[Symbol.asyncIterator]().next() },
  { name: 'secret list', run: (http) => new SecretsService(INTERNAL, http).list({ ...extra, limit: 1 }) },
  { name: 'secret iter', run: (http) => new SecretsService(INTERNAL, http).iter({ ...extra, limit: 1 })[Symbol.asyncIterator]().next() },
  { name: 'secret set', run: (http) => new SecretsService(INTERNAL, http).set('TOKEN', 'value', { ...extra, allowedDomains: ['example.com'] }) },
  { name: 'reservation create', run: (http) => new ReservationsService(INTERNAL, http).create({ ...extra, region: 'region-a' }) },
  { name: 'reservation bulk create', run: (http) => new ReservationsService(INTERNAL, http).createBulk({ ...extra, region: 'region-a', count: 1 }) },
  { name: 'reservation list', run: (http) => new ReservationsService(INTERNAL, http).list({ ...extra, limit: 1 }) },
  { name: 'reservation iter', run: (http) => new ReservationsService(INTERNAL, http).iter({ ...extra, limit: 1 })[Symbol.asyncIterator]().next() },
  { name: 'reservation renew', run: (http) => new ReservationsService(INTERNAL, http).renew('reservation', { ...extra, idempotencyKey: 'key' }) },
  { name: 'reservation update', run: (http) => new ReservationsService(INTERNAL, http).update('reservation', { ...extra, autoRenew: false }) },
  { name: 'Mac catalog', run: (http) => new MacModelsService(INTERNAL, http).catalog({ ...extra, region: 'region-a' }) },
  { name: 'port expose', run: (http) => new PortsService(INTERNAL, undefined, http, 'sandbox').expose(8080, { ...extra, public: false }) },
  { name: 'signed preview link', run: (http) => new PortsService(INTERNAL, undefined, http, 'sandbox').signedPreviewLink(8080, { ...extra, expiresInSeconds: 60 }) },
  { name: 'endpoint list', run: (http) => new PortsService(INTERNAL, undefined, http, 'sandbox').listEndpoints({ ...extra, limit: 1 }) },
  { name: 'endpoint iter', run: (http) => new PortsService(INTERNAL, undefined, http, 'sandbox').iterEndpoints({ ...extra, limit: 1 })[Symbol.asyncIterator]().next() },
]

describe('platform option validation', () => {
  it.each(cases)('rejects unknown options before HTTP when calling $name', async ({ run }) => {
    // Given
    let requests = 0
    const http = axios.create({ adapter: async (config) => {
      requests += 1
      return { config, status: 200, statusText: 'OK', headers: {}, data: {} }
    } })
    // When
    const result = run(http)
    // Then
    await expect(result).rejects.toBeInstanceOf(BadRequestError)
    await expect(result).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK', statusCode: 400 })
    expect(requests).toBe(0)
  })

  it.each([
    ['Docker wait', (transport: HttpTransport) => new DockerService(INTERNAL, transport).waitUntilReady({ ...extra, timeoutSeconds: 1 })],
    ['volume pull', (transport: HttpTransport) => new SandboxVolumesService(INTERNAL, transport).pull('volume', { ...extra, requestId: 'request' })],
  ] as const)('rejects unknown keys before HTTP when calling %s', async (_name, run) => {
    // Given
    let requests = 0
    const transport = new HttpTransport('http://toolbox.invalid')
    transport.http.defaults.adapter = async (config) => {
      requests += 1
      return { config, status: 200, statusText: 'OK', headers: {}, data: {} }
    }
    // When
    const result = run(transport)
    // Then
    await expect(result).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toBe(0)
  })

  it.each([
    ['Docker wait', (transport: HttpTransport) => new DockerService(INTERNAL, transport).waitUntilReady({ ...{ timeout: 1000 }, timeoutSeconds: 1 })],
    ['volume pull', (transport: HttpTransport) => new SandboxVolumesService(INTERNAL, transport).pull('volume', { requestId: 'request', ...{ timeout: 1000 } })],
  ] as const)('provides the seconds rename hint when %s receives the old timeout key', async (_name, run) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    // When
    const result = run(transport)
    // Then
    await expect(result).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK',
      message: expect.stringContaining('`timeout` was renamed to `timeoutSeconds` (seconds)') })
  })
})
