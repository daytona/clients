/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'
import { archiveBuilder } from '../src/v2/contextArchive.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { parseTar } from './contextTar.js'
import { writeTree } from './imageBuilderContextFixture.js'

let root: string
beforeEach(async () => { root = await fs.mkdtemp(join(tmpdir(), 'builder-source-safety-')) })
afterEach(async () => { await fs.rm(root, { recursive: true, force: true }) })

it.each(['file', 'dir'] as const)('refuses a declared %s whose type changes before building', async (kind) => {
  // Given a valid source recorded before it changes type.
  const source = join(root, 'source')
  const base = ImageBuilder.fromRegistry('alpine')
  let builder: ImageBuilder
  switch (kind) {
    case 'file':
      await fs.writeFile(source, 'original')
      builder = base.addLocalFile(source, '/x')
      await fs.unlink(source)
      await fs.mkdir(source)
      break
    case 'dir':
      await fs.mkdir(source)
      builder = base.addLocalDir(source, '/x')
      await fs.rmdir(source)
      await fs.writeFile(source, 'replacement')
      break
    default: throw new TypeError(`unknown source kind ${kind satisfies never}`)
  }
  // When built, then a different kind is refused rather than silently copied.
  await expect(archiveBuilder(builder)).rejects.toMatchObject({
    constructor: BadRequestError,
    message: `local source is not a ${kind === 'file' ? 'regular file' : 'directory'}: ${JSON.stringify(source)}`,
  })
})

it('reports a looping source symlink as a missing local path', async () => {
  // Given a source name that resolves only to itself.
  const source = join(root, 'loop')
  await fs.symlink('loop', source)
  // When declared, then it uses the missing-path SDK error, not raw ELOOP.
  expect(() => ImageBuilder.fromRegistry('alpine').addLocalFile(source, '/x')).toThrow(expect.objectContaining({
    constructor: BadRequestError, message: `${source} does not exist`,
  }))
})

const posixTest = process.platform === 'win32' ? it.skip : it
posixTest('preserves a POSIX relative symlink target starting with a backslash', async () => {
  // Given a backslash that is an ordinary filename character on POSIX.
  await writeTree(root, { files: { 'd/\\foo': 'inside' }, symlinks: { 'd/link': '\\foo' } })
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When archived.
  const archive = await archiveBuilder(builder)
  try {
    // Then its relative link is retained without Windows path interpretation.
    expect(parseTar(await fs.readFile(archive.path)).find((entry) => entry.name.endsWith('/link'))).toMatchObject({ type: '2', link: '\\foo' })
  } finally { await archive.cleanup() }
})

it.each(['a$HOME.txt', 'cfg[prod].txt', 'close].txt', 'star*.txt', 'what?.txt', 'tab\t.txt', 'delete\x7f.txt', 'next\u0085line.txt', "it's.txt", 'a"b.txt'])('stages %s at a fixed COPY source while preserving its destination basename', async (name) => {
  // Given a filename Docker would otherwise interpret as COPY syntax.
  const source = join(root, name)
  await fs.writeFile(source, 'literal content')
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(source, '/out/')
  // When rendered and archived.
  const archive = await archiveBuilder(builder)
  try {
    // Then COPY reads a safe source, and the destination keeps the file's name:
    // BuildKit substitutes `$` in destinations, so it is escaped (globs are not
    // expanded there).
    const copy = builder.toDockerfile().split('\n')[1].slice('COPY '.length)
    expect(JSON.parse(copy)).toEqual(['.daytona-context/1/file', `/out/${name.replace(/[\\$'"]/g, (char) => `\\${char}`)}`])
    expect(parseTar(await fs.readFile(archive.path)).find((entry) => entry.name === '.daytona-context/1/file')?.contents.toString()).toBe('literal content')
  } finally { await archive.cleanup() }
})

it.each(['config.json', 'spaced name.txt', 'café.txt', '😀.txt'])('preserves safe basename %s when copying into an existing directory', async (name) => {
  // Given a file whose basename Docker can use literally as a COPY source.
  const source = join(root, name)
  await fs.writeFile(source, 'literal content')
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(source, '/existing')
  // When rendered and archived.
  const archive = await archiveBuilder(builder)
  try {
    // Then BuildKit can retain the original basename for an existing directory.
    expect(builder.toDockerfile()).toContain(`COPY [${JSON.stringify(`.daytona-context/1/${name}`)}, "/existing"]`)
    expect(parseTar(await fs.readFile(archive.path)).find((entry) => entry.name === `.daytona-context/1/${name}`)?.contents.toString()).toBe('literal content')
  } finally { await archive.cleanup() }
})

posixTest('stages a backslash basename at a fixed source while escaping its destination', async () => {
  // Given a POSIX filename containing the Dockerfile escape character.
  const source = join(root, 'back\\slash.txt')
  await fs.writeFile(source, 'literal content')
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(source, '/out/')
  // When rendered, then source expansion is avoided and the destination is literal.
  expect(JSON.parse(builder.toDockerfile().split('\n')[1].slice('COPY '.length))).toEqual([
    '.daytona-context/1/file', '/out/back\\\\slash.txt',
  ])
})
