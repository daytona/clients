/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Image, Snapshot } from '../src/v2/Image.js'
import { ImagesService } from '../src/v2/Images.js'
import { SnapshotsService } from '../src/v2/Snapshots.js'
import { artifactApi, imageWire, snapshotWire } from './artifactFixtures.js'
import { INTERNAL } from '../src/v2/internal'

it('maps image metadata and nested sources without exposing wire fields', async () => {
  // Given a typed response with open vocabularies and an extra server field.
  const wire = {
    ...imageWire({ class: 'future-class', retention: 'archival', origin: 'future-origin', status: 'future-status',
      bytes: 1024, layer_count: 7, disk_mib: 81920,
      source: { type: 'dockerfile', context_object: 'context-one', dockerfile: 'Dockerfile' } }),
    future_field: 'ignored',
  }
  const { http } = artifactApi(wire)
  // When reading the image through the service boundary.
  const image = await new ImagesService(INTERNAL, http).get('img-one')
  // Then the SDK exposes normalized camelCase fields and preserves open vocabularies.
  expect(image).toBeInstanceOf(Image)
  expect(image).toMatchObject({
    sandboxClass: 'future-class', retention: 'unknown', rawRetention: 'archival',
    origin: 'future-origin', status: 'future-status', bytes: 1024, layerCount: 7, diskMib: 81920,
    availableIn: ['region-a', 'region-b'], region: 'region-a',
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-02T00:00:00Z',
  })
  expect(image.source).toEqual({ type: 'dockerfile', dockerfile: 'Dockerfile' })
  expect(image.source).not.toHaveProperty('contextObject')
  for (const key of ['class', 'layer_count', 'cursor', 'future_field', 'rawSandboxClass']) {
    expect(image).not.toHaveProperty(key)
  }
})

it('normalizes absent image metadata to undefined, name and disk size included', () => {
  // Given optional fields explicitly null on the wire.
  const wire = imageWire({ name: null, source: null, bytes: null, layer_count: null, error: null, expires_at: null, disk_mib: null })
  // When converting the response into an SDK model.
  const image = new Image(INTERNAL, wire)
  // Then absent optional values use undefined consistently.
  expect(image).toMatchObject({ name: undefined, diskMib: undefined, source: undefined, bytes: undefined,
    layerCount: undefined, error: undefined, expiresAt: undefined })
})

it('maps a source kind this release does not know to unknown, keeping the wire type', () => {
  // A later API's source kind is no malformed response (as Python).
  const image = new Image(INTERNAL, imageWire({ source: { type: 'oci_layout', uri: 'oci://example' } as never }))
  expect(image.source).toEqual({ type: 'unknown', rawType: 'oci_layout' })
  expect(Object.isFrozen(image.source)).toBe(true)
})

it.each([
  [{ type: 'registry' }, 'image source'],
  [{ type: 7 }, 'source.type'],
  [{ type: '' }, 'source.type'],
  [{ ref: 'alpine' }, 'source.type'],
])('refuses a malformed source %j', (source, field) => {
  // A known kind keeps every check; a type that is not a non-empty string is malformed.
  expect(() => new Image(INTERNAL, imageWire({ source: source as never }))).toThrow(expect.objectContaining({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(field) }))
})

it.each(['linux', 'future-class'])('normalizes snapshot class %s and keeps the raw value', async (sandboxClass) => {
  // Given a snapshot with a known or future class and future kind.
  const { http } = artifactApi(snapshotWire({ class: sandboxClass, kind: 'future-kind', bytes: 2048,
    memory_bytes: 1024, sandbox_id: 'sbx-one' }))
  // When reading the snapshot through the service boundary.
  const snapshot = await new SnapshotsService(INTERNAL, http).get('snap-one')
  // Then enum fallbacks preserve raw values and sizes remain distinct.
  expect(snapshot).toBeInstanceOf(Snapshot)
  expect(snapshot).toMatchObject({ sandboxClass: sandboxClass === 'linux' ? 'linux' : 'unknown',
    rawSandboxClass: sandboxClass, kind: 'unknown', rawKind: 'future-kind',
    bytes: 2048, memoryBytes: 1024, sandboxId: 'sbx-one' })
  expect(snapshot).not.toHaveProperty('class')
})

it('normalizes absent snapshot sizes and source sandbox', () => {
  // Given a snapshot captured without size or source information.
  const wire = snapshotWire({ bytes: null, memory_bytes: null, sandbox_id: null, error: null })
  // When converting the response into an SDK model.
  const snapshot = new Snapshot(INTERNAL, wire)
  // Then optional values are absent, while the known kind remains usable.
  expect(snapshot).toMatchObject({ bytes: undefined, memoryBytes: undefined, sandboxId: undefined,
    error: undefined, kind: 'full', rawKind: 'full' })
})

it.each([
  { name: 'image', create: () => { const wire = imageWire(); return { wire, artifact: new Image(INTERNAL, wire) } } },
  { name: 'snapshot', create: () => { const wire = snapshotWire(); return { wire, artifact: new Snapshot(INTERNAL, wire) } } },
])('keeps $name availability independent of the response array', ({ create }) => {
  // Given an already-created artifact model.
  const { wire, artifact } = create()
  // When the transport's response array is subsequently changed.
  wire.region_ids.push('region-c')
  // Then observed metadata remains the response-time snapshot.
  expect(artifact.availableIn).toEqual(['region-a', 'region-b'])
})
