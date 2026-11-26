/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'
import type { SearchSandboxesOptions } from '../src/v2/index'

it.each([
  [true, 'true'],
  [false, 'false'],
  [undefined, undefined],
  [null, undefined],
] as const)('sends the persistence filter when it is %s', async (persistent, expected) => {
  // Given
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: 0, total_relation: 'eq' } }])
  const client = clientAt(api.url)
  const options: SearchSandboxesOptions = persistent === null ? {} : { persistent }
  if (persistent === null) Reflect.set(options, 'persistent', null)
  try {
    // When
    await client.v2.search(options)
    // Then
    const query = new URL(sentRequest(api, 1).path, api.url).searchParams
    expect(query.getAll('persistent')).toEqual(expected === undefined ? [] : [expected])
  } finally {
    await client.close()
    await api.close()
  }
})

it('omits the persistence filter when no options are supplied', async () => {
  // Given
  const api = await startFakeApi([{ status: 200, body: { sandboxes: [], total: 0, total_relation: 'eq' } }])
  const client = clientAt(api.url)
  try {
    // When
    await client.v2.search()
    // Then
    expect(new URL(sentRequest(api, 1).path, api.url).searchParams.has('persistent')).toBe(false)
  } finally {
    await client.close()
    await api.close()
  }
})
