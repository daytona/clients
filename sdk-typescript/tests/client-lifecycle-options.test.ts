/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { jsonBody, startFakeApi } from './networkPolicyTestServer'
import type { CreateSandboxOptions, SandboxState } from '../src/v2/index'

describe('client options boundaries', () => {
  it.each(['cpu', 'memory', 'disk', 'envVars', 'unexpected'])('rejects %s when a Linux create receives an obsolete key', async (key) => {
    // Given
    const client = clientAt('http://api.invalid')
    const options: CreateSandboxOptions = Object.assign({ waitSeconds: 0 }, { [key]: 4 })
    // When
    const pending = client.v2.create(options)
    // Then
    await expect(pending).rejects.toBeInstanceOf(BadRequestError)
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await client.close()
  })

  it.each(['resources', 'cpuArch', 'cpuType', 'env', 'volumes', 'secrets', 'networkPolicy'])('rejects %s when creating from a snapshot', async (key) => {
    // Given
    const client = clientAt('http://api.invalid')
    // When
    const pending = client.v2.createFromSnapshot('snap_source', Object.assign({ waitSeconds: 0 }, { [key]: {} }))
    // Then
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await client.close()
  })

  it.each([
    { resources: { memory: 0.1 } }, { resources: { memoryMax: 0.1 } },
    { resources: { disk: 0.1 } }, { resources: { diskMax: 0.1 } },
    { resources: { cpu: 1, unexpected: 1 } }, { ports: [{ port: 80, unexpected: true }] },
    { volumes: [{ volumeId: 'vol', mountPath: '/data', unexpected: true }] },
    { lifecyclePolicy: { timeoutSeconds: 60, unexpected: true } },
  ])('rejects invalid nested options when creating: %j', async (extra) => {
    // Given
    const client = clientAt('http://api.invalid')
    // When
    const pending = client.v2.create(Object.assign({ waitSeconds: 0 }, extra))
    // Then
    await expect(pending).rejects.toBeInstanceOf(BadRequestError)
    await client.close()
  })

  it('preserves exact MiB values when creating with fractional GiB', async () => {
    // Given
    const api = await startFakeApi([{ status: 202, body: { id: 'sbx', class: 'linux', status: 'queued', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const client = clientAt(api.url)
    try {
      // When
      await client.v2.create({ resources: { cpu: 0.5, memory: 1 / 1024, memoryMax: 4.5, disk: 1.5, diskMax: 2.5 }, waitSeconds: 0 })
      // Then
      expect(api.requests.map(jsonBody)).toEqual([expect.objectContaining({ cpu: 0.5, memory_mib: 1, memory_max_mib: 4608, disk_mib: 1536, disk_max_mib: 2560 })])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['create', 'createFromSnapshot', 'createMacos', 'get'] as const)('keeps a sandbox when %s did not opt into disposal', async (route) => {
    // Given
    const api = await startFakeApi([{ status: 200, body: { id: 'sbx', class: 'linux', status: 'running', toolbox_proxy_url: 'http://toolbox.invalid', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create(),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source'),
        createMacos: () => client.v2.createMacos('macos'),
        get: () => client.v2.get('sbx'),
      }
      const sandbox = await acquire[route]()
      // When
      await sandbox[Symbol.asyncDispose]()
      // Then
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['create', 'createFromSnapshot', 'createMacos', 'get'] as const)('deletes a sandbox when %s opts into disposal', async (route) => {
    // Given
    const api = await startFakeApi([
      { status: 200, body: { id: 'sbx', class: 'linux', status: 'running', toolbox_proxy_url: 'http://toolbox.invalid', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
      { status: 202, body: {} },
      { status: 404, body: { code: 'NOT_FOUND', source: 'DAYTONA_API', message: 'not found' } },
    ])
    const client = clientAt(api.url)
    try {
      const acquire = {
        create: () => client.v2.create({ autoDelete: true }),
        createFromSnapshot: () => client.v2.createFromSnapshot('snap_source', { autoDelete: true }),
        createMacos: () => client.v2.createMacos('macos', { autoDelete: true }),
        get: () => client.v2.get('sbx', { autoDelete: true }),
      }
      const sandbox = await acquire[route]()
      // When
      await sandbox[Symbol.asyncDispose]()
      // Then
      expect(api.requests.slice(1).map(({ method, path }) => [method, path.split('?')[0]])).toEqual([
        ['DELETE', '/sandboxes/sbx'], ['GET', '/sandboxes/sbx'],
      ])
      expect(sandbox.state).toBe('destroyed')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('names the build budget when createFromBuild receives the old timeout option', async () => {
    // Given
    const client = clientAt('http://api.invalid')
    const options = Object.assign({ waitSeconds: 0 }, { timeout: 1 })
    // When
    const pending = client.v2.createFromBuild(ImageBuilder.fromRegistry('python'), options)
    // Then
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await expect(pending).rejects.toThrow('buildTimeoutSeconds')
    await client.close()
  })
})

describe('sandbox search states', () => {
  it('expands and deduplicates states when searching', async () => {
    // Given
    const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: 0, total_relation: 'eq' } }])
    const client = clientAt(api.url)
    try {
      // When
      await client.v2.search({ states: ['creating', 'error', 'creating', 'started'] })
      // Then
      expect(new URL(api.requests[0]?.path ?? '', api.url).searchParams.get('status')).toBe('pending,assigned,failed,lost,running')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each<SandboxState | readonly SandboxState[]>(['unknown', []])('refuses an unsearchable state when given %j', async (states) => {
    // Given
    const client = clientAt('http://api.invalid')
    // When
    // SAFETY: Exercise invalid JavaScript input, including the excluded unknown state.
    const pending = client.v2.search({ states: states as never })
    // Then
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await client.close()
  })

  it('refuses filters when calling list', async () => {
    // Given
    const client = clientAt('http://api.invalid')
    const options = Object.assign({ limit: 10 }, { states: 'started' })
    // When
    const pending = client.v2.list(options)
    // Then
    await expect(pending).rejects.toBeInstanceOf(BadRequestError)
    await client.close()
  })
})
