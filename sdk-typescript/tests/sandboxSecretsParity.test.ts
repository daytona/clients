/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Runtime sandbox-secrets parity.
//
// Mirrored by sdk-python/tests/test_sandbox_secrets_parity.py: the two
// suites pin the same decode, error-mapping, and replace-wire behaviour.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { lazySandbox } from './lazySandbox'

import { Daytona } from '../src/v2/Daytona'
import type { components } from '../src/v2/gen/api'
import {
  BadRequestError,
  ConflictError,
  DaytonaError,
  NotFoundError,
  PreconditionFailedError,
  PreconditionRequiredError,
} from '../src/v2/index'
import type { SandboxSecretsInfo, SandboxClass } from '../src/v2/types'

import { jsonBody, startFakeApi } from './networkPolicyTestServer'

type SecretsCall = 'get' | 'replace'

function call(client: Daytona, secretsCall: SecretsCall): Promise<SandboxSecretsInfo> {
  switch (secretsCall) {
    case 'get':
      return lazySandbox(client, 'sbx').getSecrets()
    case 'replace':
      return lazySandbox(client, 'sbx').replaceSecrets(['TOKEN'], { ifMatch: '"sv-3"' })
    default: {
      const unreachable: never = secretsCall
      throw new Error(`unreachable secrets call: ${String(unreachable)}`)
    }
  }
}

function withApiUrl<T>(apiUrl: string, callback: () => T): T {
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = apiUrl
  try {
    return callback()
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}

type SandboxSecretsWire = components['schemas']['SandboxSecrets']

function secretsBody(overrides: Partial<SandboxSecretsWire> = {}): SandboxSecretsWire {
  return {
    secrets: ['TOKEN'],
    desired_revision: 3,
    applied_revision: null,
    status: null,
    error: null,
    capture_status: null,
    capture_unresolved_count: null,
    ...overrides,
  }
}

async function getWith(body: SandboxSecretsWire): Promise<SandboxSecretsInfo> {
  const api = await startFakeApi([{ status: 200, headers: { ETag: '"sv-3"' }, body }])
  const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
  try {
    return await lazySandbox(client, 'sbx').getSecrets()
  } finally {
    await api.close()
  }
}

describe('sandbox secrets parity', () => {
  it.each([
    [null, null, null],
    ['pending', 2, null],
    ['applied', 3, null],
    ['failed', 2, 'secret bundle was rejected'],
  ])('decodes runtime status %s', async (status, appliedRevision, error) => {
    const info = await getWith(secretsBody({ status, applied_revision: appliedRevision, error }))

    expect([info.status, info.appliedRevision, info.error]).toEqual([status ?? undefined, appliedRevision ?? undefined, error ?? undefined])
    expect(info.desiredRevision).toBe(3)
  })

  it.each([
    [null, null],
    ['complete', 0],
    ['partial', 2],
    ['unresolved', null],
  ])('decodes capture status %s', async (captureStatus, captureUnresolvedCount) => {
    const info = await getWith(secretsBody({ capture_status: captureStatus, capture_unresolved_count: captureUnresolvedCount }))

    expect([info.captureStatus, info.captureUnresolvedCount]).toEqual([captureStatus ?? undefined, captureUnresolvedCount ?? undefined])
  })

  it('decodes absent optional fields as undefined', async () => {
    const info = await getWith({ secrets: [], desired_revision: 0 })

    expect(info).toEqual({ secrets: [], desiredRevision: 0, etag: '"sv-3"', appliedRevision: undefined, status: undefined, error: undefined, captureStatus: undefined, captureUnresolvedCount: undefined })
  })

  it('decodes every contract response property', async () => {
    const contract = JSON.parse(readFileSync(join(__dirname, '..', '..', 'contract', 'api-openapi.json'), 'utf8'))
    const properties = Object.keys(contract.components.schemas.SandboxSecrets.properties).sort()
    const camel = (name: string): string => name.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())
    const fullBody = secretsBody({ applied_revision: 2, status: 'pending', error: 'x', capture_status: 'partial', capture_unresolved_count: 1 })

    expect(Object.keys(fullBody).sort()).toEqual(properties)
    const info = await getWith(fullBody)
    const decoded = Object.keys(info).filter((key) => key !== 'etag').sort()
    expect(decoded).toEqual(properties.map(camel).sort())
    for (const key of decoded) expect(info[key as keyof SandboxSecretsInfo]).toBeDefined()
  })

  it('get maps a missing sandbox to NotFoundError without retrying', async () => {
    const body = { code: 'NOT_FOUND', message: 'sandbox not found', source: 'DAYTONA_API', trace_id: 'trace-404' }
    const api = await startFakeApi([{ status: 404, body }])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    let thrown: unknown
    try {
      await lazySandbox(client, 'sbx').getSecrets()
    } catch (error) {
      thrown = error
    } finally {
      await api.close()
    }

    expect(thrown).toBeInstanceOf(NotFoundError)
    const error = thrown as DaytonaError
    expect([error.statusCode, error.code, error.source, error.traceId]).toEqual([404, 'NOT_FOUND', 'DAYTONA_API', 'trace-404'])
    expect(api.requests).toHaveLength(1)
  })

  it.each([
    [412, 'STALE_SECRETS_REVISION', ['TOKEN'], PreconditionFailedError],
    [409, 'SECRETS_TRANSITION_IN_PROGRESS', ['TOKEN'], ConflictError],
    [409, 'CONFLICT', ['TOKEN'], ConflictError],
    [400, 'BAD_REQUEST', ['TOKEN', 'TOKEN'], BadRequestError],
    [404, 'NOT_FOUND', ['TOKEN'], NotFoundError],
    [428, 'IF_MATCH_REQUIRED', ['TOKEN'], PreconditionRequiredError],
  ] as const)('maps HTTP %i %s without retrying', async (status, code, secrets, expected) => {
    const body = { code, message: `${code.toLowerCase()} from the api`, source: 'DAYTONA_API', trace_id: `trace-${status}` }
    const api = await startFakeApi([{ status, body }])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    let thrown: unknown
    try {
      await lazySandbox(client, 'sbx').replaceSecrets(secrets, { ifMatch: '"sv-3"' })
    } catch (error) {
      thrown = error
    } finally {
      await api.close()
    }

    expect(thrown).toBeInstanceOf(expected)
    const error = thrown as DaytonaError
    expect([error.statusCode, error.code, error.source, error.traceId]).toEqual([status, code, 'DAYTONA_API', `trace-${status}`])
    expect(api.requests).toHaveLength(1)
    // Duplicates are the server's call: the SDK forwards the list verbatim.
    expect(jsonBody(api.requests[0]!)).toEqual({ secrets })
  })

  it('refuses missing secrets locally instead of sending an empty body', async () => {
    const api = await startFakeApi([])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const missing = JSON.parse('null')
    let thrown: unknown
    try {
      await lazySandbox(client, 'sbx').replaceSecrets(missing, { ifMatch: '"sv-3"' })
    } catch (error) {
      thrown = error
    } finally {
      await api.close()
    }

    expect(thrown).toBeInstanceOf(BadRequestError)
    const error = thrown as DaytonaError
    expect([error.statusCode, error.code, error.source]).toEqual([400, 'BAD_REQUEST', 'SDK'])
    expect(api.requests).toHaveLength(0)
  })

  it.each(['linux', 'macos'] as const)('root replace validates input for a %s sandbox before sending a request', async (sandboxClass: SandboxClass) => {
    const api = await startFakeApi([])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    const sandbox = lazySandbox(client, { sandboxId: 'sbx', sandboxClass })
    const notAnArray = JSON.parse('"TOKEN"')
    let thrown: unknown
    try {
      await sandbox.replaceSecrets(notAnArray, { ifMatch: '"sv-3"' })
    } catch (error) {
      thrown = error
    } finally {
      await api.close()
    }

    expect(thrown).toBeInstanceOf(BadRequestError)
    const error = thrown as DaytonaError
    expect([error.statusCode, error.code, error.source]).toEqual([400, 'BAD_REQUEST', 'SDK'])
    expect(api.requests).toHaveLength(0)
  })

  it.each([
    ['get', 200],
    ['replace', 202],
  ] as const)('%s: a response without ETag is an SDK error, not a tag rebuilt from the revision', async (secretsCall, status) => {
    const api = await startFakeApi([{ status, body: secretsBody() }])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    let thrown: unknown
    try {
      await call(client, secretsCall)
    } catch (error) {
      thrown = error
    } finally {
      await api.close()
    }

    expect(thrown).toBeInstanceOf(BadRequestError)
    const error = thrown as DaytonaError
    expect([error.statusCode, error.code, error.source, error.message]).toEqual([400, 'BAD_REQUEST', 'SDK', 'sandbox secrets response is missing the ETag header'])
    expect(api.requests).toHaveLength(1)
  })

  it('detach-all sends an explicit empty array with If-Match', async () => {
    const api = await startFakeApi([
      { status: 202, headers: { ETag: '"sv-4"' }, body: secretsBody({ secrets: [], desired_revision: 4, applied_revision: 3, status: 'pending' }) },
    ])
    const client = withApiUrl(api.url, () => new Daytona({ apiKey: 'key' }))
    let info: SandboxSecretsInfo
    try {
      info = await lazySandbox(client, 'sbx').replaceSecrets([], { ifMatch: '"sv-3"' })
    } finally {
      await api.close()
    }

    const request = api.requests[0]!
    expect([request.method, request.path, request.headers['if-match']]).toEqual(['PUT', '/sandboxes/sbx/secrets', '"sv-3"'])
    expect(jsonBody(request)).toEqual({ secrets: [] })
    expect([info.secrets, info.desiredRevision, info.etag]).toEqual([[], 4, '"sv-4"'])
    expect(info).not.toHaveProperty('httpStatus')
  })
})
