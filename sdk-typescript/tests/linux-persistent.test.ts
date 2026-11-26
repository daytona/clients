/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { assertLifecycleClassSupport, createHttpClient } from '../src/v2/http'
import type { CreateSandboxOptions, LifecycleAction } from '../src/v2/types'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { imageWire } from './artifactFixtures'
import { jsonBody, sentRequest, startFakeApi } from './networkPolicyTestServer'

it.each([true, false])('sends Linux persistent=%s and retains it without a detail read', async (persistent) => {
  // Given a create response without metadata,
  const api = await startFakeApi([{ status: 202, body: { id: 'new', status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    // When creating explicitly,
    const sandbox = await client.v2.create({ persistent, waitSeconds: 0 })
    // Then the body and handle agree, even before a detail read.
    expect(jsonBody(sentRequest(api, 1))).toMatchObject({ persistent })
    expect(sandbox.persistent).toBe(persistent)
    expect(sandbox.toJSON().persistent).toBe(persistent)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([true, false])('retains requested persistence %s when a create wait detail omits it', async (persistent) => {
  // Given a create with known persistence and a started detail without that optional field,
  const api = await startFakeApi([
    { status: 202, body: { id: 'new', status: 'pending', wait_cap_seconds: 0 } },
    { status: 200, body: { id: 'new', class: 'linux', status: 'running', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] } },
  ])
  const client = clientAt(api.url)
  try {
    // When the create waits through that detail read,
    const sandbox = await client.v2.create({ persistent, waitSeconds: 1 })
    // Then immutable persistence remains known.
    expect(sandbox.persistent).toBe(persistent)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([true, null])('forwards persistent=%s through createFromBuild', async (persistent) => {
  // Given a ready image build and a create acknowledgement,
  const api = await startFakeApi([
    { status: 202, body: { id: 'img_ready' } },
    { status: 200, body: imageWire({ id: 'img_ready', status: 'ready' }) },
    { status: 202, body: { id: 'new', status: 'pending' } },
  ])
  const client = clientAt(api.url)
  try {
    // When building and creating,
    const options = { persistent: persistent ?? undefined, waitSeconds: 0, lifecyclePolicy: persistent ? { onTimeout: 'pause' as const } : undefined }
    Reflect.set(options, 'persistent', persistent)
    const sandbox = await client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), options)
    // Then persistence reaches only the create request, not the image build.
    expect(jsonBody(sentRequest(api, 3, 0))).not.toHaveProperty('persistent')
    const body = jsonBody(sentRequest(api, 3, 2))
    if (persistent === null) expect(body).not.toHaveProperty('persistent')
    else expect(body).toMatchObject({ persistent: true })
    expect(sandbox.persistent).toBe(persistent ?? undefined)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['stop', 'pause'] as const)('creates persistent Linux with automatic %s', async (onTimeout) => {
  // Given a persistent Linux create and a policy action,
  const api = await startFakeApi([{ status: 202, body: { id: 'new', status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    // When creating, then the action reaches the server with persistence.
    await client.v2.create({ persistent: true, lifecyclePolicy: { onTimeout }, waitSeconds: 0 })
    expect(jsonBody(sentRequest(api, 1))).toMatchObject({ persistent: true, lifecycle_policy: { on_timeout: onTimeout } })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['create', 'createFromBuild'] as const)('%s rejects a non-boolean persistent value before any request', async (method) => {
  // Given an invalid JavaScript value,
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  const options: CreateSandboxOptions = { waitSeconds: 0, lifecyclePolicy: { onTimeout: 'pause' } }
  Reflect.set(options, 'persistent', 'yes')
  try {
    // When invoking the create route, then it fails before a billed request.
    const result = method === 'create' ? client.v2.create(options)
      : client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), options)
    await expect(result).rejects.toThrow('persistent must be a boolean')
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it('refuses a persistent option on snapshot restore rather than dropping it', async () => {
  // Given a snapshot and a create-only option,
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    // When restoring, then the unknown key is refused before submission.
    const options = { persistent: true, waitSeconds: 0 }
    await expect(client.v2.createFromSnapshot('snap', options)).rejects.toBeInstanceOf(BadRequestError)
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['stop', 'pause'] as const)('lets the API validate inherited snapshot persistence for %s', async (onTimeout) => {
  // Given a snapshot whose persistence is unknown locally,
  const api = await startFakeApi([{ status: 202, body: { id: 'restored', status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    // When restoring with a persistence-dependent action,
    await client.v2.createFromSnapshot('snap', { lifecyclePolicy: { onTimeout }, waitSeconds: 0 })
    // Then the action is submitted rather than rejected as ephemeral.
    expect(jsonBody(sentRequest(api, 1))).toMatchObject({ lifecycle_policy: { on_timeout: onTimeout } })
  } finally {
    await client.close()
    await api.close()
  }
})

const CLASSES = ['linux', 'macos', 'gpu', 'future'] as const
const ACTIONS: readonly LifecycleAction[] = ['delete', 'stop', 'pause']
it.each(CLASSES.flatMap((sandboxClass) => [true, false, undefined].flatMap((persistent) =>
  ACTIONS.map((action) => ({ sandboxClass, persistent, action })),
)))('validates $action on $sandboxClass persistent=$persistent', ({ sandboxClass, persistent, action }) => {
  // Given each class/persistence combination, when validating an action,
  const validate = () => assertLifecycleClassSupport({ onTimeout: action }, { sandboxClass, persistent })
  // Then only the policy matrix is accepted.
  const allowed = action === 'delete' || (persistent !== false && (action === 'stop' ? sandboxClass !== 'gpu' : sandboxClass === 'linux'))
  if (allowed) expect(validate).not.toThrow()
  else expect(validate).toThrow(BadRequestError)
})

it('sends a delete policy for an unknown sandbox class without guessing Linux', async () => {
  // Given an unfamiliar class with known persistence,
  const api = await startFakeApi([{ status: 200, body: { id: 'src', lifecycle_policy: { on_timeout: 'delete' } } }])
  const sandbox = createSandbox({ sandboxId: 'src', sandboxClass: 'unknown', rawSandboxClass: 'future', persistent: true }, createHttpClient(api.url, 'key'))
  try {
    // When setting a class-independent action, then it reaches the API.
    await expect(sandbox.updateLifecyclePolicy({ onTimeout: 'delete' })).resolves.toMatchObject({ onTimeout: 'delete' })
    expect(jsonBody(sentRequest(api, 1))).toEqual({ on_timeout: 'delete' })
  } finally {
    await api.close()
  }
})

it.each([
  { persistent: true, classKnown: true },
  { persistent: undefined, classKnown: true },
  { persistent: undefined, classKnown: false },
])('reads missing Linux persistence ($persistent, class known=$classKnown) before validating an update', async ({ persistent, classKnown }) => {
  // Given a class-only handle and authoritative metadata,
  const detail = { id: 'src', class: 'linux', status: 'running', name: 'src', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], persistent }
  const api = await startFakeApi([
    { status: 200, body: detail },
    { status: 200, body: { id: 'src', lifecycle_policy: { on_timeout: 'pause' } } },
  ])
  const sandbox = createSandbox({ sandboxId: 'src', sandboxClass: classKnown ? 'linux' : undefined }, createHttpClient(api.url, 'key'))
  try {
    // When updating, then a true or still unknown persistence never becomes false.
    await expect(sandbox.updateLifecyclePolicy({ onTimeout: 'pause' })).resolves.toMatchObject({ onTimeout: 'pause' })
    expect(api.requests.map((request) => request.method)).toEqual(['GET', 'PATCH'])
    expect(jsonBody(sentRequest(api, 2, 1))).toEqual({ on_timeout: 'pause' })
  } finally {
    await api.close()
  }
})

it('rejects automatic pause when a metadata read proves Linux persistence is false', async () => {
  // Given an initially metadata-less handle for an ephemeral sandbox,
  const detail = { id: 'src', class: 'linux', status: 'running', name: 'src', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], persistent: false }
  const api = await startFakeApi([{ status: 200, body: detail }])
  const sandbox = createSandbox({ sandboxId: 'src', sandboxClass: 'linux' }, createHttpClient(api.url, 'key'))
  try {
    // When updating, then authoritative false is refused before PATCH.
    await expect(sandbox.updateLifecyclePolicy({ onTimeout: 'pause' })).rejects.toBeInstanceOf(BadRequestError)
    expect(api.requests.map((request) => request.method)).toEqual(['GET'])
  } finally {
    await api.close()
  }
})

it.each([
  ['gpu', 'stop'],
  ['gpu', 'pause'],
  ['macos', 'pause'],
  ['future', 'pause'],
] as const)('rejects %s %s even when a metadata read omits persistence', async (sandboxClass, onTimeout) => {
  // Given a known class whose detail still omits persistence,
  const detail = { id: 'src', class: sandboxClass, status: 'running', name: 'src', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
  const api = await startFakeApi([{ status: 200, body: detail }])
  const sandbox = createSandbox(detail, createHttpClient(api.url, 'key'))
  try {
    // When requesting an action forbidden by class alone,
    await expect(sandbox.updateLifecyclePolicy({ onTimeout })).rejects.toBeInstanceOf(BadRequestError)
    // Then the metadata read is not followed by a PATCH.
    expect(api.requests.map((request) => request.method)).toEqual(['GET'])
  } finally {
    await api.close()
  }
})
