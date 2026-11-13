/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer } from 'node:http'
import { once } from 'node:events'
import { ClientTimeoutError, ConnectionError, ImageBuilder, TransferCancelledError } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'
import { clientAt } from './helpers/client'
import { imageWire } from './artifactFixtures'
import { startFakeApi } from './networkPolicyTestServer'

afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

it.each([new ClientTimeoutError(), new ConnectionError('unreachable')])('returns the created handle when its wait fails with %s', async (error) => {
  // Given a successful create POST and a failed wait request.
  const client = clientAt('http://api.invalid')
  const api = client.v2['api']
  jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'created', class: 'linux', status: 'pending', name: 'created', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
  jest.spyOn(api, 'get').mockRejectedValue(error)
  try {
    // When waiting after successful submission.
    const sandbox = await client.v2.create()
    // Then the accepted sandbox remains inspectable.
    expect(sandbox).toMatchObject({ id: 'created', state: 'creating' })
  } finally {
    await client.close()
  }
})

it.each([new TypeError('bug'), new TransferCancelledError()])('propagates a non-recoverable wait error %s', async (error) => {
  // Given a successful POST followed by a bug or caller cancellation.
  const client = clientAt('http://api.invalid')
  const api = client.v2['api']
  jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'created', class: 'linux', status: 'pending', name: 'created', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
  jest.spyOn(api, 'get').mockRejectedValue(error)
  try {
    // When the post-create wait is interrupted.
    const result = client.v2.create()
    // Then cancellation and programming errors are never swallowed.
    await expect(result).rejects.toBe(error)
  } finally {
    await client.close()
  }
})

it.each([404, 408, 500])('returns the POST-derived fork when the detail read fails with %s', async (status) => {
  // Given a successful fork POST but no readable detail metadata.
  const api = await startFakeApi([
    { status: 200, body: { id: 'source', class: 'macos', status: 'running', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    { status: 202, body: { id: 'fork', class: 'linux', status: 'pending', forked_from: 'source', toolbox_proxy_url: 'https://fork-toolbox.invalid', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    { status, body: { message: 'detail unavailable' } },
  ])
  const source = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
  try {
    // When the initial detail lookup fails.
    const fork = await source.fork()
    // Then only POST fields seed the fallback, with no further reads.
    expect(fork.toJSON()).toEqual({ sandboxId: 'fork', state: 'creating', rawState: 'pending', sandboxClass: 'macos', rawSandboxClass: 'macos', forkedFrom: 'source' })
    expect(fork.toolboxProxyUrl).toBe('https://fork-toolbox.invalid/fork')
    expect(api.requests).toHaveLength(3)
  } finally {
    await api.close()
  }
})

it('charges the fork detail read to the wait budget', async () => {
  // Given a detail read that uses the full fractional wait budget.
  jest.useFakeTimers({ now: 0 })
  const api = createHttpClient('http://api.invalid', undefined, 100)
  jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'fork', class: 'linux', status: 'pending', forked_from: 'source', toolbox_proxy_url: 'https://proxy.example/sandboxes', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } })
  const get = jest.spyOn(api, 'get').mockImplementationOnce(async () => ({
    data: { id: 'source', status: 'running', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] },
  })).mockImplementation(async () => {
    jest.setSystemTime(250)
    return { data: { id: 'fork', status: 'pending', class: 'macos', name: 'fork', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } }
  })
  const source = createSandbox({ id: 'source', class: 'macos', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, api)
  // When acquiring the fork with a quarter-second wait budget.
  const fork = await source.fork({ waitSeconds: 0.25 })
  // Then the detail uses the smaller request bound and no wait starts after expiry.
  expect(get).toHaveBeenCalledTimes(2)
  expect(get).toHaveBeenNthCalledWith(2, '/sandboxes/fork', { timeout: 100 })
  expect(fork.state).toBe('creating')
})

it('builds a toolbox-less detail handle without fetching the same resource again', async () => {
  // Given a complete API detail response without a toolbox URL.
  const api = await startFakeApi([
    { status: 200, body: { id: 'sandbox', class: 'linux', status: 'stopped', name: 'sandbox', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
    { status: 200, body: { id: 'sandbox', status: 'stopped', name: 'sandbox', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
  ])
  const client = clientAt(api.url)
  try {
    // When connecting to the sandbox.
    const sandbox = await client.v2.get('sandbox')
    // Then the response is mapped directly, with no second read.
    expect(sandbox).toMatchObject({ id: 'sandbox', state: 'stopped', sandboxClass: 'linux', rawSandboxClass: 'linux' })
    expect(api.requests).toHaveLength(1)
    // And a detail without the required class is a contract error, never a guessed Linux.
    await expect(client.v2.get('sandbox')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'sandbox response is missing its class' })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['build', 'createFromBuild'] as const)('bounds a stalled %s submission by the request timeout', async (method) => {
  // Given a real peer that accepts POST /images but never replies.
  const methods: string[] = []
  const server = createServer((request) => { methods.push(`${request.method} ${request.url}`); request.resume() })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  const client = clientAt(`http://127.0.0.1:${address.port}`, { requestTimeoutSeconds: 0.05 })
  try {
    const builder = ImageBuilder.fromRegistry('base')
    // When submitting with no build-wait deadline.
    const pending = method === 'build' ? client.v2.images.build(builder) : client.v2.createFromBuild(builder)
    // Then the ordinary request timeout applies before any build or create wait.
    await expect(pending).rejects.toBeInstanceOf(ClientTimeoutError)
    expect(methods).toEqual(['POST /images'])
  } finally {
    await client.close()
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  }
})

it.each(['pending', 'error'])('reports an error-status image as a failed build when the initial status is %s', async (status) => {
  // Given an accepted build whose readiness observation reports error.
  const api = await startFakeApi([
    { status: 202, body: { id: 'img_failed', status: 'pending' } },
    { status: 200, body: imageWire({ id: 'img_failed', status, error: status === 'error' ? 'bad Dockerfile' : undefined }) },
    { status: 200, body: imageWire({ id: 'img_failed', status: 'error', error: 'bad Dockerfile' }) },
  ])
  const client = clientAt(api.url)
  try {
    // When creating from the build.
    const result = client.v2.createFromBuild(ImageBuilder.fromRegistry('base'))
    // Then the image error, not a timeout, is actionable and no sandbox is created.
    await expect(result).rejects.toMatchObject({ statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: 'img_failed', buildError: 'bad Dockerfile' })
    expect(api.requests.filter((request) => request.method === 'POST').map((request) => request.path)).toEqual(['/images'])
  } finally {
    await client.close()
    await api.close()
  }
})
