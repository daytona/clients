/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The SDK follows no redirect, in every transport: a 3xx answer is a
// DaytonaError carrying its status (source SDK), and the Location is never
// requested. The Python twin is tests/test_redirects.py.

import { DaytonaError, NotFoundError } from '../src/v2/index'
import { forceFetchAdapterForTests } from '../src/v2/boundedAdapter'
import { HttpTransport } from '../src/v2/http'
import { errorFromFetchResponse } from '../src/v2/errors/DaytonaError'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { FakeApi } from './networkPolicyTestServer'

async function redirecting(status: number): Promise<FakeApi> {
  // The redirect target would answer: a follower would turn the 3xx into a 404.
  return startFakeApi([
    { status, body: '', headers: { location: '/elsewhere', 'content-type': 'text/plain' } },
    { status: 404, body: { code: 'NOT_FOUND', message: 'followed' } },
  ])
}

function expectRedirectError(error: unknown, status: number): void {
  expect(error).toBeInstanceOf(DaytonaError)
  expect(error).not.toBeInstanceOf(NotFoundError)
  expect(error).toMatchObject({
    statusCode: status,
    source: 'SDK',
    message: `HTTP ${status}: unexpected redirect (the SDK does not follow redirects)`,
  })
}

describe.each([false, true])('a 3xx answer (fetch adapter: %s)', (forceFetch) => {
  beforeEach(() => forceFetchAdapterForTests(forceFetch))
  afterEach(() => forceFetchAdapterForTests(false))

  it.each([301, 302, 303, 307, 308])('%s is a typed error and is never followed', async (status) => {
    const api = await redirecting(status)
    const client = clientAt(api.url)
    try {
      const error = await client.v2.images.get('img').then(() => undefined, (failure: unknown) => failure)
      expectRedirectError(error, status)
      expect(api.requests.map((request) => request.path)).toEqual(['/images/img'])
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it('HttpTransport.fetch refuses a 3xx without following it', async () => {
  const api = await redirecting(307)
  try {
    const error = await new HttpTransport(api.url, 'k').fetch('/thing').then(() => undefined, (failure: unknown) => failure)
    expectRedirectError(error, 307)
    expect(api.requests).toHaveLength(1)
  } finally {
    await api.close()
  }
})

it('postRaw does not hand a 3xx to its caller', async () => {
  const api = await redirecting(302)
  try {
    const error = await new HttpTransport(api.url, 'k').postRaw('/thing', {}).then(() => undefined, (failure: unknown) => failure)
    expectRedirectError(error, 302)
    expect(api.requests).toHaveLength(1)
  } finally {
    await api.close()
  }
})

it('a JSON body on a 3xx does not choose the error', async () => {
  const error = await errorFromFetchResponse(new Response('{"code":"NOT_FOUND","message":"x"}', { status: 302, headers: { 'content-type': 'application/json' } }))
  expectRedirectError(error, 302)
})

it("a browser's opaque redirect is the same error", async () => {
  const opaque = { status: 0, type: 'opaqueredirect', body: null, headers: new Headers() } as unknown as Response
  const error = await errorFromFetchResponse(opaque)
  expect(error).toBeInstanceOf(DaytonaError)
  expect(error).toMatchObject({ statusCode: undefined, source: 'SDK', message: 'HTTP redirect: unexpected redirect (the SDK does not follow redirects)' })
})
