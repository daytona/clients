/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { createHttpClient } from '../src/v2/http'
import { imageWire, snapshotWire } from './artifactFixtures'
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
  ['metrics', (_client, api) => withToolbox(api).metrics(), 'expected system metrics: timestamp is required'],
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
  // The registry is not paged: a next_cursor there is an unknown field, ignored.
  ['Mac models with an unknown next_cursor', (client: Daytona) => client.v2.macModels.list(), { mac_models: [MODEL], next_cursor: 5 }],
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

it.each(['search', 'findByName'] as const)('refuses a negative search total (%s)', async (call) => {
  // The contract's total is an int64 with minimum 0.
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: -1, total_relation: 'eq', next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    await expect(call === 'search' ? client.v2.search() : client.v2.findByName('n')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'search response has a negative total' })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
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
    await expect(client.v2.search()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'search response has a total that is not an integer' })
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
    await expect(client.v2.images.list()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(flag) })
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
    await expect(client.v2.get('sbx-region-a')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('region_id') })
  } finally {
    await client.close()
    await api.close()
  }
})

// A sandbox detail's lifecycle policy is checked field by field, as Python's
// generated model reads it (tests/test_malformed_responses.py): integers,
// actions any string (an unknown one kept), deadlines strings.
const LIFECYCLE_DETAIL = { id: 'src', class: 'linux', name: 'src', status: 'stopped', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a' }
it.each([
  ['a string timeout_seconds', { timeout_seconds: '60' }, 'lifecycle_policy.timeout_seconds'],
  ['a numeric on_timeout', { on_timeout: 5 }, 'lifecycle_policy.on_timeout'],
  ['a numeric timeout_at', { timeout_at: 5 }, 'lifecycle_policy.timeout_at'],
] as const)('refuses a lifecycle policy with %s on get and list', async (_name, policy, field) => {
  const detail = { ...LIFECYCLE_DETAIL, lifecycle_policy: policy }
  const api = await startFakeApi([{ status: 200, body: detail }, { status: 200, body: { sandboxes: [detail], next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('src')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(field) })
    await expect(client.v2.list()).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(field) })
    expect(api.requests).toHaveLength(2)
  } finally {
    await client.close()
    await api.close()
  }
})

it('accepts an unknown lifecycle action', async () => {
  const api = await startFakeApi([{ status: 200, body: { ...LIFECYCLE_DETAIL, lifecycle_policy: { on_timeout: 'hibernate', timeout_seconds: 60 } } }])
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.get('src')
    expect(sandbox.lifecyclePolicy).toMatchObject({ onTimeout: 'hibernate', timeoutSeconds: 60 })
  } finally {
    await client.close()
    await api.close()
  }
})

// A timestamp the contract types as a string is refused when it is a number,
// as Python refuses it (tests/test_malformed_responses.py).
it.each([
  ['images.get created_at', (client: Daytona) => client.v2.images.get('img'), { ...imageWire({ id: 'img' }), created_at: 5 }, 'created_at'],
  ['images.get expires_at', (client: Daytona) => client.v2.images.get('img'), { ...imageWire({ id: 'img' }), expires_at: 5 }, 'expires_at'],
  ['images.list updated_at', (client: Daytona) => client.v2.images.list(), { images: [{ ...imageWire({ id: 'img' }), updated_at: 1.5 }], next_cursor: null }, 'updated_at'],
  ['snapshots.get created_at', (client: Daytona) => client.v2.snapshots.get('snap'), { ...snapshotWire(), created_at: 5 }, 'created_at'],
  ['snapshots.list updated_at', (client: Daytona) => client.v2.snapshots.list(), { snapshots: [{ ...snapshotWire(), updated_at: 5 }], next_cursor: null }, 'updated_at'],
] as const)('refuses a numeric artifact timestamp: %s', async (_name, call, body, field) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(call(client)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`has a ${field} that is not a string`) })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['create', 5], ['create', null], ['create', ['running']], ['create', 'missing'],
  ['fork', 5], ['fork', null], ['fork', ['running']], ['fork', 'missing'],
] as const)('%s reads a status that is not a string (%j) as unknown and returns the handle', async (call, status) => {
  // The sandbox exists once its id came back: create and fork return the
  // handle, its state `unknown`, and raise only when the request failed or no
  // id came back. The Python twin is in test_malformed_responses.py.
  const body: Record<string, unknown> = call === 'fork' ? { id: 'sbx', forked_from: 'src', toolbox_proxy_url: 'https://p.example' } : { id: 'sbx' }
  if (status !== 'missing') body.status = status
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    const handle = call === 'create' ? await client.v2.create('img_1', { waitSeconds: 0 }) : await source(api).fork({ waitSeconds: 0 })
    expect(handle.id).toBe('sbx')
    expect(handle.state).toBe('unknown')
    expect(handle.rawState).toBeUndefined()
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

// Every timestamp a mapped response carries is a string, on the preview
// endpoint and signed preview link responses too, as in Python
// (test_a_numeric_timestamp_is_a_contract_error_on_every_model).
const PORT = { port: 8080, public: false, url: 'https://8080-src.preview.invalid', created_at: '2026-01-01T00:00:00Z' }
it.each([
  ['ports.expose', (api: FakeApi) => source(api).ports.expose(8080), { ...PORT, created_at: 5 }, 'created_at'],
  ['ports.listEndpoints', (api: FakeApi) => source(api).ports.listEndpoints(), { ports: [{ ...PORT, created_at: 1.5 }], next_cursor: null }, 'created_at'],
  ['ports.signedPreviewLink', (api: FakeApi) => source(api).ports.signedPreviewLink(8080), { url: 'https://s.preview.invalid', token: `s${'0'.repeat(32)}`, expires_at: 5 }, 'expires_at'],
] as const)('refuses a numeric timestamp: %s', async (_name, call, body, field) => {
  const api = await startFakeApi([{ status: 200, body }])
  try {
    await expect(call(api)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`${field} that is not a string`) })
    expect(api.requests).toHaveLength(1)
  } finally {
    await api.close()
  }
})

// Neither SDK reads the per-item cursor (nextCursor continues the list), so a
// missing or mistyped one is not a contract error, as in Python.
it.each([
  ['images', 'missing'], ['images', 5], ['snapshots', 'missing'], ['snapshots', 5],
] as const)('reads a %s page whose item cursor is %s', async (key, cursor) => {
  const base: Record<string, unknown> = key === 'images' ? { ...imageWire({ id: 'img' }) } : { ...snapshotWire() }
  if (cursor === 'missing') delete base.cursor
  else base.cursor = cursor
  const api = await startFakeApi([{ status: 200, body: { [key]: [base], next_cursor: null } }])
  const client = clientAt(api.url)
  try {
    const page = key === 'images' ? await client.v2.images.list() : await client.v2.snapshots.list()
    expect(page.items).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it('refuses a signed preview link without expires_at, which the contract requires', async () => {
  const api = await startFakeApi([{ status: 200, body: { url: 'https://s.preview.invalid', token: `s${'0'.repeat(32)}` } }])
  try {
    await expect(source(api).ports.signedPreviewLink(8080)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'signed preview url response is missing its expires_at' })
  } finally {
    await api.close()
  }
})

it('refuses an integer too large for a double in a mapped field, as Python does', async () => {
  const body = JSON.stringify({ ...imageWire({ id: 'img' }), bytes: 1 }).replace('"bytes":1', `"bytes":1${'0'.repeat(400)}`)
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.images.get('img')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'image response has a bytes that is not an integer' })
  } finally {
    await client.close()
    await api.close()
  }
})

// The fork exists once its id came back: fork() returns the handle, with the
// source as `forkedFrom` and the toolbox URL from the fork's detail read, never
// the source's. The Python twin is in test_malformed_responses.py.
const FORK_URL = 'https://toolbox.example/fork'
const FORK_BODIES: Readonly<Record<string, Record<string, unknown>>> = {
  'id only': { id: 'f1' },
  'no forked_from': { id: 'f1', status: 'pending', toolbox_proxy_url: FORK_URL },
  'no toolbox_proxy_url': { id: 'f1', status: 'pending', forked_from: 'src' },
  'wrong types': { id: 'f1', status: 5, forked_from: 7, toolbox_proxy_url: ['x'] },
}
const SANDBOX_DETAIL = { id: 'src', class: 'linux', name: 'src', status: 'stopped', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: 'region-a', port_map: [] }
describe.each(Object.keys(FORK_BODIES))('a fork response with %s', (body) => {
  it.each([0, 5])('returns the handle (waitSeconds %d)', async (waitSeconds) => {
    const detail = { ...SANDBOX_DETAIL, id: 'f1', forked_from: 'src', toolbox_proxy_url: 'https://toolbox.example/detail' }
    const accepted = { status: 200, body: FORK_BODIES[body] }
    const api = await startFakeApi(waitSeconds ? [{ status: 200, body: SANDBOX_DETAIL }, accepted, { status: 200, body: detail }] : [accepted])
    try {
      const fork = await source(api).fork({ waitSeconds })
      expect([fork.id, fork.forkedFrom]).toEqual(['f1', 'src'])
      const given = FORK_BODIES[body].toolbox_proxy_url
      expect(fork.toolboxProxyUrl).toBe(typeof given === 'string' ? `${FORK_URL}/f1` : waitSeconds ? 'https://toolbox.example/detail/f1' : undefined)
      expect(api.requests.map((request) => request.method)).toEqual(waitSeconds ? ['GET', 'POST', 'GET'] : ['POST'])
    } finally {
      await api.close()
    }
  })
})

// An accepted artifact create needs only its id: the call follows it whatever
// the acknowledgement's status, as in Python.
it.each([
  ['images.build', (client: Daytona) => client.v2.images.build(ImageBuilder.fromRegistry('alpine')), { id: 'img-one' }],
  ['images.pull', (client: Daytona) => client.v2.images.pull('alpine', { wait: false }), { id: 'img-one' }],
  ['captureImage', (_client: Daytona, api: FakeApi) => source(api).captureImage(), { id: 'img-one', status: 5 }],
  ['createSnapshot', (_client: Daytona, api: FakeApi) => source(api).createSnapshot('snap'), { id: 'snap-one' }],
] as const)('%s needs only the id of its acknowledgement', async (name, call, accepted) => {
  const created = name === 'createSnapshot' ? snapshotWire() : imageWire()
  const api = await startFakeApi([{ status: 200, body: accepted }, { status: 200, body: created }])
  const client = clientAt(api.url)
  try {
    const result = await call(client, api)
    expect(result.id).toBe(created.id)
    expect(api.requests).toHaveLength(2)
  } finally {
    await client.close()
    await api.close()
  }
})

it('returns the create handle when its optional fields are mistyped, as Python does', async () => {
  // Only the id is required once the sandbox exists: every other field that is
  // not a string reads as absent.
  const body = {
    id: 'sbx', status: 'pending', name: 5, toolbox_proxy_url: 7, access_token: 8, access_token_expires_at: 9,
    image: { id: 5, name: 6 }, reservation_id: 10, wait_cap_seconds: 'x',
  }
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.create('img_1', { waitSeconds: 0 })
    expect([sandbox.id, sandbox.state, sandbox.name, sandbox.reservationId, sandbox.toolboxProxyUrl]).toEqual(['sbx', 'creating', undefined, undefined, undefined])
    expect(sandbox.image).toEqual({ id: 'img_1', name: null })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

// The sandbox exists, so the create returns the handle: a token that could not
// be sent as a header is absent (the handle mints its own), and an expiry that
// is not a timestamp is absent (the token is used until a 401), as in Python.
it.each([
  [5, '2030-01-01T00:00:00Z', false, true],
  ['', '2030-01-01T00:00:00Z', false, true],
  [' padded', '2030-01-01T00:00:00Z', false, true],
  ['tok\n', '2030-01-01T00:00:00Z', false, true],
  ['tok', 5, true, false],
  ['tok', 'not a date', true, false],
  // One RFC 3339 rule in both SDKs: date, time and an offset, real values.
  ['tok', '2030', true, false],
  ['tok', '2030-01-01', true, false],
  ['tok', '2030-01-01T00:00:00', true, false],
  ['tok', '2030-02-30T00:00:00Z', true, false],
  ['tok', '2030-01-01T00:00:60Z', true, false],
  ['tok', '2030-01-01T00:00:00+24:00', true, false],
  ['tok', '2030-01-01T02:00:00+02:00', true, true],
  // ASCII digits only: Arabic-Indic digits are not a timestamp.
  ['tok', '\u0662\u0660\u0663\u0660-01-01T00:00:00Z', true, false],
  ['tok', '2030-01-01T00:00:00Z', true, true],
] as const)('reads a create token %j expiring %j only when it can be used', async (token, expiry, keptToken, keptExpiry) => {
  const api = await startFakeApi([{ status: 200, body: { id: 'sbx', status: 'pending', access_token: token, access_token_expires_at: expiry } }])
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.create('img_1', { waitSeconds: 0 })
    expect(sandbox.accessToken).toBe(keptToken ? token : undefined)
    expect(sandbox.accessTokenExpiresAt).toBe(keptExpiry ? expiry : undefined)
  } finally {
    await client.close()
    await api.close()
  }
})

// An empty or blank id is no id: nothing can follow without it, as in Python.
it.each([
  ['create', ''], ['create', '  '], ['fork', ''], ['fork', '  '],
] as const)('refuses a %s response whose id is %j', async (call, id) => {
  const api = await startFakeApi([{ status: 200, body: { id, status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    const pending = call === 'create' ? client.v2.create('img_1', { waitSeconds: 0 }) : source(api).fork({ waitSeconds: 0 })
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: `sandbox ${call} response is missing the sandbox id` })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

// Once the request succeeded the handle is returned however the wait ends: a
// read whose response breaks the contract is a failed read like any other (as
// in Python).
it.each(['create', 'fork'] as const)('returns the handle when a detail breaks the contract during a %s wait', async (call) => {
  const accepted = call === 'fork' ? { id: 'f1', status: 'pending', forked_from: 'src', toolbox_proxy_url: 'https://toolbox.example/fork' } : { id: 'f1', status: 'pending' }
  const broken = { ...SANDBOX_DETAIL, id: 'f1', class: null }
  const planned = call === 'fork'
    ? [{ status: 200, body: SANDBOX_DETAIL }, { status: 200, body: accepted }, { status: 200, body: broken }]
    : [{ status: 200, body: accepted }, { status: 200, body: broken }]
  const api = await startFakeApi(planned)
  const client = clientAt(api.url, { events: { enabled: false } })
  try {
    const pending = call === 'create' ? client.v2.create('img_1', { waitSeconds: 5 }) : source(api).fork({ waitSeconds: 5 })
    await expect(pending).resolves.toMatchObject({ id: 'f1' })
  } finally {
    await client.close()
    await api.close()
  }
})

// Present and not an RFC 3339 timestamp: refused naming the field, never read
// as absent or as a date (as in Python).
describe.each(['timeout_at', 'idle_timeout_at', 'last_activity_at'])('a lifecycle %s', (field) => {
  it.each(['nope', '2026-01-01', '2026-01-01T00:00:00', '2026-01-01 00:00:00Z'])('that is %j is a contract error', async (stamp) => {
    const api = await startFakeApi([{ status: 200, body: { ...SANDBOX_DETAIL, lifecycle_policy: { [field]: stamp } } }])
    const client = clientAt(api.url)
    try {
      await expect(client.v2.get('src')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`has a lifecycle_policy.${field} that is not an RFC 3339 timestamp`) })
    } finally {
      await client.close()
      await api.close()
    }
  })
})

// As in Python.
it.each(['nope', '2026-01-01', '2026-01-01T00:00:00'])('refuses a spot eviction time %j that is not RFC 3339', async (stamp) => {
  const api = await startFakeApi([{ status: 200, body: { ...SANDBOX_DETAIL, spot_evicted_at: stamp } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('src')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('has a spot_evicted_at that is not an RFC 3339 timestamp') })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([['id', ''], ['id', '  '], ['class', ''], ['class', '  ']] as const)('refuses a sandbox whose %s is %j', async (field, value) => {
  const api = await startFakeApi([{ status: 200, body: { ...SANDBOX_DETAIL, [field]: value } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.get('src')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: `sandbox response is missing its ${field}` })
  } finally {
    await client.close()
    await api.close()
  }
})

// Its id is the path id of the read that follows: refused after the one
// request (as in Python).
describe.each(['images.pull', 'images.build', 'captureImage', 'createSnapshot'] as const)('an acknowledgement of %s', (call) => {
  it.each(['', '  '])('with id %j is refused before the read', async (id) => {
    const api = await startFakeApi([{ status: 200, body: { id, status: 'pending' } }])
    const client = clientAt(api.url)
    try {
      const pending = call === 'images.pull' ? client.v2.images.pull('python:3.12', { wait: false })
        : call === 'images.build' ? client.v2.images.build(ImageBuilder.fromRegistry('python:3.12'))
          : call === 'captureImage' ? source(api).captureImage()
            : source(api).createSnapshot('snap')
      await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(/response is missing its id$/) })
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  })
})

// Refused on a read, naming the field; a create response reads it as absent
// (as in Python).
describe.each(['last_activity_at', 'create_timeout_at'])('a sandbox %s', (field) => {
  it.each(['nope', '2026-01-01', '2026-01-01T00:00:00'])('that is %j is refused on a read and absent on a create', async (stamp) => {
    const api = await startFakeApi([
      { status: 200, body: { ...SANDBOX_DETAIL, [field]: stamp } },
      { status: 200, body: { id: 's1', status: 'pending', [field]: stamp } },
    ])
    const client = clientAt(api.url)
    try {
      await expect(client.v2.get('src')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`has a ${field} that is not an RFC 3339 timestamp`) })
      await expect(client.v2.create('img_1', { waitSeconds: 0 })).resolves.toMatchObject({ id: 's1' })
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it('keeps the source id when the fork detail omits forked_from (as in Python)', async () => {
  // SANDBOX_DETAIL carries no forked_from.
  const detail = SANDBOX_DETAIL
  const api = await startFakeApi([
    { status: 200, body: SANDBOX_DETAIL },
    { status: 200, body: { id: 'f1', status: 'pending', toolbox_proxy_url: 'https://toolbox.example/fork' } },
    { status: 200, body: { ...detail, id: 'f1', status: 'stopped' } },
  ])
  const client = clientAt(api.url, { events: { enabled: false } })
  try {
    const fork = await source(api).fork({ waitSeconds: 5 })
    expect([fork.id, fork.forkedFrom]).toEqual(['f1', 'src'])
  } finally {
    await client.close()
    await api.close()
  }
})

// As in Python.
it.each(['images.pull', 'captureImage', 'createSnapshot'] as const)('refuses a %s acknowledgement id of ".." before the read', async (call) => {
  const api = await startFakeApi([{ status: 200, body: { id: '..', status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    const pending = call === 'images.pull' ? client.v2.images.pull('python:3.12', { wait: false })
      : call === 'captureImage' ? source(api).captureImage() : source(api).createSnapshot('snap')
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(/must not be '\.' or '\.\.'$/) })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

const VOLUME = { id: 'vol', name: 'data', type: 'standard', status: 'ready', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
const SECRET = { name: 'KEY', allowed_domains: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
const API_KEY = {
  id: 'k', name: 'ci', project_id: 'p', project_permissions: [], created_at: '2026-01-01T00:00:00Z',
  context: { project_name: 'p', org_id: 'o', org_name: 'o' }, sync_to_v0: true, sync_to_v1: true,
}
const LISTED = { ...SANDBOX_DETAIL, id: 'sbx', cursor: 'c1', toolbox_proxy_url: 'https://toolbox.example/sbx' }
type DateTimeCase = readonly [string, (client: Daytona, api: FakeApi) => Promise<unknown>, string, (field: string, value: string) => unknown]

// Every date-time field the SDK maps, one call per model kind: a string that
// is not an RFC 3339 timestamp (here a bare date) is refused naming the field
// (as in Python).
const DATE_TIMES: readonly DateTimeCase[] = [
  ...['created_at', 'updated_at', 'started_at', 'stopped_at'].map((field): DateTimeCase => [`get ${field}`, (client) => client.v2.get('src'), field, (f, v) => ({ ...SANDBOX_DETAIL, [f]: v })]),
  ['list created_at', (client) => client.v2.list(), 'created_at', (f, v) => ({ sandboxes: [{ ...LISTED, [f]: v }], next_cursor: null })],
  ['search started_at', (client) => client.v2.search(), 'started_at', (f, v) => ({ sandboxes: [{ ...LISTED, [f]: v }], total: 1, total_relation: 'eq', next_cursor: null })],
  ['images.get updated_at', (client) => client.v2.images.get('img'), 'updated_at', (f, v) => ({ ...imageWire({ id: 'img' }), [f]: v })],
  ['images.get expires_at', (client) => client.v2.images.get('img'), 'expires_at', (f, v) => ({ ...imageWire({ id: 'img' }), [f]: v })],
  ['snapshots.get created_at', (client) => client.v2.snapshots.get('snap'), 'created_at', (f, v) => ({ ...snapshotWire(), [f]: v })],
  ...['created_at', 'updated_at', 'last_used_at', 'used_bytes_at'].map((field): DateTimeCase => [`volumes.get ${field}`, (client) => client.v2.volumes.get('data'), field, (f, v) => ({ ...VOLUME, [f]: v })]),
  ['secrets.get created_at', (client) => client.v2.secrets.get('KEY'), 'created_at', (f, v) => ({ ...SECRET, [f]: v })],
  ['ports.expose created_at', (_client, api) => source(api).ports.expose(8080), 'created_at', (f, v) => ({ ...PORT, [f]: v })],
  ...['created_at', 'expires_at', 'last_used_at', 'revoked_at'].map((field): DateTimeCase => [`currentApiKey ${field}`, (client) => client.v2.currentApiKey(), field, (f, v) => ({ ...API_KEY, [f]: v })]),
  ...['created_at', 'updated_at', 'evaluated_at', 'exceeded_at', 'stop_at'].map((field): DateTimeCase => [`project budget ${field}`, (client) => client.v2.getProjectBudget(), field, (f, v) => ({ ...BUDGET_NULLS, [f]: v })]),
]

it.each(DATE_TIMES)('refuses a date-time field that is not RFC 3339: %s', async (_name, call, field, body) => {
  const api = await startFakeApi([{ status: 200, body: body(field, '2026-01-01') }])
  const client = clientAt(api.url)
  try {
    await expect(call(client, api)).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`has a ${field} that is not an RFC 3339 timestamp`) })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

// In a create response the field reads as absent, and a read during the
// create wait that breaks the contract is a failed read: the handle is
// returned (as in Python).
it.each(['created_at', 'updated_at', 'started_at', 'stopped_at'])('keeps the create handle over a bad sandbox %s', async (field) => {
  const accepted = { id: 's1', status: 'pending', [field]: '2026-01-01' }
  const api = await startFakeApi([
    { status: 200, body: accepted },
    { status: 200, body: accepted },
    { status: 200, body: { ...SANDBOX_DETAIL, id: 's1', status: 'pending', [field]: '2026-01-01' } },
  ])
  const client = clientAt(api.url, { events: { enabled: false } })
  try {
    await expect(client.v2.create('img_1', { waitSeconds: 0 })).resolves.toMatchObject({ id: 's1' })
    await expect(client.v2.create('img_1', { waitSeconds: 5 })).resolves.toMatchObject({ id: 's1' })
    expect(api.requests).toHaveLength(3)
  } finally {
    await client.close()
    await api.close()
  }
})
