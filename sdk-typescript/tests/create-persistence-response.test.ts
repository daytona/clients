/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

it.each([
  [true, true], [false, false], [null, undefined], ['invalid', undefined],
] as const)('retains reported persistence %s on create and snapshot restore', async (reported, expected) => {
  // Given creates that report inherited persistence without a detail read,
  const api = await startFakeApi([
    { status: 202, body: { id: 'image', persistent: reported } },
    { status: 202, body: { id: 'snapshot', persistent: reported } },
  ])
  const client = clientAt(api.url)
  try {
    // When creating through each source route,
    const sandboxes = await Promise.all([
      client.v2.create({ waitSeconds: 0 }),
      client.v2.createFromSnapshot('snap', { waitSeconds: 0 }),
    ])
    // Then the server observation is available immediately, without a GET.
    expect(sandboxes.map((sandbox) => sandbox.persistent)).toEqual([expected, expected])
    expect(api.requests.map((request) => request.method)).toEqual(['POST', 'POST'])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  [false, true, false], [true, false, true], [null, true, true], ['invalid', false, false],
] as const)('prefers reported %s to requested %s, falling back when unusable', async (reported, persistent, expected) => {
  // Given competing request and response values,
  const api = await startFakeApi([{ status: 202, body: { id: 'new', persistent: reported } }])
  const client = clientAt(api.url)
  try {
    // When creating before any detail read, then only a boolean response wins.
    const sandbox = await client.v2.create({ persistent, waitSeconds: 0 })
    expect(sandbox.persistent).toBe(expected)
  } finally {
    await client.close()
    await api.close()
  }
})
