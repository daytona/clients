/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ImageBuilder } from '../src/v2/Image.js'
import { ImagesService } from '../src/v2/Images.js'
import { SnapshotsService } from '../src/v2/Snapshots.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { artifactApi, imageWire, snapshotWire } from './artifactFixtures.js'
import { INTERNAL } from '../src/v2/internal'

describe.each([
  { name: 'image', create: () => { const fixture = artifactApi(imageWire()); return { ...fixture, service: new ImagesService(INTERNAL, fixture.http) } } },
  { name: 'snapshot', create: () => { const fixture = artifactApi(snapshotWire()); return { ...fixture, service: new SnapshotsService(INTERNAL, fixture.http) } } },
])('$name get options', ({ create }) => {
  beforeEach(() => { jest.useFakeTimers() })
  afterEach(() => { jest.useRealTimers() })
  it.each([
    { seconds: 0.0001, wire: 1, request: 1 },
    { seconds: 1.25, wire: 1250, request: 1250 },
    { seconds: 60, wire: 60000, request: 60000 },
    { seconds: 86400, wire: 60000, request: 65000 },
    { seconds: 0, wire: undefined, request: 0 },
    { seconds: undefined, wire: undefined, request: 0 },
  ])('converts $seconds seconds into a capped wire hold', async ({ seconds, wire, request }) => {
    // Given an artifact service with a shorter default transport timeout.
    const { service, requests } = create()
    // When waiting for readiness with a seconds-based budget.
    await service.get('artifact/one', { wait: 'ready', timeoutSeconds: seconds })
    // Then the hold is the smaller of the budget and 60 s, whatever the
    // shorter request timeout, and the read allows the hold plus network
    // slack, never past the wait's deadline.
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ params: { wait: 'ready', timeout_ms: wire }, timeout: request })
    expect(requests[0]?.path).toContain('artifact%2Fone')
  })

  it('retains the root transport budget when no readiness wait was requested', async () => {
    // Given a service with a default request timeout.
    const { service, requests } = create()
    // When fetching current metadata only.
    await service.get('artifact-one', { timeoutSeconds: 10 })
    // Then a long-poll budget does not disable the ordinary request deadline.
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ params: undefined, timeout: 4321 })
  })

  it.each([-1, NaN, Infinity])('refuses invalid seconds %s before HTTP', async (timeoutSeconds) => {
    // Given an invalid caller budget.
    const { service, requests } = create()
    // When the caller supplies it, even without a wait.
    const pending = service.get('artifact-one', { timeoutSeconds })
    // Then validation is local and consistently typed.
    await expect(pending).rejects.toBeInstanceOf(BadRequestError)
    expect(requests).toEqual([])
  })

  it('rejects the old timeout option before HTTP', async () => {
    // Given a JS-style object retaining the old option alongside a valid key.
    const { service, requests } = create()
    const options = { timeoutSeconds: 1, timeout: 1000 }
    // When the caller fetches an artifact.
    const pending = service.get('artifact-one', options)
    // Then the old key cannot silently remove the requested timeout.
    await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining('timeoutSeconds') })
    expect(requests).toEqual([])
  })
})

it('maps every image list filter and returns only the requested page', async () => {
  // Given a typed image page with a continuation cursor.
  const { http, requests } = artifactApi({ images: [imageWire()], next_cursor: 'next-image' })
  // When listing one filtered page.
  const page = await new ImagesService(INTERNAL, http).list({ cursor: 'first', limit: 2, name: 'base', anonymous: false, sandboxId: 'sbx', platform: 'any' })
  // Then filters use wire names without automatically following the cursor.
  expect(requests).toHaveLength(1)
  expect(requests[0]?.params).toEqual({ cursor: 'first', limit: 2, name: 'base', anonymous: false, sandbox_id: 'sbx', platform: 'any' })
  expect(page).toMatchObject({ items: [{ id: 'img-one', sandboxClass: 'linux' }], nextCursor: 'next-image' })
})

it('maps every snapshot list filter and returns only the requested page', async () => {
  // Given a typed snapshot page with a continuation cursor.
  const { http, requests } = artifactApi({ snapshots: [snapshotWire()], next_cursor: 'next-snapshot' })
  // When listing one filtered page.
  const page = await new SnapshotsService(INTERNAL, http).list({ cursor: 'first', limit: 2, name: 'saved', sandboxId: 'sbx' })
  // Then filters use wire names without automatically following the cursor.
  expect(requests).toHaveLength(1)
  expect(requests[0]?.params).toEqual({ cursor: 'first', limit: 2, name: 'saved', sandbox_id: 'sbx' })
  expect(page).toMatchObject({ items: [{ id: 'snap-one', sandboxClass: 'linux' }], nextCursor: 'next-snapshot' })
})

it.each([
  { name: 'build', run: (images: ImagesService) => images.build(ImageBuilder.fromRegistry('alpine'), { ...{ name: 'base', unknownOption: true } }) },
  { name: 'pull', run: (images: ImagesService) => images.pull('alpine', { ...{ wait: false, timeout: 1000 } }) },
  { name: 'image list', run: (images: ImagesService) => images.list({ ...{ name: 'base', unknownOption: true } }) },
  { name: 'image iterator', run: async (images: ImagesService) => { for await (const image of images.iter({ ...{ name: 'base', unknownOption: true } })) return image } },
  { name: 'snapshot list', run: (_images: ImagesService, snapshots: SnapshotsService) => snapshots.list({ ...{ name: 'saved', unknownOption: true } }) },
  { name: 'snapshot iterator', run: async (_images: ImagesService, snapshots: SnapshotsService) => { for await (const snapshot of snapshots.iter({ ...{ name: 'saved', unknownOption: true } })) return snapshot } },
])('rejects unknown $name options before HTTP', async ({ run }) => {
  // Given options containing a stale or misspelled key.
  const { http, requests } = artifactApi(imageWire())
  // When invoking the corresponding artifact operation.
  const pending = run(new ImagesService(INTERNAL, http), new SnapshotsService(INTERNAL, http))
  // Then malformed options never reach the network.
  await expect(pending).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
  expect(requests).toEqual([])
})
