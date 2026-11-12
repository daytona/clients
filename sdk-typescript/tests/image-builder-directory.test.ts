/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, rm, utimes, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { contextUploadServer } from './contextUploadServer.js'
import { parseTar } from './contextTar.js'

let api: Awaited<ReturnType<typeof contextUploadServer>>
let directory: string

beforeEach(async () => {
  api = await contextUploadServer()
  directory = await mkdtemp(join(tmpdir(), 'directory-upload-'))
})

afterEach(async () => {
  await api.close()
  await rm(directory, { recursive: true, force: true })
})

it.each([
  { options: { name: ' ' }, message: 'name' },
  { options: { dockerfile: '\u001c\u0085' }, message: 'dockerfile must be a non-empty path' },
  { options: { dockerfile: 'Dockerfile', dockerFile: 'typo' }, message: 'dockerFile' },
])('validates directory options before reading an unavailable directory ($message)', async ({ options, message }) => {
  // Given a directory that is no longer available and unusable build options.
  await rm(directory, { recursive: true })
  // When an attempted directory build could otherwise fail on its source path.
  const pending = api.images.buildFromContext(directory, options)
  // Then argument validation wins, before archive creation or upload.
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  await expect(pending).rejects.toThrow(message)
  expect(api.seen).toEqual([])
})

it.each([true, false])('uses the Dockerfile-specific ignore file when present (%s), keeping build metadata', async (specific) => {
  // Given competing ignore rules and a custom Dockerfile in a subdirectory.
  await mkdir(join(directory, 'build'))
  const dockerfile = 'build/Customfile'
  await writeFile(join(directory, dockerfile), 'FROM alpine:3\nCOPY . /app\n')
  await writeFile(join(directory, '.dockerignore'), 'root-only.txt\nbuild\n.dockerignore\n')
  await writeFile(join(directory, 'root-only.txt'), 'root excludes this')
  await writeFile(join(directory, 'specific-only.txt'), 'specific excludes this')
  await writeFile(join(directory, 'keep.txt'), 'always ordinary content')
  if (specific) await writeFile(join(directory, `${dockerfile}.dockerignore`), 'specific-only.txt\nbuild\n.dockerignore\n')
  // When a directory is built, rather than a prebuilt tar.
  await api.images.buildFromContext(directory, { dockerfile })
  // Then only the selected ignore file controls exclusions; metadata survives.
  const names = parseTar(api.uploads()[0].body).filter((entry) => entry.type !== '5').map((entry) => entry.name)
  expect(names).toContain(dockerfile)
  expect(names).toContain('keep.txt')
  expect(names.includes('root-only.txt')).toBe(specific)
  expect(names.includes('specific-only.txt')).toBe(!specific)
  expect(names).toContain(specific ? `${dockerfile}.dockerignore` : '.dockerignore')
  expect(names).toContain('.dockerignore')
  expect(api.json('POST', '/build-contexts')).toMatchObject({ dockerfile })
})

it('declares the same archive digest for unchanged directory contents despite filesystem timestamps', async () => {
  // Given files written out of lexical order and a default Dockerfile.
  await writeFile(join(directory, 'z.txt'), 'last')
  await writeFile(join(directory, 'Dockerfile'), 'FROM alpine:3\n')
  await writeFile(join(directory, 'a.txt'), 'first')
  // When the directory is built twice, after changing only source timestamps.
  await api.images.buildFromContext(directory)
  await utimes(join(directory, 'a.txt'), 1_234, 5_678)
  await api.images.buildFromContext(directory)
  // Then both declarations describe exactly the same uploaded tar bytes.
  const uploads = api.uploads()
  expect(uploads).toHaveLength(2)
  expect(uploads[0].body).toEqual(uploads[1].body)
  const digest = createHash('sha256').update(uploads[0].body).digest('hex')
  expect(api.declarations()).toEqual([
    { dockerfile: 'Dockerfile', bytes: uploads[0].body.length, sha256: digest },
    { dockerfile: 'Dockerfile', bytes: uploads[1].body.length, sha256: digest },
  ])
})
