/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { createHttpClient } from '../src/v2/http'
import { startFakeApi } from './networkPolicyTestServer'

// Every options parameter of the client, lifecycle and platform services
// refuses a value that is not an options object (an older positional argument)
// before any request.
describe('options parameters refuse non-object values', () => {
  type Call = (value: never) => Promise<unknown> | AsyncIterable<unknown>
  const calls = (client: ReturnType<typeof clientAt>, url: string): Record<string, Call> => {
    const v2 = client.v2
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'running', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(url), undefined, 'key', undefined, { toolboxUrl: `${url}/toolbox` })
    return {
      'create': (value) => v2.create('img', value),
      'createFromSnapshot': (value) => v2.createFromSnapshot('snap', value),
      'createMacos': (value) => v2.createMacos('base-macos', value),
      'createFromBuild': (value) => v2.createFromBuild(ImageBuilder.fromRegistry('python:3.12'), value),
      'get': (value) => v2.get('sbx', value),
      'findByName': (value) => v2.findByName('name', value),
      'list': (value) => v2.list(value),
      'iter': (value) => v2.iter(value),
      'search': (value) => v2.search(value),
      'start': (value) => v2.start('sbx', value),
      'stop': (value) => v2.stop('sbx', value),
      'pause': (value) => v2.pause('sbx', value),
      'resume': (value) => v2.resume('sbx', value),
      'delete': (value) => v2.delete('sbx', value),
      'sandbox.wait': (value) => sandbox.wait(value),
      'sandbox.fork': (value) => sandbox.fork(value),
      'sandbox.captureImage': (value) => sandbox.captureImage(value),
      'sandbox.updateNetworkPolicy': (value) => sandbox.updateNetworkPolicy({ blockAll: true }, value),
      'sandbox.resetNetworkPolicy': (value) => sandbox.resetNetworkPolicy(value),
      'sandbox.replaceSecrets': (value) => sandbox.replaceSecrets([], value),
      'sandbox.createAccessToken': (value) => sandbox.createAccessToken(value),
      'images.build': (value) => v2.images.build(ImageBuilder.fromRegistry('python:3.12'), value),
      'images.pull': (value) => v2.images.pull('python:3.12', value),
      'images.get': (value) => v2.images.get('img', value),
      'images.list': (value) => v2.images.list(value),
      'snapshots.get': (value) => v2.snapshots.get('snap', value),
      'snapshots.list': (value) => v2.snapshots.list(value),
      'volumes.get': (value) => v2.volumes.get('vol', value),
      'volumes.create': (value) => v2.volumes.create(value),
      'volumes.delete': (value) => v2.volumes.delete('vol', value),
      'volumes.list': (value) => v2.volumes.list(value),
      'secrets.set': (value) => v2.secrets.set('S', 'v', value),
      'secrets.list': (value) => v2.secrets.list(value),
      'reservations.list': (value) => v2.reservations.list(value),
      'reservations.renew': (value) => v2.reservations.renew('res', value),
      'reservations.update': (value) => v2.reservations.update('res', value),
      'macModels.catalog': (value) => v2.macModels.catalog(value),
      'getLimits': (value) => v2.getLimits(value),
      'images.iter': (value) => v2.images.iter(value),
      'snapshots.iter': (value) => v2.snapshots.iter(value),
      'volumes.iter': (value) => v2.volumes.iter(value),
      'secrets.iter': (value) => v2.secrets.iter(value),
      'reservations.iter': (value) => v2.reservations.iter(value),
      'sandbox.start': (value) => sandbox.start(value),
      'sandbox.stop': (value) => sandbox.stop(value),
      'sandbox.pause': (value) => sandbox.pause(value),
      'sandbox.resume': (value) => sandbox.resume(value),
      'sandbox.delete': (value) => sandbox.delete(value),
      'sandbox.updateLifecyclePolicy': (value) => sandbox.updateLifecyclePolicy(value),
      'sandbox.ports.expose': (value) => sandbox.ports.expose(3000, value),
      'sandbox.ports.signedPreviewLink': (value) => sandbox.ports.signedPreviewLink(3000, value),
      'sandbox.ports.listEndpoints': (value) => sandbox.ports.listEndpoints(value),
      'sandbox.docker.waitUntilReady': (value) => sandbox.docker.waitUntilReady(value),
      'sandbox.volumes.pull': (value) => sandbox.volumes.pull('vol', value),
      'create resources': (value) => v2.create('img', { resources: value }),
      'create ports entry': (value) => v2.create('img', { ports: [value] }),
      'create volumes entry': (value) => v2.create('img', { volumes: [value] }),
      'createMacos resources': (value) => v2.createMacos('base-macos', { resources: value }),
      'createFromBuild resources': (value) => v2.createFromBuild(ImageBuilder.fromRegistry('python:3.12'), { resources: value }),
    }
  }

  it.each(['create', 'createFromSnapshot', 'createMacos', 'createFromBuild', 'get', 'findByName', 'list', 'iter', 'search',
    'start', 'stop', 'pause', 'resume', 'delete', 'sandbox.wait', 'sandbox.fork', 'sandbox.captureImage',
    'sandbox.updateNetworkPolicy', 'sandbox.resetNetworkPolicy', 'sandbox.replaceSecrets', 'sandbox.createAccessToken',
    'images.build', 'images.pull', 'images.get', 'images.list', 'snapshots.get', 'snapshots.list',
    'volumes.get', 'volumes.create', 'volumes.delete', 'volumes.list', 'secrets.set', 'secrets.list',
    'reservations.list', 'reservations.renew', 'reservations.update', 'macModels.catalog', 'getLimits',
    'images.iter', 'snapshots.iter', 'volumes.iter', 'secrets.iter', 'reservations.iter',
    'sandbox.start', 'sandbox.stop', 'sandbox.pause', 'sandbox.resume', 'sandbox.delete', 'sandbox.updateLifecyclePolicy',
    'sandbox.ports.expose', 'sandbox.ports.signedPreviewLink', 'sandbox.ports.listEndpoints', 'sandbox.docker.waitUntilReady',
    'sandbox.volumes.pull', 'create resources', 'create ports entry', 'create volumes entry', 'createMacos resources',
    'createFromBuild resources'])('%s', async (name) => {
    const api = await startFakeApi([])
    const client = clientAt(api.url)
    try {
      for (const value of [null, true, 'x', 3, [], () => undefined]) {
        const result = calls(client, api.url)[name]?.(value as never)
        const pending = result !== undefined && Symbol.asyncIterator in result
          ? (async () => { for await (const _ of result) break })()
          : Promise.resolve(result)
        await expect(pending).rejects.toBeInstanceOf(BadRequestError)
        await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      }
      expect(api.requests).toHaveLength(0)
    } finally {
      await client.close()
      await api.close()
    }
  })
})
