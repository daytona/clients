/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs, { access, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import * as archives from '../src/v2/contextArchive.js'
import * as tar from '../src/v2/contextTar.js'
import { ImageBuilder } from '../src/v2/Image.js'
import { BadRequestError, ConflictError } from '../src/v2/errors/DaytonaError.js'
import { contextUploadServer } from './contextUploadServer.js'

let api: Awaited<ReturnType<typeof contextUploadServer>>
let directory: string

beforeEach(async () => {
  api = await contextUploadServer()
  directory = await mkdtemp(join(tmpdir(), 'cleanup-upload-'))
})

it('closes and removes a partial archive when its writer rejects mid-write', async () => {
  // Given a valid source and a writer that fails after writing actual archive bytes.
  await writeFile(join(directory, 'file.txt'), 'local data')
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(join(directory, 'file.txt'), '/file.txt')
  const failure = Object.assign(new Error('storage write failed'), { code: 'ENOSPC' })
  let output: Parameters<typeof tar.writeTar>[0] | undefined
  let filename: string | undefined
  const makeDirectory = fs.mkdtemp.bind(fs)
  jest.spyOn(fs, 'mkdtemp').mockImplementation(async (prefix) => {
    const created = await makeDirectory(prefix)
    filename = join(created, 'context.tar')
    return created
  })
  jest.spyOn(tar, 'writeTar').mockImplementationOnce(async (file) => {
    output = file
    await file.write(new Uint8Array(512))
    throw failure
  })
  // When building, then the original failure propagates after closing and cleanup.
  await expect(api.images.build(builder)).rejects.toBe(failure)
  expect(output?.fd).toBe(-1)
  expect(filename).toBeDefined()
  if (filename === undefined) throw new TypeError('writer did not receive an archive')
  await expect(access(filename)).rejects.toMatchObject({ code: 'ENOENT' })
  await expect(access(dirname(filename))).rejects.toMatchObject({ code: 'ENOENT' })
  expect(api.seen).toEqual([])
})

afterEach(async () => {
  jest.restoreAllMocks()
  await api.close()
  await rm(directory, { recursive: true, force: true })
})

/** Observe actual archive paths without changing their creation or cleanup. */
function trackArchives(): readonly string[] {
  const paths: string[] = []
  const archiveBuilder = archives.archiveBuilder
  const archiveDirectory = archives.archiveDirectory
  jest.spyOn(archives, 'archiveBuilder').mockImplementation(async (builder) => {
    const archive = await archiveBuilder(builder)
    paths.push(archive.path)
    return archive
  })
  jest.spyOn(archives, 'archiveDirectory').mockImplementation(async (context, dockerfile) => {
    const archive = await archiveDirectory(context, dockerfile)
    paths.push(archive.path)
    return archive
  })
  return paths
}

describe.each(['builder', 'directory'] as const)('%s temporary contexts', (kind) => {
  it.each([undefined, 'declare', 'put', 'complete', 'image'] as const)('removes its archive and temporary directory after %s', async (failure) => {
    // Given a real archive, with either a successful API or a failing upload/build leg.
    await writeFile(join(directory, 'Dockerfile'), 'FROM alpine:3\n')
    await writeFile(join(directory, 'file.txt'), 'local data')
    const builder = ImageBuilder.fromRegistry('alpine:3').addLocalFile(join(directory, 'file.txt'), '/file.txt')
    const paths = trackArchives()
    api.controls.failure = failure
    // When the generated archive goes through the real uploader.
    const pending = kind === 'builder' ? api.images.build(builder) : api.images.buildFromContext(directory)
    if (failure === undefined) await expect(pending).resolves.toMatchObject({ id: 'img-local' })
    else await expect(pending).rejects.toBeInstanceOf(failure === 'put' ? BadRequestError : ConflictError)
    // Then the generated tar and its private directory are gone, but sources remain.
    expect(paths).toHaveLength(1)
    await expect(access(paths[0])).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(access(dirname(paths[0]))).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(access(join(directory, 'file.txt'))).resolves.toBeUndefined()
  })
})
