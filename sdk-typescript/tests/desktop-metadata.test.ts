/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { DesktopSettings, Sandbox } from '../src/v2/index'
import type * as api from '../src/v2/gen/api-types'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { createHttpClient } from '../src/v2/http'
import { assertNever } from '../src/v2/builderText'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'

const detail = {
  id: 'sandbox', class: 'linux', name: 'sandbox', status: 'running',
  cpu: 1, cores_max: 1, memory_mib: 2048, disk_mib: 1024,
  region_id: '', port_map: [], toolbox_proxy_url: 'https://toolbox.example/sandboxes',
}
const configs = [
  { environment: 'xfce', resolution: { width: 1280, height: 720 } },
  { environment: 'gnome', resolution: { width: 1920, height: 1080 } },
  { environment: 'future-desktop', resolution: { width: 1600, height: 900 } },
  undefined, null,
] as const

describe.each(['get', 'list', 'search', 'create', 'wait'] as const)('%s desktop metadata', (operation) => {
  it.each(configs)('maps resolved configuration %j without inventing defaults', async (desktop) => {
    // Given a detail, list item, create operation, or waited detail response.
    const wire = { ...detail, desktop }
    const bodies = {
      get: wire, list: { sandboxes: [wire] }, create: wire, wait: wire,
      search: { sandboxes: [wire], total: 1, total_relation: 'eq' },
    }
    const api = await startFakeApi([{ status: 200, body: bodies[operation] }])
    const client = clientAt(api.url)
    try {
      // When a public acquisition path reads the response.
      let sandbox: Sandbox | undefined
      switch (operation) {
        case 'get': sandbox = await client.v2.get('sandbox'); break
        case 'list': sandbox = (await client.v2.list()).items[0]; break
        case 'search': sandbox = (await client.v2.search()).items[0]; break
        case 'create': sandbox = await client.v2.create({ waitSeconds: 0 }); break
        case 'wait': sandbox = await createSandbox('sandbox', createHttpClient(api.url)).wait({ until: 'started' }); break
        default: assertNever(operation)
      }
      // Then the raw environment survives forward-compatible normalization.
      const expected = desktop == null ? undefined : {
        environment: desktop.environment === 'future-desktop' ? 'unknown' : desktop.environment,
        rawEnvironment: desktop.environment, resolution: desktop.resolution,
      }
      expect(sandbox?.desktop).toEqual(expected)
      expect(sandbox?.toJSON().desktop).toEqual(expected)
      if (expected === undefined) expect(sandbox?.toJSON()).not.toHaveProperty('desktop')
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it('leaves desktop unknown when a batch-wait snapshot omits it without enriching the snapshot', () => {
  // Given the batch-wait response schema's snapshot, which omits desktop.
  // The SDK uses WebSocket state snapshots/per-sandbox reads, not this route;
  // exercise the shared snapshot mapper without inventing an HTTP consumer.
  const batch = { sandboxes: [detail] satisfies [api.Sandbox] } satisfies api.SandboxList
  // When an existing Sandbox snapshot is mapped to a handle.
  const sandbox = createSandbox(batch.sandboxes[0])
  // Then omission remains unknown, never an inferred disabled/default desktop.
  expect(sandbox.desktop).toBeUndefined()
  expect(sandbox.toJSON()).not.toHaveProperty('desktop')
})

it('learns resolved desktop settings from the detail after an abbreviated create response', async () => {
  // Given a create acknowledgement without desktop and a later complete detail.
  const desktop = { environment: 'gnome', resolution: { width: 1920, height: 1080 } }
  const api = await startFakeApi([
    { status: 202, body: { id: 'sandbox', status: 'pending' } },
    { status: 200, body: { ...detail, desktop } },
  ])
  const client = clientAt(api.url)
  try {
    // When create waits for the sandbox's resolved detail.
    const sandbox = await client.v2.create({ desktop: {}, waitSeconds: 60 })
    // Then server-resolved values, not requested defaults, populate the handle.
    expect(sandbox.desktop).toEqual({ ...desktop, rawEnvironment: 'gnome' })
    expect(sentRequest(api, 2, 1).path).toContain('/sandboxes/sandbox?')
  } finally {
    await client.close()
    await api.close()
  }
})

it('detaches and freezes nested metadata when constructing a handle from internal info', () => {
  // Given externally owned normalized metadata (the create-response seam).
  const desktop: DesktopSettings = { environment: 'unknown', rawEnvironment: 'future', resolution: { width: 1600, height: 900 } }
  const sandbox = createSandbox({ sandboxId: 'sandbox', desktop })
  // When a caller tries changing the input, public observation, and JSON copy.
  Reflect.set(desktop.resolution, 'width', 1)
  expect(Reflect.set(sandbox.desktop?.resolution ?? {}, 'height', 1)).toBe(false)
  const json = sandbox.toJSON()
  Reflect.set(json.desktop?.resolution ?? {}, 'height', 2)
  // Then the observed configuration stays unchanged.
  expect(sandbox.desktop).toEqual({ environment: 'unknown', rawEnvironment: 'future', resolution: { width: 1600, height: 900 } })
})

const malformed = [
  true, [], {},
  { environment: null, resolution: { width: 1280, height: 720 } },
  { environment: 1, resolution: { width: 1280, height: 720 } },
  { environment: 'xfce' },
  { environment: 'xfce', resolution: null },
  { environment: 'xfce', resolution: [] },
  { environment: 'xfce', resolution: { width: 1280 } },
  { environment: 'xfce', resolution: { width: '1280', height: 720 } },
  { environment: 'xfce', resolution: { width: 1280, height: 720.5 } },
  { environment: 'xfce', resolution: { width: 2147483648, height: 720 } },
  { environment: 'xfce', resolution: { width: -1, height: 720 } },
  { environment: 'xfce', resolution: { width: 1280, height: -1 } },
] as const

describe('malformed detail desktop', () => {
  it.each(malformed)('refuses %j through response-field validation', async (desktop) => {
    // Given a response whose nested desktop violates the wire schema.
    const api = await startFakeApi([{ status: 200, body: { ...detail, desktop } }])
    const client = clientAt(api.url)
    try {
      // When reading the detail boundary.
      const pending = client.v2.get('sandbox')
      // Then malformed nested values are not exposed or silently defaulted.
      await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('desktop') })
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it.each(malformed)('refuses malformed nested desktop on create: %j', async (desktop) => {
  // Given an accepted sandbox ID with malformed optional response metadata.
  const api = await startFakeApi([{ status: 202, body: { ...detail, desktop } }])
  const client = clientAt(api.url)
  try {
    // When the create mapper reads the optional structured metadata.
    const pending = client.v2.create({ waitSeconds: 0 })
    // Then the nested response contract is enforced, as for detail reads.
    await expect(pending).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('desktop') })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})
