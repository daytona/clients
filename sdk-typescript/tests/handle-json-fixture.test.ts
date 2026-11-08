/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

// One SandboxHandleJSON for both SDKs, byte for byte: the fixture is shared
// with the Python suite (sdk-python/tests/test_handle_json_fixture.py).
const fixture = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'sandbox-handle-json.json'), 'utf8')) as {
  readonly detail: Record<string, unknown>
  readonly handleJson: Record<string, unknown>
}

function canonical(value: unknown): string {
  const sort = (input: unknown): unknown => {
    if (Array.isArray(input)) return input.map(sort)
    if (input !== null && typeof input === 'object') {
      return Object.fromEntries(Object.keys(input).sort().map((key) => [key, sort(Reflect.get(input, key))]))
    }
    return input
  }
  return JSON.stringify(sort(value))
}

it('get and fork serialize the shared fixture byte for byte, timestamps verbatim', async () => {
  const api = await startFakeApi([
    { status: 200, body: fixture.detail },
    { status: 202, body: { id: 'sbx-ts', status: 'running', forked_from: 'src', toolbox_proxy_url: fixture.detail.toolbox_proxy_url } },
    { status: 200, body: fixture.detail },
  ])
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.get('sbx-ts')
    const forked = await sandbox.fork({ waitSeconds: 0 })
    expect(canonical(sandbox.toJSON())).toBe(canonical(fixture.handleJson))
    expect(canonical(forked.toJSON())).toBe(canonical(fixture.handleJson))
  } finally {
    await client.close()
    await api.close()
  }
})

it('keeps the handle id and artifact references read-only at run time', async () => {
  const api = await startFakeApi([{ status: 200, body: { ...fixture.detail, image: { id: 'img_1', name: 'base' } } }])
  const client = clientAt(api.url)
  try {
    const sandbox = await client.v2.get('sbx-ts')
    expect(Reflect.set(sandbox, 'id', 'other')).toBe(false)
    expect(sandbox.id).toBe('sbx-ts')
    const image = sandbox.image
    expect(image).toEqual({ id: 'img_1', name: 'base' })
    expect(Object.isFrozen(image)).toBe(true)
  } finally {
    await client.close()
    await api.close()
  }
})
