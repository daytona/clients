/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createServer, type Server } from 'node:http'
import { Sandbox } from '../src/v2/Sandbox'
import { SandboxVolumesService } from '../src/v2/SandboxVolumes'
import { INTERNAL } from '../src/v2/internal'
import { HttpTransport } from '../src/v2/http'
import { WAIT_READ_SLACK_MS } from '../src/v2/Process'
import { BadRequestError, ConflictError, RateLimitError, ServiceUnavailableError, UnsupportedOperationError } from '../src/v2/errors/DaytonaError'

describe('sandbox volume pull', () => {
  let server: Server
  let base: string
  let requests: { path?: string, body: unknown, method?: string }[]
  let responses: { status: number, body: object }[]

  beforeEach(async () => {
    requests = []
    responses = []
    server = createServer((request, response) => {
      let body = ''
      request.setEncoding('utf8')
      request.on('data', (chunk: string) => { body += chunk })
      request.on('end', () => {
        requests.push({ path: request.url, body: JSON.parse(body), method: request.method })
        const next = responses.shift() ?? { status: 500, body: { message: 'unexpected request' } }
        response.writeHead(next.status, { 'Content-Type': 'application/json', 'Retry-After': '1' })
        response.end(JSON.stringify(next.body))
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('missing server address')
    base = `http://127.0.0.1:${address.port}`
  })

  afterEach(async () => {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  })

  it('scopes to the sandbox URL, preserves nullable results and reuses caller identity after pending', async () => {
    const sandbox = new Sandbox(INTERNAL, { sandbox: "sbx/!'", apiKey: 'token', options: { toolboxUrl: `${base}/sandbox-api/sbx%2F%21%27` } })
    responses.push(
      { status: 202, body: { requestId: 'req_1', status: 'pending', manifestId: null, futureField: true } },
      { status: 200, body: { requestId: 'req_1', status: 'pulled', manifestId: 'manifest-1' } },
      { status: 200, body: { requestId: 'req_1', status: 'pulled', manifestId: 'manifest-1' } },
    )
    expect(await sandbox.volumes.pull('volume_1', { requestId: 'req_1', timeoutSeconds: 0 })).toMatchObject({ status: 'pending', rawStatus: 'pending', manifestId: undefined })
    expect(requests).toHaveLength(1)
    const result = await sandbox.volumes.pull('volume_1', { requestId: 'req_1', timeoutSeconds: 30 })
    expect(result).toEqual({ requestId: 'req_1', status: 'pulled', rawStatus: 'pulled', manifestId: 'manifest-1' })
    expect(await sandbox.volumes.pull('volume_1', { requestId: 'req_1' })).toEqual(result)
    expect(requests).toEqual([
      { method: 'POST', path: '/sandbox-api/sbx%2F%21%27/volumes/volume_1/pull', body: { requestId: 'req_1', waitMs: 0 } },
      { method: 'POST', path: '/sandbox-api/sbx%2F%21%27/volumes/volume_1/pull', body: { requestId: 'req_1', waitMs: 30_000 } },
      { method: 'POST', path: '/sandbox-api/sbx%2F%21%27/volumes/volume_1/pull', body: { requestId: 'req_1' } },
    ])
  })

  test('passes a status this SDK release does not list through', async () => {
    responses.push({ status: 200, body: { requestId: 'r', status: 'superseded', manifestId: null } })
    const volumes = new SandboxVolumesService(INTERNAL, new HttpTransport(base))
    expect(await volumes.pull('v', { requestId: 'r' })).toEqual({ requestId: 'r', status: 'unknown', rawStatus: 'superseded', manifestId: undefined })
  })

  test.each([null, 'manifest-2'])('unchanged manifest %s', async (manifestId) => {
    responses.push({ status: 200, body: { requestId: 'r', status: 'unchanged', manifestId } })
    const volumes = new SandboxVolumesService(INTERNAL, new HttpTransport(base))
    expect(await volumes.pull('v', { requestId: 'r' })).toEqual({ requestId: 'r', status: 'unchanged', rawStatus: 'unchanged', manifestId: manifestId ?? undefined })
  })

  test.each([
    [undefined, undefined], [30, 30_000], [0, 0], [1.2349, 1234], [0.0001, 1],
  ])('HTTP budget accommodates %s seconds plus read slack', async (timeoutSeconds, waitMs) => {
    // Given
    const transport = new HttpTransport(base)
    const budgets: (number | undefined)[] = []
    transport.http.defaults.adapter = async (config) => {
      budgets.push(config.timeout)
      expect(config.data).toBe(JSON.stringify({ requestId: 'r', waitMs }))
      return {
        data: { requestId: 'r', status: 'pending', manifestId: null },
        status: 202, statusText: 'Accepted', headers: {}, config,
      }
    }
    const volumes = new SandboxVolumesService(INTERNAL, transport)
    // When
    const result = await volumes.pull('v', { requestId: 'r', timeoutSeconds })
    // Then
    expect(result)
      .toEqual({ requestId: 'r', status: 'pending', rawStatus: 'pending', manifestId: undefined })
    expect(budgets).toEqual([(waitMs ?? 30_000) + WAIT_READ_SLACK_MS])
  })

  test.each(['', 'a/b', '..', 'a%2Fb', 'a?b', 'a#b', 'a b', 'é', 'a\n', 'a'.repeat(129)])('rejects invalid id %j before sending', async (id) => {
    const volumes = new SandboxVolumesService(INTERNAL, new HttpTransport(base))
    await expect(volumes.pull(id, { requestId: 'r' })).rejects.toBeInstanceOf(BadRequestError)
    await expect(volumes.pull('v', { requestId: id })).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toHaveLength(0)
  })

  test.each([-1, 30.001, NaN, Infinity])('rejects wait %s', async (timeoutSeconds) => {
    const volumes = new SandboxVolumesService(INTERNAL, new HttpTransport(base))
    await expect(volumes.pull('v', { requestId: 'r', timeoutSeconds })).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toHaveLength(0)
  })

  test.each([
    [429, 'TOO_MANY_REQUESTS', RateLimitError],
    [503, 'SERVICE_UNAVAILABLE', ServiceUnavailableError],
    [409, 'CONFLICT', ConflictError],
    [400, 'UNSUPPORTED_OPERATION', UnsupportedOperationError],
  ] as const)('propagates %s without retry', async (status, code, errorClass) => {
    responses.push({ status, body: { code, message: 'not ready', source: 'DAYTONA_DAEMON', requestId: 'r', retryAfterMs: 1000 } })
    const volumes = new SandboxVolumesService(INTERNAL, new HttpTransport(base))
    const result = volumes.pull('v', { requestId: 'r' })
    await expect(result).rejects.toBeInstanceOf(errorClass)
    await expect(result).rejects.toMatchObject({ requestId: 'r', retryAfterMs: 1000, statusCode: status, code })
    expect(requests).toHaveLength(1)
  })

  it('caches the volume namespace without sending a request', () => {
    const sandbox = new Sandbox(INTERNAL, { sandbox: 'sandbox-id', apiKey: 'token', options: { toolboxUrl: base } })
    const volumes = sandbox.volumes
    expect(sandbox.volumes).toBe(volumes)
    expect(requests).toHaveLength(0)
  })
})
