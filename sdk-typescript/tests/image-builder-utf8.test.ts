/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'
import { archiveBuilder, archiveDirectory } from '../src/v2/contextArchive.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { assertNever } from '../src/v2/builderText.js'
import { contextUploadServer } from './contextUploadServer.js'

let root: string
beforeEach(async () => { root = await fs.mkdtemp(join(tmpdir(), 'builder-utf8-')) })
afterEach(async () => { await fs.rm(root, { recursive: true, force: true }) })

it.each(['addLocalFile', 'addLocalDir', 'pipInstallFromRequirements'] as const)('refuses lone surrogates in %s paths before accessing the filesystem', (method) => {
  // Given a path string with no UTF-8 representation.
  const source = join(root, 'bad\udcff')
  const builder = ImageBuilder.fromRegistry('alpine')
  // When declared, then the source has the shared typed UTF-8 refusal.
  const call = method === 'pipInstallFromRequirements' ? () => builder.pipInstallFromRequirements(source) : () => builder[method](source, '/out')
  expect(call).toThrow(expect.objectContaining({
    constructor: BadRequestError, message: `build context path is not valid UTF-8: ${JSON.stringify(join(root, 'bad\ufffd'))}`,
  }))
})

it('refuses a lone surrogate in a directory context path before uploading', async () => {
  // Given a context string with no UTF-8 representation.
  const source = join(root, 'bad\ud800')
  const api = await contextUploadServer()
  try {
    // When built, then UTF-8 validation wins over the missing-path error.
    await expect(api.images.buildFromContext(source)).rejects.toMatchObject({
      constructor: BadRequestError, message: `build context path is not valid UTF-8: ${JSON.stringify(join(root, 'bad\ufffd'))}`,
    })
  } finally { await api.close() }
})

// These filesystems reject raw non-UTF-8 names before the SDK can inspect them.
const posixTest = process.platform === 'win32' || process.platform === 'darwin' ? it.skip : it
posixTest.each(['file', 'dir'] as const)('refuses an invalid UTF-8 %s name even when ignored', async (kind) => {
  // Given an invalid filesystem byte sequence hidden by an ignore-all rule.
  await fs.writeFile(join(root, 'Dockerfile'), 'FROM alpine\n')
  await fs.writeFile(join(root, '.dockerignore'), '*\n')
  const source = Buffer.concat([Buffer.from(root + '/bad'), Buffer.from([255])])
  switch (kind) {
    case 'file': await fs.writeFile(source, 'private'); break
    case 'dir': await fs.mkdir(source); break
    default: assertNever(kind)
  }
  // When archived, then refusal precedes filtering and uses replacement decoding.
  await expect(archiveDirectory(root, 'Dockerfile')).rejects.toMatchObject({
    constructor: BadRequestError, message: `build context path is not valid UTF-8: ${JSON.stringify(join(root, 'bad\ufffd'))}`,
  })
})

posixTest('refuses an invalid UTF-8 name inside a builder directory source', async () => {
  // Given a checked directory containing a non-UTF-8 filename.
  const source = Buffer.concat([Buffer.from(root + '/bad'), Buffer.from([0xed, 0xa0, 0x80])])
  await fs.writeFile(source, 'private')
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(root, '/out')
  // When built, then the path is replacement-decoded rather than leaked as ENOENT.
  await expect(archiveBuilder(builder)).rejects.toMatchObject({
    constructor: BadRequestError, message: `build context path is not valid UTF-8: ${JSON.stringify(join(root, 'bad\ufffd\ufffd\ufffd'))}`,
  })
})

posixTest.each(['file', 'dir'] as const)('refuses a symlinked %s source resolving to an invalid UTF-8 name', async (kind) => {
  // Given a valid source spelling whose symlink resolves to invalid filesystem bytes.
  const target = Buffer.concat([Buffer.from(root + '/bad'), Buffer.from([255])])
  const source = join(root, 'alias')
  const base = ImageBuilder.fromRegistry('alpine')
  let builder: ImageBuilder
  switch (kind) {
    case 'file':
      await fs.writeFile(target, 'content')
      await fs.symlink(target, source)
      builder = base.addLocalFile(source, '/out')
      break
    case 'dir':
      await fs.mkdir(target)
      await fs.symlink(target, source)
      builder = base.addLocalDir(source, '/out')
      break
    default: assertNever(kind)
  }
  // When built, then the resolved path is checked without a replacement lookup.
  await expect(archiveBuilder(builder)).rejects.toMatchObject({
    constructor: BadRequestError, message: `build context path is not valid UTF-8: ${JSON.stringify(join(root, 'bad\ufffd'))}`,
  })
})

it('preserves a UTF-8 BOM at the start of a directory entry name', async () => {
  // Given a valid name whose first code point is a byte-order mark.
  await fs.writeFile(join(root, 'Dockerfile'), 'FROM alpine\n')
  await fs.writeFile(join(root, '\ufefffile'), 'content')
  // When archived, then UTF-8 decoding does not strip the actual filename prefix.
  const archive = await archiveDirectory(root, 'Dockerfile')
  try {
    const bytes = await fs.readFile(archive.path)
    expect(bytes.includes(Buffer.from(' path=\ufefffile\n'))).toBe(true)
  } finally { await archive.cleanup() }
})

it('refuses a symbolic link whose target is not UTF-8', async () => {
  // Given a link whose target bytes are not UTF-8 (Python reads them as raw
  // bytes, Node with replacement characters: both refuse instead).
  const context = join(root, 'badlink-ctx')
  await fs.mkdir(context)
  await fs.writeFile(join(context, 'Dockerfile'), 'FROM alpine\n')
  try {
    await fs.symlink(Buffer.from([0xff, 0xfe, 0x77]), Buffer.from(join(context, 'badlink')))
  } catch {
    return // The filesystem refuses non-UTF-8 link targets.
  }
  // When the directory is archived, then it is refused before any upload.
  await expect(archiveDirectory(context, 'Dockerfile')).rejects.toThrow(/build context path is not valid UTF-8/)
})

it('reports the first problem in walk order when a later name is not UTF-8', async () => {
  // Given an escaping link in a/ and an undecodable name in z/.
  const context = join(root, 'order')
  await fs.mkdir(join(context, 'a'), { recursive: true })
  await fs.mkdir(join(context, 'z'))
  await fs.writeFile(join(context, 'Dockerfile'), 'FROM alpine\n')
  await fs.symlink('../../x', join(context, 'a', 'link'))
  try {
    await fs.writeFile(Buffer.concat([Buffer.from(join(context, 'z') + '/'), Buffer.from([0x62, 0xff])]), 'x')
  } catch {
    return // The filesystem refuses non-UTF-8 names.
  }
  // When archived, then the link (reached first) is the refusal, as in Python.
  await expect(archiveDirectory(context, 'Dockerfile')).rejects.toThrow(`symbolic link leaves its local source: ${JSON.stringify(join(context, 'a', 'link'))}`)
})

it.each([
  ['addLocalFile', (builder: ImageBuilder, path: string) => (builder.addLocalFile as (...args: unknown[]) => ImageBuilder)(path, '/x', {})],
  ['addLocalDir', (builder: ImageBuilder, path: string) => (builder.addLocalDir as (...args: unknown[]) => ImageBuilder)(path, '/x', {})],
  ['pipInstallFromRequirements', (builder: ImageBuilder, path: string) => (builder.pipInstallFromRequirements as (...args: unknown[]) => ImageBuilder)(path, { indexUrl: 'https://private.example/simple' })],
] as const)('%s refuses an extra argument (0.x pip options) instead of dropping it', async (_name, call) => {
  // Given a source that exists.
  await fs.writeFile(join(root, 'req.txt'), 'numpy\n')
  // When called with a 0.x-style options argument, then it is refused, not ignored.
  expect(() => call.call(undefined, ImageBuilder.fromRegistry('alpine'), _name === 'addLocalDir' ? root : join(root, 'req.txt'))).toThrow(BadRequestError)
})
