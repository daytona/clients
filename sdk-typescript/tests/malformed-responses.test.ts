/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'
import type { FakeApi } from './networkPolicyTestServer'

type Call = (client: Daytona, api: FakeApi) => Promise<unknown>

const source = (api: FakeApi) => createSandbox({ id: 'src', class: 'linux', status: 'stopped' }, createHttpClient(api.url, 'key'))
const withToolbox = (api: FakeApi) => createSandbox({ id: 'src', class: 'linux', status: 'started' }, createHttpClient(api.url, 'key'), undefined, 'key', undefined, { toolboxUrl: api.url, accessToken: 'tok' })

// The malformed-response rule, for every read of the client, lifecycle and
// platform-service area: a `{}` body is a BadRequestError (400, BAD_REQUEST,
// SDK) naming what is missing, and nothing follows it (no
// `GET /images/undefined`, no fork handle). Python's twin is
// tests/test_malformed_responses.py.
const CALLS: readonly (readonly [string, Call, string])[] = [
  ['get', (client) => client.v2.get('sbx'), 'sandbox response is missing its id'],
  ['images.get', (client) => client.v2.images.get('img'), 'image response is missing its id'],
  ['images.promote', (client) => client.v2.images.promote('img', 'named'), 'image response is missing its id'],
  ['images.build', (client) => client.v2.images.build(ImageBuilder.fromRegistry('alpine')), 'image create response is missing its id'],
  ['images.pull', (client) => client.v2.images.pull('alpine', { wait: false }), 'image create response is missing its id'],
  ['snapshots.get', (client) => client.v2.snapshots.get('snap'), 'snapshot response is missing its id'],
  ['volumes.get', (client) => client.v2.volumes.get('data'), 'volume response is missing its id'],
  ['volumes.create', (client) => client.v2.volumes.create({ name: 'data' }), 'volume response is missing its id'],
  ['secrets.get', (client) => client.v2.secrets.get('KEY'), 'secret response is missing its name'],
  ['secrets.set', (client) => client.v2.secrets.set('KEY', 'value', { allowedDomains: [] }), 'secret response is missing its name'],
  ['reservations.get', (client) => client.v2.reservations.get('res'), 'reservation response is missing its id'],
  ['reservations.create', (client) => client.v2.reservations.create({ region: 'mac' }), 'reservation response is missing its id'],
  ['reservations.createBulk', (client) => client.v2.reservations.createBulk({ region: 'mac', count: 2 }), 'reservation batch response is missing its requested'],
  ['reservations.renew', (client) => client.v2.reservations.renew('res'), 'reservation response is missing its id'],
  ['reservations.update', (client) => client.v2.reservations.update('res', { autoRenew: false }), 'reservation response is missing its id'],
  ['macModels.catalog', (client) => client.v2.macModels.catalog(), 'Mac catalog response is missing its regions'],
  ['currentApiKey', (client) => client.v2.currentApiKey(), 'API key response is missing its id'],
  ['listAuditActions', (client) => client.v2.listAuditActions(), 'audit actions response is missing its targets'],
  ['getLimits', (client) => client.v2.getLimits({ organizationId: 'org' }), 'organization limits response is missing its sandboxes'],
  ['fork', (_client, api) => source(api).fork({ waitSeconds: 0 }), 'sandbox fork response is missing its id'],
  ['captureImage', (_client, api) => source(api).captureImage(), 'image capture response is missing its id'],
  ['createSnapshot', (_client, api) => source(api).createSnapshot('snap'), 'snapshot create response is missing its id'],
  ['createAccessToken', (_client, api) => source(api).createAccessToken(), 'sandbox access token response is missing its access_token'],
  ['ports.expose', (_client, api) => source(api).ports.expose(8080), 'preview endpoint response is missing its port'],
  ['ports.signedPreviewLink', (_client, api) => source(api).ports.signedPreviewLink(8080), 'signed preview url response is missing its url'],
  // Toolbox reads this area maps (a waiting Docker read fails at once, not at its deadline).
  ['docker.status', (_client, api) => withToolbox(api).docker.status(), 'docker status response is missing its owner'],
  ['docker.waitUntilReady', (_client, api) => withToolbox(api).docker.waitUntilReady({ timeoutSeconds: 30 }), 'docker status response is missing its owner'],
  ['volumes.pull', (_client, api) => withToolbox(api).volumes.pull('vol1', { requestId: 'r1' }), 'volume pull response is missing its requestId'],
  ['ports.isInUse', (_client, api) => withToolbox(api).ports.isInUse(3000), 'port in use response is missing its isInUse'],
  ['metrics', (_client, api) => withToolbox(api).metrics(), 'system metrics response is missing its timestamp'],
  ['updateLifecyclePolicy', (_client, api) => source(api).updateLifecyclePolicy({ timeoutSeconds: 60 }), 'sandbox lifecycle policy response is missing its id'],
  ['resetIdleTimer', (_client, api) => source(api).resetIdleTimer(), 'sandbox lifecycle policy response is missing its id'],
]

it.each(CALLS)('%s refuses a {} body with BadRequestError and sends nothing after it', async (_name, call, message) => {
  const api = await startFakeApi([{ status: 200, body: {} }])
  const client = clientAt(api.url)
  try {
    const error = await call(client, api).then(() => undefined, (failure: unknown) => failure)
    expect(error).toBeInstanceOf(BadRequestError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  [{ access_token: 'tok', token_type: 'Bearer', expires_at: 'tomorrow', operations: [] }, 'sandbox access token response has an expires_at that is not a timestamp'],
  [{ access_token: '', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [] }, 'sandbox access token response has an empty access_token'],
  [{ access_token: 'tok', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [1] }, 'sandbox access token response has a operations[0] that is not a string'],
])('refuses a malformed access token %j', async (body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    await expect(source(api).createAccessToken()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await api.close()
  }
})

it('falls back to the list item when the detail read that fills a missing toolbox URL breaks the contract', async () => {
  const item = { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [] }
  const api = await startFakeApi([
    { status: 200, body: { sandboxes: [item], next_cursor: null } },
    { status: 200, body: ['not a sandbox'] },
  ])
  const client = clientAt(api.url)
  try {
    const page = await client.v2.list()
    expect(page.items.map((sandbox) => [sandbox.id, sandbox.state])).toEqual([['sbx', 'started']])
    expect(api.requests.map((request) => request.path)).toEqual(['/sandboxes', '/sandboxes/sbx'])
  } finally {
    await client.close()
    await api.close()
  }
})

// A field the contract requires but allows to be null must be present: an
// absent key is malformed, an explicit null is valid. Python's twin is
// tests/test_malformed_responses.py.
const BUDGET_NULLS = { monthly_limit_cents: null, period_start_date: null, created_at: null, updated_at: null }
const USAGE = { limit: null, usage: 1 }
const LIMITS = {
  sandboxes: { concurrent: USAGE, total: USAGE }, image_builds: { concurrent: USAGE },
  storage: { volumes: USAGE, images: USAGE, snapshots: USAGE },
  secrets_per_project: USAGE, api_keys_per_project: USAGE, api_keys_per_organization: USAGE,
}
const MODEL = { id: 'm4', name: 'Apple M4', cores: 10, memory_mib: 16384, disk_mib: 244198, sandbox_disk_mib: 235520, max_sandboxes: 2, default_size: null }

it.each([
  ['project budget', (client: Daytona) => client.v2.getProjectBudget(), {}, 'project budget response is missing its monthly_limit_cents'],
  ['project budget', (client: Daytona) => client.v2.getProjectBudget(), { ...BUDGET_NULLS, updated_at: undefined }, 'project budget response is missing its updated_at'],
  ['usage limit', (client: Daytona) => client.v2.getLimits({ organizationId: 'org' }), { ...LIMITS, secrets_per_project: { usage: 1 } }, 'organization limits response is missing its secrets_per_project.limit'],
  ['Mac model', (client: Daytona) => client.v2.macModels.list(), { mac_models: [{ ...MODEL, default_size: undefined }] }, 'Mac model response is missing its default_size'],
  ['Mac price', (client: Daytona) => client.v2.macModels.catalog(), { regions: [{ id: 'r', default_model: 'm4', models: [{ model: MODEL, available: 1 }] }] }, 'Mac catalog response is missing its regions[0].models[0].price'],
] as const)('refuses a %s response without a required nullable key', async (_name, call, body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['project budget', (client: Daytona) => client.v2.getProjectBudget(), BUDGET_NULLS],
  ['organization limits', (client: Daytona) => client.v2.getLimits({ organizationId: 'org' }), LIMITS],
  ['Mac models', (client: Daytona) => client.v2.macModels.list(), { mac_models: [MODEL] }],
  ['Mac catalog', (client: Daytona) => client.v2.macModels.catalog(), { regions: [{ id: 'r', default_model: 'm4', models: [{ model: MODEL, available: 1, price: null }] }] }],
] as const)('reads a %s response whose nullable keys are explicit nulls', async (_name, call, body) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).resolves.toBeDefined()
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['a sandbox label', (client: Daytona) => client.v2.get('sbx'), { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], labels: { team: 1 } }, 'sandbox response has a labels.team that is not a string'],
  ['a create body that is null', (client: Daytona) => client.v2.create('img_1', { waitSeconds: 0 }), null, 'sandbox create response is missing the sandbox id'],
] as const)('refuses %s with BadRequestError', async (_name, call, body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await client.close()
    await api.close()
  }
})

it('refuses a setLabels answer with a non-string label', async () => {
  const api = await startFakeApi([{ status: 200, body: { id: 'src', labels: { team: 1 } } }])
  try {
    await expect(source(api).setLabels({ team: 'a' })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'sandbox labels response has a labels.team that is not a string' })
  } finally {
    await api.close()
  }
})

it.each([
  [{ team: 1 }, 'sandbox response has a labels.team that is not a string'],
  ['team', 'sandbox response has a labels that is not an object'],
] as const)('refuses a sandbox object whose labels are %j, as Python does', async (labels, message) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], labels } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('sbx')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['next_cursor', 'nextCursor'])('refuses a list %s of the wrong type, as Python does', async (key) => {
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], [key]: 5 } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.list()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`${key} that is not a string`) })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['a source whose class is unknown', 'src' as const, undefined],
  ['a source whose class is known', { id: 'src', class: 'macos', status: 'stopped' }, 'macos'],
] as const)('fork({ waitSeconds: 0 }) of %s is one request', async (_name, sourceData, sandboxClass) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'f', status: 'pending', forked_from: 'src', toolbox_proxy_url: 'https://p.example' } }])
  try {
    const fork = await createSandbox(sourceData, createHttpClient(api.url, 'key')).fork({ waitSeconds: 0 })
    // No class read first: the route is the same for every class.
    expect(api.requests.map((request) => [request.method, request.path])).toEqual([['POST', '/sandboxes/src/fork']])
    expect(fork.sandboxClass).toBe(sandboxClass)
  } finally {
    await api.close()
  }
})

it.each([
  ['a timeout_seconds that is a string', { id: 'src', lifecycle_policy: { timeout_seconds: '60' } }, 'sandbox lifecycle policy response has a lifecycle_policy.timeout_seconds that is not a 32-bit integer'],
  ['no lifecycle_policy', { id: 'src' }, 'sandbox lifecycle policy response is missing its lifecycle_policy'],
  ['a body that is not an object', [1], 'sandbox lifecycle policy response is not a JSON object'],
] as const)('refuses a lifecycle echo with %s, as Python does', async (_name, body, message) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    await expect(source(api).resetIdleTimer()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await api.close()
  }
})

// An integer a double cannot carry: JSON.parse reads it as Infinity, which is
// the malformed-response error naming the field. The Python twin pins the same rows.
const HUGE = '1' + '0'.repeat(400)
const networkPolicy = (desired: string, desiredRevision: string, appliedRevision: string) =>
  `{"desired": ${desired}, "desired_revision": ${desiredRevision}, "applied": null, "applied_revision": ${appliedRevision}, "applied_start_id": null, "status": "applied", "error": null}`
const policy = (version: string) => `{"version": ${version}, "block_all": false, "allow_cidrs": [], "allow_domains": []}`
const secrets = (desiredRevision: string, appliedRevision: string, count: string) =>
  `{"secrets": [], "desired_revision": ${desiredRevision}, "applied_revision": ${appliedRevision}, "status": "pending", "error": null, "capture_status": null, "capture_unresolved_count": ${count}}`

it.each([
  ['network policy desired_revision', networkPolicy('null', HUGE, 'null'), (api: FakeApi) => source(api).getNetworkPolicy(), 'desired_revision'],
  ['network policy applied_revision', networkPolicy('null', '1', HUGE), (api: FakeApi) => source(api).getNetworkPolicy(), 'applied_revision'],
  ['network policy desired.version', networkPolicy(policy(HUGE), '1', 'null'), (api: FakeApi) => source(api).getNetworkPolicy(), 'desired.version'],
  ['secrets desired_revision', secrets(HUGE, 'null', 'null'), (api: FakeApi) => source(api).getSecrets(), 'desired_revision'],
  ['secrets applied_revision', secrets('1', HUGE, 'null'), (api: FakeApi) => source(api).getSecrets(), 'applied_revision'],
  ['secrets capture_unresolved_count', secrets('1', 'null', HUGE), (api: FakeApi) => source(api).getSecrets(), 'capture_unresolved_count'],
  ['access token ports[0]', `{"access_token": "t", "token_type": "Bearer", "expires_at": "2030-01-01T00:00:00Z", "operations": [], "ports": [${HUGE}]}`, (api: FakeApi) => source(api).createAccessToken(), 'ports[0]'],
] as const)('refuses %s that a double cannot carry', async (_name, body, call, field) => {
  const api = await startFakeApi([{ status: 200, body, headers: { ETag: '"e"' } }])
  try {
    await expect(call(api)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(field) })
  } finally {
    await api.close()
  }
})

it('refuses a search total a double cannot carry, and accepts one above 2^53 that it carries', async () => {
  const api = await startFakeApi([
    { status: 200, body: `{"sandboxes": [], "total": ${HUGE}, "total_relation": "eq", "next_cursor": null}` },
    { status: 200, body: { sandboxes: [], total: 2 ** 60, total_relation: 'eq', next_cursor: null } },
  ])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.search()).rejects.toMatchObject({ message: 'search response has a total that is not an integer' })
    expect((await client.v2.search()).total).toBe(2 ** 60)
  } finally {
    await client.close()
    await api.close()
  }
})

// The create response's `wait_cap_seconds` is read, never refused (the sandbox
// exists): a finite number, a negative one clamped to 0, anything else 0. The
// Python twin pins the same rows.
it.each([
  ['5', 1], ['5.0', 1], ['7', 1], ['4.5', 2], ['-3', 2], ['"5"', 2], ['null', 2], ['1e400', 2], [HUGE, 2],
] as const)('reads a create wait_cap_seconds of %s as Python does', async (raw, requests) => {
  const detail = { id: 'src', class: 'linux', name: 'src', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'r' }
  const api = await startFakeApi([{ status: 200, body: `{"id": "src", "status": "pending", "wait_cap_seconds": ${raw}}` }, { status: 200, body: detail }])
  const client = clientAt(api.url, { events: { enabled: false } })
  try {
    await client.v2.create('img_1', { waitSeconds: 5 })
    expect(api.requests).toHaveLength(requests)
  } finally {
    await client.close()
    await api.close()
  }
}, 10_000)

it.each([
  [{ sandboxes: [], total_relation: 'eq', next_cursor: null }, 'search response is missing its total'],
  [{ sandboxes: [], total: 0, next_cursor: null }, 'search response is missing its total_relation'],
] as const)('findByName checks every page as search does: %j', async (body, message) => {
  // The Python twin checks the same pages.
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.findByName('x')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['platform', 'default', 'deprecated'].flatMap((flag) => [[flag, 'missing'], [flag, 'not a boolean']] as const))('refuses an image whose %s is %s, as Python does', async (flag, broken) => {
  // The contract requires all three flags on an image and a list item.
  const body: Record<string, unknown> = { ...imageWire({ id: 'img', status: 'ready' }) }
  if (broken === 'missing') delete body[flag]
  else body[flag] = 'yes'
  const api = await startFakeApi([{ status: 200, body }, { status: 200, body: { images: [body], next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.images.get('img')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(flag) })
    await expect(client.v2.images.list()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', message: expect.stringContaining(flag) })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([['missing'], ['null']] as const)('refuses a sandbox whose region_id is %s, as Python does', async (broken) => {
  const body: Record<string, unknown> = { id: 'sbx-region-a', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a' }
  if (broken === 'missing') delete body.region_id
  else body.region_id = null
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('sbx-region-a')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', message: expect.stringContaining('region_id') })
  } finally {
    await client.close()
    await api.close()
  }
})
