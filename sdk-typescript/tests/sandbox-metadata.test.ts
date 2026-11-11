/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createSandbox } from './helpers/sandbox'
import { createHttpClient } from '../src/v2/http'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'

describe('sandbox observed metadata', () => {
  it.each(['future-class', ''])('preserves an unrecognized wire class %s as unknown', (sandboxClass) => {
    // Given a wire class outside the committed vocabulary.
    const sandbox = createSandbox({ id: 'sbx', class: sandboxClass })
    // When serializing the observed class.
    const json = sandbox.toJSON()
    // Then even an empty wire string remains distinct from an absent class.
    expect(json).toMatchObject({ sandboxClass: 'unknown', rawSandboxClass: sandboxClass })
  })

  it.each(['linux', 'macos', 'future-class'])('reads fractional GiB exactly for %s', (sandboxClass) => {
    // Given a response with fractions and a zero allocation.
    const response = { id: 'sbx', class: sandboxClass, memory_mib: 1, memory_max_mib: 1537, disk_mib: 0, disk_max_mib: 8193 }
    // When the handle reads the response.
    const sandbox = createSandbox(response)
    // Then no value is rounded, clamped, or invented.
    expect([sandbox.memory, sandbox.memoryMax, sandbox.disk, sandbox.diskMax]).toEqual([1 / 1024, 1537 / 1024, 0, 8193 / 1024])
  })

  it('keeps observed metadata read-only and detached from the input', () => {
    // Given an externally owned response object.
    const labels = { team: 'original' }
    const sandbox = createSandbox({ id: 'sbx', name: 'original', labels })
    // When the input and public property are modified.
    labels.team = 'changed'
    const changed = Reflect.set(sandbox, 'name', 'changed')
    // Then the observation remains the server's value.
    expect(changed).toBe(false)
    expect(sandbox.name).toBe('original')
    expect(sandbox.labels).toEqual({ team: 'original' })
    expect(Object.isFrozen(sandbox.labels)).toBe(true)
  })

  it('replaces read-only observations when refreshed', async () => {
    // Given a handle with stale metadata and a real HTTP response.
    const api = await startFakeApi([{ status: 200, body: { id: 'sbx', name: 'fresh', class: 'future', status: 'future-state', memory_mib: 513, cpu: 1, cores_max: 1, disk_mib: 1024, region_id: '', port_map: [] } }])
    const sandbox = createSandbox({ id: 'sbx', name: 'stale', class: 'linux', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When metadata is fetched again.
      await sandbox.refreshData()
      // Then normalized and raw observations are replaced together.
      expect(sandbox.toJSON()).toMatchObject({ name: 'fresh', state: 'unknown', rawState: 'future-state', sandboxClass: 'unknown', rawSandboxClass: 'future', memory: 513 / 1024 })
    } finally {
      await api.close()
    }
  })

  it.each([
    { response: { id: 'sbx' }, expected: { sandboxId: 'sbx', labels: {} } },
    { response: { id: 'sbx', status: '' }, expected: { sandboxId: 'sbx', labels: {} } },
    { response: { id: 'sbx', status: 'future' }, expected: { sandboxId: 'sbx', state: 'unknown', rawState: 'future', labels: {} } },
    { response: { id: 'sbx', persistent: false, spot: false, cpu: 0, labels: {} }, expected: { sandboxId: 'sbx', persistent: false, spot: false, cpu: 0, labels: {} } },
  ])('serializes only known fields: $response', ({ response, expected }) => {
    // Given an observation containing absent, empty, unknown, or falsy values.
    const sandbox = createSandbox(response)
    // When serialized.
    const json = sandbox.toJSON()
    // Then the object itself (before JSON.stringify) follows the presence rules;
    // partial data without a class leaves it out, and labels a read reported
    // (none counts) are known.
    expect(json).toEqual(expected)
    expect(Object.values(json)).not.toContain(undefined)
  })

  it('serializes safe metadata and the server\'s timestamp strings without connection details', () => {
    // Given observed artifacts, timestamps, and a usable toolbox credential.
    const sandbox = createSandbox({
      id: 'sbx', class: 'linux', status: 'running', image: { id: 'img', name: null },
      snapshot: { id: 'snap', name: 'backup' }, labels: { owner: 'team' },
      spot_evicted_at: '2026-05-01T13:30:00.123456+01:30',
      lifecycle_policy: { timeout_seconds: 30, last_activity_at: '2026-05-01T13:30:00+01:30' },
    }, undefined, undefined, undefined, undefined, { toolboxUrl: 'https://toolbox.invalid', accessToken: 'fixture-token' })
    // When serialized for persistence.
    const json = sandbox.toJSON()
    // Then only the documented metadata is present, timestamps verbatim (as Python writes them).
    expect(json.image).not.toHaveProperty('name')
    expect(json).toEqual({
      sandboxId: 'sbx', sandboxClass: 'linux', rawSandboxClass: 'linux', state: 'started', rawState: 'running',
      image: { id: 'img' }, snapshot: { id: 'snap', name: 'backup' }, labels: { owner: 'team' },
      spotEvictedAt: '2026-05-01T13:30:00.123456+01:30', lifecyclePolicy: { timeoutSeconds: 30, lastActivityAt: '2026-05-01T13:30:00+01:30' },
    })
  })

  it('refuses a sandbox response without its class as a contract error', async () => {
    // Given a detail read that omits the required class.
    const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'running' } }])
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // Then refreshing raises the SDK's BadRequestError instead of guessing Linux.
      await expect(sandbox.refreshData()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'sandbox response is missing its class' })
    } finally {
      await api.close()
    }
  })

  it('refuses class-gated operations on a future class without guessing Linux', async () => {
    // Given an unknown API class and a transport with no planned requests.
    const api = await startFakeApi([])
    const sandbox = createSandbox({ id: 'sbx', class: 'future', name: 'sbx', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024 }, createHttpClient(api.url))
    try {
      // When a waiting fork requires a known class (its wait targets depend on it).
      const result = sandbox.fork({ waitSeconds: 5 })
      // Then it fails locally with the SDK classification error.
      await expect(result).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
      expect(api.requests).toEqual([])
    } finally {
      await api.close()
    }
  })

  it('updates read-only token observations after lazy toolbox authentication', async () => {
    // Given a key-backed handle whose first toolbox request must mint a token.
    const api = await startFakeApi([
      { status: 200, body: { access_token: 'minted-fixture', expires_at: '2030-01-01T00:00:00Z', operations: ['toolbox'], token_type: 'Bearer' } },
      { status: 200, body: { timestamp: '2030-01-01T00:00:00Z', cpuCount: 1, cpuUtilization: 0, memoryUsageBytes: 0, memoryTotalBytes: 1, diskUsageBytes: 0, diskTotalBytes: 1 } },
    ])
    const sandbox = createSandbox('sbx', createHttpClient(api.url), undefined, 'project-fixture', undefined, { toolboxUrl: api.url })
    try {
      // When a toolbox operation authenticates.
      await sandbox.metrics()
      // Then token metadata reflects the minted credential but remains read-only.
      expect(sandbox.accessToken).toBe('minted-fixture')
      expect(sandbox.accessTokenExpiresAt).toBe('2030-01-01T00:00:00Z')
      expect(Reflect.set(sandbox, 'accessToken', 'replacement')).toBe(false)
      expect(sentRequest(api, 2, 1).headers.authorization).toBe('Bearer minted-fixture')
      expect(sandbox.toJSON()).toEqual({ sandboxId: 'sbx' })
    } finally {
      await api.close()
    }
  })
})
