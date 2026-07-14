// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import axios from 'axios'
import { createServer, type Server } from 'node:http'
import {
  Configuration,
  SandboxApi,
  SnapshotsApi,
  SandboxState,
  SnapshotState,
  type Sandbox as SandboxDto,
} from '@daytona/api-client'
import { Sandbox } from '../Sandbox'

const sandboxDto = (toolboxProxyUrl: string): SandboxDto => ({
  id: 'sb-1',
  name: 'sandbox-one',
  organizationId: 'org-1',
  user: 'daytona',
  env: {},
  labels: {},
  public: false,
  target: 'eu',
  cpu: 2,
  gpu: 0,
  memory: 4,
  disk: 10,
  state: SandboxState.STOPPED,
  networkBlockAll: false,
  toolboxProxyUrl,
})

describe('Sandbox snapshot polling base URL', () => {
  let server: Server
  let origin: string
  let requests: string[]

  beforeEach(async () => {
    requests = []
    server = createServer((request, response) => {
      const path = request.url ?? ''
      requests.push(`${request.method} ${path}`)
      response.setHeader('content-type', 'application/json')

      if (request.method === 'POST' && path === '/api/sandbox/sb-1/snapshot') {
        response.statusCode = 202
        response.end(JSON.stringify({ id: 'snapshot-id', name: 'snap-1', state: SnapshotState.CAPTURING }))
        return
      }

      if (request.method === 'GET' && path === '/api/snapshots/snapshot-id') {
        response.end(JSON.stringify({ id: 'snapshot-id', name: 'snap-1', state: SnapshotState.ACTIVE }))
        return
      }

      if (request.method === 'GET' && path === '/api/sandbox/sb-1') {
        response.end(JSON.stringify(sandboxDto(`${origin}/toolbox`)))
        return
      }

      response.statusCode = 404
      response.end(JSON.stringify({ message: 'page not found' }))
    })

    const { promise, resolve, reject } = Promise.withResolvers<void>()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
    await promise

    const address = server.address()
    if (!address || typeof address === 'string') {
      throw new Error('Mock server did not bind to a TCP port')
    }
    origin = `http://127.0.0.1:${address.port}`
  })

  afterEach(async () => {
    const { promise, resolve, reject } = Promise.withResolvers<void>()
    server.close((error) => (error ? reject(error) : resolve()))
    await promise
  })

  it('keeps snapshot polling on the main API after toolbox initialization', async () => {
    const mainConfiguration = new Configuration({ basePath: `${origin}/api` })
    const sandboxApi = new SandboxApi(mainConfiguration, '', axios.create())
    const snapshotsApi = new SnapshotsApi(mainConfiguration, '', axios.create())
    const sandboxConfiguration = new Configuration(structuredClone(mainConfiguration))
    const sandbox = new Sandbox(
      sandboxDto(`${origin}/toolbox`),
      sandboxConfiguration,
      axios.create(),
      sandboxApi,
      snapshotsApi,
      async () => undefined,
    )

    await sandbox._experimental_createSnapshot('snap-1', 1)

    expect(requests).toContain('POST /api/sandbox/sb-1/snapshot')
    expect(requests).toContain('GET /api/snapshots/snapshot-id')
    expect(requests).not.toContain('GET /toolbox/sb-1/snapshots/snapshot-id')
  })
})
