/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ImageBuilder } from '../src/v2/index'
import type { CreateSandboxOptions, DesktopConfig } from '../src/v2/index'
import { clientAt } from './helpers/client'
import { createSandbox } from './helpers/sandbox'
import { imageWire } from './artifactFixtures'
import { jsonBody, sentRequest, startFakeApi } from './networkPolicyTestServer'

const configs: readonly DesktopConfig[] = [
  {}, { environment: 'xfce' }, { environment: 'gnome' },
  { resolution: { width: 1024, height: 768 } },
  { environment: 'xfce', resolution: { width: 1280, height: 720 } },
  { environment: 'gnome', resolution: { width: 1920, height: 1080 } },
]

describe.each(['image', 'default'] as const)('desktop %s create', (source) => {
  it.each(configs)('sends %j unchanged when opted in', async (desktop) => {
    // Given an accepted create and caller-specified desktop configuration.
    const api = await startFakeApi([{ status: 202, body: { id: 'sandbox', status: 'running' } }])
    const client = clientAt(api.url)
    try {
      // When the image/default create is submitted.
      await client.v2.create(source === 'image' ? 'img_base' : undefined, { desktop, waitSeconds: 0 })
      // Then defaults are server-owned, including the empty-object opt-in.
      const request = sentRequest(api, 1)
      expect([request.method, request.path]).toEqual(['POST', '/sandboxes'])
      expect(jsonBody(request)).toHaveProperty('desktop', desktop)
      if (source === 'default') expect(jsonBody(request)).not.toHaveProperty('image')
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('omits desktop when it was not requested', async () => {
    // Given no desktop option.
    const api = await startFakeApi([{ status: 202, body: { id: 'sandbox', status: 'running' } }])
    const client = clientAt(api.url)
    try {
      // When creating an ordinary sandbox.
      await client.v2.create(source === 'image' ? 'img_base' : undefined, { waitSeconds: 0 })
      // Then no desktop opt-in reaches the server.
      expect(jsonBody(sentRequest(api, 1))).not.toHaveProperty('desktop')
    } finally {
      await client.close()
      await api.close()
    }
  })
})

it('delegates desktop unchanged when creating from a built image', async () => {
  // Given a ready build followed by an accepted sandbox create.
  const desktop = { environment: 'gnome', resolution: { width: 1920, height: 1080 } } satisfies DesktopConfig
  const api = await startFakeApi([
    { status: 202, body: { id: 'img_built' } },
    { status: 200, body: imageWire({ id: 'img_built', status: 'ready' }) },
    { status: 202, body: { id: 'sandbox', status: 'running' } },
  ])
  const client = clientAt(api.url)
  try {
    // When the builder delegates to the image create path.
    await client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), { desktop, waitSeconds: 0 })
    // Then only the sandbox request carries desktop.
    expect(jsonBody(sentRequest(api, 3, 0))).not.toHaveProperty('desktop')
    const request = sentRequest(api, 3, 2)
    expect([request.method, request.path]).toEqual(['POST', '/sandboxes'])
    expect(jsonBody(request)).toMatchObject({ image: 'img_built', desktop })
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  { desktop: { enviroment: 'xfce' } },
  { desktop: { resolution: { width: 1280, height: 720, depth: 24 } } },
  { desktop: null },
  { desktop: { resolution: null } },
])('refuses malformed desktop option keys/objects before a build: %j', async (options) => {
  // Given a plain-JavaScript options object with unknown keys or null objects.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  try {
    // When validating the public input boundary, before any build or create.
    const pending = client.v2.createFromBuild(ImageBuilder.fromRegistry('alpine'), Object.assign<CreateSandboxOptions, object>({}, options))
    // Then the established nested-options mechanism refuses it without I/O.
    await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each(['snapshot', 'fork', 'macos'] as const)('refuses desktop on %s through its option-key check', async (source) => {
  // Given a desktop option supplied to a route that does not support it.
  const api = await startFakeApi([])
  const client = clientAt(api.url)
  const options = { desktop: {}, waitSeconds: 0 }
  try {
    // When validating unsupported create/fork options.
    const pending = source === 'snapshot' ? client.v2.createFromSnapshot('snap_base', options)
      : source === 'macos' ? client.v2.createMacos('macos', options)
        : createSandbox('sandbox').fork(options)
    // Then the option cannot be silently dropped.
    await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('desktop') })
    expect(api.requests).toEqual([])
  } finally {
    await client.close()
    await api.close()
  }
})

it.each([
  { environment: 'invalid' }, { environment: null },
  { resolution: { width: 800, height: 600 } },
  { resolution: { width: null, height: 720 } },
])('leaves desktop value validation to the server for %j', async (desktop) => {
  // Given a plain-JavaScript value outside the typed supported configurations.
  const api = await startFakeApi([{ status: 400, body: { code: 'BAD_REQUEST', message: 'invalid desktop' } }])
  const client = clientAt(api.url)
  const options: CreateSandboxOptions = { waitSeconds: 0 }
  Reflect.set(options, 'desktop', desktop)
  try {
    // When the request reaches server-owned value validation.
    const pending = client.v2.create(options)
    // Then the server error is surfaced and the supplied value was not rewritten.
    await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', statusCode: 400 })
    expect(jsonBody(sentRequest(api, 1))).toHaveProperty('desktop', desktop)
  } finally {
    await client.close()
    await api.close()
  }
})
