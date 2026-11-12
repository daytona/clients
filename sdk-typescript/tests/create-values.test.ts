/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import { createSandbox } from './helpers/sandbox'
import { createHttpClient } from '../src/v2/http'

// A mistyped create value is refused before any request, never forwarded, and
// createFromBuild refuses it before the build, so a caller error costs no build.
describe('create option values', () => {
  it.each([
    ['env', { A: 1 }, 'env must be a plain object of string to string'],
    ['env', 'x', 'env must be a plain object of string to string'],
    ['labels', { a: 1 }, 'labels must be a plain object of string to string'],
    ['secrets', 's1', 'secrets must be an array of secret names'],
    ['secrets', ['s1', 2], 'secrets must be an array of secret names'],
    ['ports', [{ port: '80' }], 'ports[].port must be an integer in 1..65535'],
    ['ports', [5], 'PortMapping must be an options object'],
    ['volumes', [{ volumeId: 'v' }], 'volumes[].mountPath must be a non-empty string'],
    ['spot', 'yes', 'spot must be a boolean'],
    ['user', 5, 'user must be a string'],
    ['cpuArch', 5, 'cpuArch must be a string'],
    ['resources', { cpu: '2' }, 'cpu'],
  ] as const)('refuses %s=%j', async (key, value, message) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      // SAFETY: deliberately mistyped plain-JS values reach the runtime checks.
      const options = { [key]: value } as never
      for (const pending of [
        client.v2.create('img', options),
        client.v2.createFromBuild(ImageBuilder.fromRegistry('python:3.12'), options),
      ]) {
        await expect(pending).rejects.toBeInstanceOf(BadRequestError)
        await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(message) })
      }
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it.each(['FROM python:3.12', { dockerfile: 'FROM x' }, null])('refuses images.build(%j) that is not an ImageBuilder', async (builder) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      // SAFETY: deliberately not an ImageBuilder.
      await expect(client.v2.images.build(builder as never)).rejects.toMatchObject({
        statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('images.build() takes an ImageBuilder'),
      })
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('refuses a mistyped createMacos env before any request', async () => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      // SAFETY: a deliberately mistyped plain-JS value.
      await expect(client.v2.createMacos('base-macos', { env: { A: 1 } as never })).rejects.toMatchObject({ code: 'BAD_REQUEST', message: 'env must be a plain object of string to string' })
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('refuses an empty fork name and an unknown artifact wait before any request', async () => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      for (const pending of [
        client.v2.images.get('img', { wait: 'started' as never }),
        client.v2.snapshots.get('snap', { wait: 'started' as never }),
      ]) await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', message: 'wait must be "ready" or omitted, got "started"' })
      const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
      for (const name of ['', '  ']) {
        await expect(sandbox.fork({ name })).rejects.toMatchObject({ code: 'BAD_REQUEST', message: 'fork name must be a non-empty string when given' })
      }
      expect(api.requests).toEqual([])
    } finally {
      await client.close()
      await api.close()
    }
  })
})
