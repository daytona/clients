/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// An error body is read for a code only when it declares application/json, on
// the client, lifecycle and platform-service calls too. The Python twin is
// tests/test_error_media_type.py.

import { ImageNotFoundError, NotFoundError } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'
import type { Daytona } from '../src/Daytona'

const ENVELOPE = '{"code":"IMAGE_NOT_FOUND","message":"no such image","source":"DAYTONA_API"}'

const CALLS: ReadonlyArray<readonly [string, (client: Daytona) => Promise<unknown>]> = [
  ['images.get', (client) => client.v2.images.get('img')],
  ['get', (client) => client.v2.get('sbx')],
]

async function failure(call: (client: Daytona) => Promise<unknown>, contentType: string): Promise<unknown> {
  const api = await startFakeApi([{ status: 404, body: ENVELOPE, headers: { 'content-type': contentType } }])
  const client = clientAt(api.url)
  try {
    return await call(client).then(() => undefined, (error: unknown) => error)
  } finally {
    await client.close()
    await api.close()
  }
}

describe.each(CALLS)('%s', (_name, call) => {
  it.each(['text/plain', 'application/jsonl', 'text/json'])('reads an envelope declared as %s only as the message', async (contentType) => {
    const error = await failure(call, contentType)
    expect(error).toBeInstanceOf(NotFoundError)
    expect(error).not.toBeInstanceOf(ImageNotFoundError)
    // The body is the message, with no code or source (as in Python).
    expect(error).toMatchObject({ statusCode: 404, message: ENVELOPE, code: undefined, source: undefined })
  })

  it.each(['application/json', 'Application/JSON; charset=utf-8'])('reads the code of an envelope declared as %s', async (contentType) => {
    const error = await failure(call, contentType)
    expect(error).toBeInstanceOf(ImageNotFoundError)
    expect(error).toMatchObject({ code: 'IMAGE_NOT_FOUND', source: 'DAYTONA_API', message: 'no such image' })
  })
})
