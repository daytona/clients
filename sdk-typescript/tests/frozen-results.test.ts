/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { imageWire, snapshotWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'

// SDK-owned results are frozen at run time, envelopes and arrays included;
// JSON fields are deep-frozen. Live handles (Sandbox) are not frozen: their
// metadata refreshes.
const TIME = '2026-01-01T00:00:00Z'
const MAC = { cores: 4, memory_mib: 8192, disk_mib: 81920 }
const MODEL = { id: 'm4', name: 'Apple M4', cores: 10, memory_mib: 16384, disk_mib: 244198, sandbox_disk_mib: 235520, max_sandboxes: 2, default_size: MAC }
const RESERVATION = {
  id: 'res', org_id: 'o', project_id: 'p', region_id: 'r', status: 'active', origin: 'explicit', auto_renew: true,
  sandboxes_per_mac: 2, slots_used: 0, period_n: 1, period_seconds: 86400, renewal_pending: false, created_at: TIME, resources_used: MAC,
}
const VOLUME = { id: 'vol', name: 'data', type: 'disk', status: 'ready', created_at: TIME, updated_at: TIME, options: { bucket: 'b', nested: { depth: [1] } } }
const SECRET = { name: 'KEY', allowed_domains: ['example.com'], created_at: TIME, updated_at: TIME }
const USAGE = { limit: 1, usage: 0 }
const DETAIL = { id: 'sbx', class: 'linux', name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], toolbox_proxy_url: 'https://p.example' }

function deeplyFrozen(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return true
  return Object.isFrozen(value) && Object.values(value).every(deeplyFrozen)
}

it.each([
  ['images.list', (client: Daytona) => client.v2.images.list(), { images: [imageWire()], next_cursor: null }],
  ['snapshots.list', (client: Daytona) => client.v2.snapshots.list(), { snapshots: [snapshotWire()], next_cursor: null }],
  ['volumes.list', (client: Daytona) => client.v2.volumes.list(), { volumes: [VOLUME], next_cursor: null }],
  ['volumes.get', (client: Daytona) => client.v2.volumes.get('vol'), VOLUME],
  ['secrets.list', (client: Daytona) => client.v2.secrets.list(), { secrets: [SECRET], next_cursor: null }],
  ['secrets.get', (client: Daytona) => client.v2.secrets.get('KEY'), SECRET],
  ['reservations.list', (client: Daytona) => client.v2.reservations.list(), { reservations: [RESERVATION], next_cursor: null }],
  ['reservations.createBulk', (client: Daytona) => client.v2.reservations.createBulk({ region: 'r', count: 1 }), { requested: 1, reserved: 1, reservations: [RESERVATION] }],
  ['macModels.list', (client: Daytona) => client.v2.macModels.list(), { mac_models: [MODEL] }],
  ['macModels.catalog', (client: Daytona) => client.v2.macModels.catalog(), { regions: [{ id: 'r', default_model: 'm4', models: [{ model: MODEL, available: 1, price: null }] }] }],
  ['listAuditActions', (client: Daytona) => client.v2.listAuditActions(), { targets: [{ target_type: 'sandbox', actions: ['create'] }] }],
  ['getProjectBudget', (client: Daytona) => client.v2.getProjectBudget(), { monthly_limit_cents: 1, period_start_date: null, created_at: null, updated_at: null }],
  ['getLimits', (client: Daytona) => client.v2.getLimits({ organizationId: 'o' }), {
    sandboxes: { concurrent: USAGE, total: USAGE }, image_builds: { concurrent: USAGE },
    storage: { volumes: USAGE, images: USAGE, snapshots: USAGE }, secrets_per_project: USAGE, api_keys_per_project: USAGE, api_keys_per_organization: USAGE,
  }],
  ['images.get (a class model)', (client: Daytona) => client.v2.images.get('img'), imageWire()],
] as const)('freezes what %s returns, at every depth', async (_name, call, body) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    expect(deeplyFrozen(await call(client))).toBe(true)
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  ['list', (client: Daytona) => client.v2.list(), { sandboxes: [DETAIL], next_cursor: null }],
  ['search', (client: Daytona) => client.v2.search(), { sandboxes: [DETAIL], next_cursor: null, total: 1, total_relation: 'eq' }],
  ['findByName', (client: Daytona) => client.v2.findByName('sbx'), { sandboxes: [DETAIL], next_cursor: null, total: 1, total_relation: 'eq' }],
] as const)('freezes the %s envelope and its array, never the live handles in it', async (_name, call, body) => {
  const api = await startFakeApi([{ status: 200, body }])
  const client = clientAt(api.url)
  try {
    const result = await call(client)
    const items = Array.isArray(result) ? result : (result as { readonly items: readonly unknown[] }).items
    expect(Object.isFrozen(result) && Object.isFrozen(items)).toBe(true)
    expect(Object.isFrozen(items[0])).toBe(false)
  } finally {
    await client.close()
    await api.close()
  }
})

it('freezes preview endpoints and links', async () => {
  const api = await startFakeApi([
    { status: 200, body: { port: 8080, public: true, url: 'https://p', created_at: TIME } },
    { status: 200, body: { ports: [{ port: 8080, public: true, url: 'https://p', created_at: TIME }], next_cursor: null } },
    // previewLink: the endpoint, then its preview URL.
    { status: 200, body: { port: 8081, public: false, url: 'https://p2', created_at: TIME } },
    { status: 200, body: { url: 'https://p2', token: 'preview-token' } },
    { status: 200, body: { url: 'https://signed', token: 'signed-token', expires_at: '2099-01-01T00:00:00Z' } },
  ])
  try {
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'running' }, createHttpClient(api.url, 'key'), undefined, undefined, undefined, { accessToken: 'caller-token' })
    expect(Object.isFrozen(await sandbox.ports.expose(8080, { public: true }))).toBe(true)
    expect(deeplyFrozen(await sandbox.ports.listEndpoints())).toBe(true)
    const link = await sandbox.ports.previewLink(8081)
    expect(link).toMatchObject({ url: 'https://p2', token: 'preview-token' })
    expect(deeplyFrozen(link)).toBe(true)
    const signed = await sandbox.ports.signedPreviewLink(8081)
    expect(signed).toMatchObject({ url: 'https://signed', token: 'signed-token' })
    expect(deeplyFrozen(signed)).toBe(true)
  } finally {
    await api.close()
  }
})

const POLICY = {
  desired: { version: 2, block_all: false, allow_cidrs: ['1.2.3.4/32'], allow_domains: ['api.example.com'] },
  desired_revision: 7,
  applied: { version: 2, block_all: false, allow_cidrs: [], allow_domains: ['a.example.com'] },
  applied_revision: 6,
}
const SECRETS = { secrets: ['KEY'], desired_revision: 3, applied_revision: 3 }
const ETAG = { ETag: '"e"' }
const METRICS = { timestamp: TIME, cpuCount: 1, cpuUtilization: 0.5, memoryUsageBytes: 1, memoryTotalBytes: 2, diskUsageBytes: 1, diskTotalBytes: 2 }
type SandboxCall = (sandbox: ReturnType<typeof createSandbox>) => Promise<unknown>

it.each([
  ['getNetworkPolicy', (sandbox) => sandbox.getNetworkPolicy(), { status: 200, headers: ETAG, body: POLICY }],
  ['updateNetworkPolicy', (sandbox) => sandbox.updateNetworkPolicy({ blockAll: false }, { ifMatch: '"e"' }), { status: 200, headers: ETAG, body: POLICY }],
  ['resetNetworkPolicy', (sandbox) => sandbox.resetNetworkPolicy({ ifMatch: '"e"' }), { status: 200, headers: ETAG, body: POLICY }],
  ['getSecrets', (sandbox) => sandbox.getSecrets(), { status: 200, headers: ETAG, body: SECRETS }],
  ['replaceSecrets', (sandbox) => sandbox.replaceSecrets(['KEY'], { ifMatch: '"e"' }), { status: 200, headers: ETAG, body: SECRETS }],
  ['docker.status', (sandbox) => sandbox.docker.status(), { status: 200, body: { owner: 'daemon', state: 'ready' } }],
  ['docker.waitUntilReady', (sandbox) => sandbox.docker.waitUntilReady(), { status: 200, body: { owner: 'daemon', state: 'ready' } }],
  ['volumes.pull', (sandbox) => sandbox.volumes.pull('vol', { requestId: 'r' }), { status: 200, body: { requestId: 'r', status: 'completed', manifestId: null } }],
  ['metrics', (sandbox) => sandbox.metrics(), { status: 200, body: METRICS }],
] as ReadonlyArray<readonly [string, SandboxCall, { readonly status: number; readonly headers?: Record<string, string>; readonly body: unknown }]>)('freezes what sandbox.%s returns, at every depth', async (_name, call, reply) => {
  const api = await startFakeApi([reply])
  try {
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'running' }, createHttpClient(api.url, 'key'), undefined, undefined, undefined, { toolboxUrl: api.url, accessToken: 'tok' })
    const result = await call(sandbox)
    expect(result).toBeDefined()
    expect(deeplyFrozen(result)).toBe(true)
  } finally {
    await api.close()
  }
})

it.each([
  ['DockerNotReadyError', { owner: 'daemon', state: 'failed', reason: 'boom' }],
  ['DockerWaitTimeoutError', { owner: 'daemon', state: 'starting' }],
] as const)('freezes the status a %s carries', async (name, body) => {
  // `failed` ends the wait at once; `starting` keeps it polling until the 1 s budget runs out.
  const api = await startFakeApi(Array.from({ length: 30 }, () => ({ status: 200, body })))
  try {
    const sandbox = createSandbox({ id: 'sbx', class: 'linux', status: 'running' }, createHttpClient(api.url, 'key'), undefined, undefined, undefined, { toolboxUrl: api.url, accessToken: 'tok' })
    const error: unknown = await sandbox.docker.waitUntilReady({ timeoutSeconds: 1 }).catch((caught: unknown) => caught)
    expect(error).toMatchObject({ name })
    const status = (error as { readonly status?: unknown }).status
    expect(status).toMatchObject({ rawState: body.state })
    expect(Object.isFrozen(status)).toBe(true)
  } finally {
    await api.close()
  }
}, 10_000)

describe('deepFrozen', () => {
  // Imported here so the walk itself is tested on values no response mapper would build.
  const load = async () => (await import('../src/v2/frozen')).deepFrozen

  it('freezes a value nested 10,000 levels deep without exhausting the stack', async () => {
    const deepFrozen = await load()
    let deep: Record<string, unknown> = {}
    const root = deep
    for (let level = 0; level < 10_000; level++) {
      const next: Record<string, unknown> = {}
      deep.child = next
      deep = next
    }
    expect(deepFrozen(root)).toBe(root)
    expect(Object.isFrozen(deep)).toBe(true)
  })

  it('walks into an object that is already frozen', async () => {
    const deepFrozen = await load()
    const leaf = {}
    const inner = { leaf }
    const value = deepFrozen({ outer: Object.freeze({ inner }) })
    expect(Object.isFrozen(value) && Object.isFrozen(value.outer)).toBe(true)
    expect(Object.isFrozen(inner) && Object.isFrozen(leaf)).toBe(true)
  })

  it('ends on a cycle', async () => {
    // A synchronous loop cannot be interrupted by a Jest timeout, so the cyclic
    // freeze runs in a worker the test terminates: a regression fails in seconds.
    const { Worker } = await import('node:worker_threads')
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const ts = (await import('typescript')).default
    const source = readFileSync(join(__dirname, '..', 'src', 'v2', 'frozen.ts'), 'utf8')
    const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    const script = `
      const module = { exports: {} }
      ;(function (exports, module) { ${compiled} })(module.exports, module)
      const cyclic = { list: [] }
      cyclic.self = cyclic
      cyclic.list.push(cyclic)
      module.exports.deepFrozen(cyclic)
      require('node:worker_threads').parentPort.postMessage(Object.isFrozen(cyclic) && Object.isFrozen(cyclic.list))
    `
    const worker = new Worker(script, { eval: true })
    try {
      const outcome = await new Promise<unknown>((resolve, reject) => {
        const timer = setTimeout(() => resolve('timed out'), 4_000)
        worker.once('message', (value: unknown) => { clearTimeout(timer); resolve(value) })
        worker.once('error', (error: unknown) => { clearTimeout(timer); reject(error) })
      })
      expect(outcome).toBe(true)
    } finally {
      await worker.terminate()
    }
  }, 10_000)

  it('freezes a volume whose options are nested 10,000 levels deep', async () => {
    // Sent as text: the fake server's JSON.stringify would itself recurse.
    const options = `${'{"child":'.repeat(10_000)}{"leaf":true}${'}'.repeat(10_000)}`
    const body = JSON.stringify({ ...VOLUME, options: null }).replace('"options":null', `"options":${options}`)
    const api = await startFakeApi([{ status: 200, body }])
    const client = clientAt(api.url)
    try {
      const volume = await client.v2.volumes.get('vol')
      expect(Object.isFrozen(volume.options)).toBe(true)
    } finally {
      await client.close()
      await api.close()
    }
  })
})
