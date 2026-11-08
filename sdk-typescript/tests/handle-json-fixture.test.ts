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
interface CreateCase {
  readonly call: 'create' | 'createFromSnapshot' | 'createMacos'
  readonly source: string
  readonly region?: string
  readonly response: Record<string, unknown>
  readonly handleJson: Record<string, unknown>
}

const fixture = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'sandbox-handle-json.json'), 'utf8')) as {
  readonly detail: Record<string, unknown>
  readonly handleJson: Record<string, unknown>
  readonly createResponses: readonly CreateCase[]
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

// Only what the response carries, an img_ / snap_ id the caller passed, and the
// class the call fixes (macOS): never a name as an id, never a guessed class.
it.each(fixture.createResponses.map((testCase) => [`${testCase.call}-${testCase.source}`, testCase] as const))(
  'a handle from a create response serializes the shared fixture: %s',
  async (_name, testCase) => {
    const api = await startFakeApi([{ status: 200, body: testCase.response }])
    const client = clientAt(api.url)
    try {
      const options = { waitSeconds: 0, ...(testCase.region === undefined ? {} : { region: testCase.region }) }
      const sandbox = testCase.call === 'create' ? await client.v2.create(testCase.source, options)
        : testCase.call === 'createFromSnapshot' ? await client.v2.createFromSnapshot(testCase.source, options)
          : await client.v2.createMacos(testCase.source, options)
      expect(canonical(sandbox.toJSON())).toBe(canonical(testCase.handleJson))
      expect(api.requests).toHaveLength(1)
    } finally {
      await client.close()
      await api.close()
    }
  },
)

it('refuses a create response without the sandbox id before any further request', async () => {
  const api = await startFakeApi([{ status: 200, body: { status: 'pending' } }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.create('img_1', { waitSeconds: 5 })).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'sandbox create response is missing the sandbox id',
    })
    expect(api.requests).toHaveLength(1)
  } finally {
    await client.close()
    await api.close()
  }
})

it('refuses a get response that is not a sandbox before building a handle', async () => {
  const api = await startFakeApi([{ status: 200, body: { unexpected: true } }, { status: 200, body: { class: 'linux', status: 'running' } }])
  const client = clientAt(api.url)
  try {
    for (const message of ['sandbox response is missing its id', 'sandbox response is missing its id']) {
      await expect(client.v2.get('sbx-ts')).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
    }
    // Nothing follows the malformed read (no GET /sandboxes/undefined).
    expect(api.requests.map((request) => request.path)).toEqual(['/sandboxes/sbx-ts', '/sandboxes/sbx-ts'])
  } finally {
    await client.close()
    await api.close()
  }
})
