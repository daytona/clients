/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from 'node:crypto'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { clientAt } from './helpers/client.js'
import { CONTEXT_ID, contextUploadServer } from './contextUploadServer.js'
import { parseTar } from './contextTar.js'

let api: Awaited<ReturnType<typeof contextUploadServer>>
let directory: string

beforeEach(async () => {
  api = await contextUploadServer()
  directory = await mkdtemp(join(tmpdir(), 'builder-upload-'))
})

afterEach(async () => {
  await api.close()
  await rm(directory, { recursive: true, force: true })
})

it('uploads the current local file contents when images.build receives a local builder', async () => {
  // Given a builder whose local file changes after the builder is made.
  const path = join(directory, 'hello.txt')
  await writeFile(path, 'before registration')
  const builder = ImageBuilder.fromRegistry('alpine:3').addLocalFile(path, '/app/')
  await writeFile(path, 'contents at build time')
  // When the builder is submitted through the public image service.
  const image = await api.images.build(builder, { name: 'local-image', region: 'region-a', cpuArch: 'arm64' })
  // Then the tar carries that file and its rendered Dockerfile, not the host path.
  const uploads = api.uploads()
  expect(uploads).toHaveLength(1)
  const tar = uploads[0].body
  const entries = parseTar(tar)
  expect(entries.find((entry) => entry.name === 'Dockerfile')?.contents.toString()).toBe(builder.toDockerfile())
  expect(entries.find((entry) => entry.name === '.daytona-context/1/hello.txt')?.contents.toString()).toBe('contents at build time')
  expect(entries.some((entry) => entry.name.includes(directory))).toBe(false)
  expect(uploads[0].headers.authorization).toBeUndefined()
  expect(api.json('POST', '/build-contexts')).toEqual({
    dockerfile: 'Dockerfile', bytes: tar.length, sha256: createHash('sha256').update(tar).digest('hex'), region_id: 'region-a',
  })
  expect(api.json('POST', `/build-contexts/${CONTEXT_ID}/complete`)).toEqual({ region_id: 'region-c' })
  expect(api.json('POST', '/images')).toEqual({ context_upload_id: CONTEXT_ID, region_id: 'region-c', name: 'local-image', cpu_arch: 'arm64' })
  expect(image.id).toBe('img-local')
})

it('uploads local sources before creating a sandbox when createFromBuild receives a local builder', async () => {
  // Given a root client configured against the local API and a local builder.
  const path = join(directory, 'app.py')
  await writeFile(path, 'print("local")\n')
  const builder = ImageBuilder.fromRegistry('python:3.13-slim').addLocalFile(path, '/app.py')
  const client = clientAt(api.baseUrl)
  try {
    // When the caller builds and creates without waiting for sandbox readiness.
    const sandbox = await client.v2.createFromBuild(builder, { region: 'region-c', waitSeconds: 0 })
    // Then create names the built image, after the verified context build.
    expect(api.json('POST', '/images')).toEqual({ context_upload_id: CONTEXT_ID, region_id: 'region-c' })
    expect(api.json('POST', '/sandboxes')).toMatchObject({ image: 'img-local', region_id: 'region-c' })
    expect(api.seen.filter((request) => request.method === 'POST').map((request) => request.url)).toEqual([
      '/build-contexts', `/build-contexts/${CONTEXT_ID}/complete`, '/images', '/sandboxes',
    ])
    expect(parseTar(api.uploads()[0].body).find((entry) => entry.name === '.daytona-context/1/app.py')?.contents.toString()).toBe('print("local")\n')
    expect(sandbox.id).toBe('sbx-local')
  } finally {
    await client.close()
  }
})

it.each([
  { label: 'unknown options', options: { name: 'local', typo: true }, message: 'typo' },
  { label: 'blank names', options: { name: ' ' }, message: 'name' },
  { label: 'zero expiry', options: { idleExpirySeconds: 0 }, message: 'idleExpirySeconds' },
  { label: 'named expiry', options: { name: 'local', idleExpirySeconds: 60 }, message: 'idleExpirySeconds' },
])('refuses $label before reading local sources or sending requests', async ({ options, message }) => {
  // Given a registered source removed before a build with invalid options.
  const path = join(directory, 'gone.txt')
  await writeFile(path, 'registered')
  const builder = ImageBuilder.fromRegistry('alpine:3').addLocalFile(path, '/gone.txt')
  await rm(path)
  // When validation competes with a source that cannot be read.
  const pending = api.images.build(builder, options)
  // Then the option error wins, and no upload or image request starts.
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  await expect(pending).rejects.toThrow(message)
  expect(api.seen).toEqual([])
})

it('refuses createFromBuild options before reading local sources or sending requests', async () => {
  // Given an unreadable source and a create budget that cannot be used.
  const path = join(directory, 'gone.txt')
  await writeFile(path, 'registered')
  const builder = ImageBuilder.fromRegistry('alpine:3').addLocalFile(path, '/gone.txt')
  await rm(path)
  const client = clientAt(api.baseUrl)
  try {
    // When createFromBuild validates its budget.
    const pending = client.v2.createFromBuild(builder, { waitSeconds: -1 })
    // Then no source-read error masks that validation failure.
    await expect(pending).rejects.toBeInstanceOf(BadRequestError)
    await expect(pending).rejects.toThrow('waitSeconds')
    expect(api.seen).toEqual([])
  } finally {
    await client.close()
  }
})

it('sends a Dockerfile without a context upload when the builder has no local sources', async () => {
  // Given an ordinary Dockerfile builder without any local files.
  const builder = ImageBuilder.fromDockerfile('FROM alpine:3').runCommands('echo hello')
  // When it is submitted.
  await api.images.build(builder)
  // Then the original Dockerfile request path remains unchanged.
  expect(api.json('POST', '/images')).toEqual({ dockerfile: 'FROM alpine:3\nRUN echo hello\n' })
  expect(api.requests('POST', '/build-contexts')).toEqual([])
  expect(api.uploads()).toEqual([])
})
