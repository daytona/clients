/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { chmod, mkdir, mkdtemp, readFile, rm, rmdir, symlink, writeFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { promisify } from 'node:util'
import { ImageBuilder } from '../src/v2/Image.js'
import { archiveBuilder, dockerfileEscape } from '../src/v2/contextArchive.js'
import escapeCases from '../../sdk-python/tests/fixtures/dockerfile-escape.json'
import { localSourcesOf } from '../src/v2/ImageBuilder.js'
import { contextUploadRuntime } from '../src/v2/contextUpload.js'
import { BadRequestError, NotSupportedError } from '../src/v2/errors/DaytonaError.js'
import { parseTar } from './contextTar.js'
import { contextUploadServer } from './contextUploadServer.js'
import { contextBuilder, writeTree } from './imageBuilderContextFixture.js'
import type { ContextCase } from './imageBuilderContextFixture.js'

const fixture: { readonly cases: readonly ContextCase[]; readonly refused: readonly ContextCase[]; readonly buildRefused: readonly ContextCase[] } = JSON.parse(readFileSync(join(__dirname, '../../sdk-python/tests/fixtures/image-builder-context.json'), 'utf8'))
let root: string

beforeEach(async () => { root = await mkdtemp(join(tmpdir(), 'builder-fixture-')) })
afterEach(async () => { await rm(root, { recursive: true, force: true }) })

// A backslash is a filename character on POSIX, but a separator on Windows.
const portableCases = fixture.cases.filter((testCase) => process.platform !== 'win32' || !Object.keys(testCase.files).some((name) => name.includes('\\')))
it.each(portableCases)('archives the shared context fixture: $name', async (testCase) => {
  // Given the shared tree and an immutable builder recording its sources.
  await writeTree(root, testCase)
  const builder = contextBuilder(root, testCase)
  // When its Dockerfile and optional context are rendered.
  expect(builder.toDockerfile()).toBe(testCase.dockerfile)
  if (testCase.entries === null) {
    expect(localSourcesOf(builder)).toEqual([])
    const api = await contextUploadServer()
    try {
      await api.images.build(builder)
      expect(api.json('POST', '/images')).toEqual({ dockerfile: testCase.dockerfile })
      expect(api.requests('POST', '/build-contexts')).toEqual([])
    } finally { await api.close() }
    return
  }
  const archive = await archiveBuilder(builder)
  try {
    const bytes = await readFile(archive.path)
    const entries = parseTar(bytes)
    if (testCase.sha256 !== undefined) expect(createHash('sha256').update(bytes).digest('hex')).toBe(testCase.sha256)
    // Then entry ordering and contents are identical in both SDKs.
    expect(entries.map((entry) => entry.name)).toEqual(testCase.entries)
    expect(entries.find((entry) => entry.name === 'Dockerfile')?.contents.toString()).toBe(testCase.dockerfile)
    for (const [name, contents] of Object.entries(testCase.contents ?? {})) {
      expect(entries.find((entry) => entry.name === name)?.contents.toString()).toBe(contents)
    }
    expect(entries.every((entry) => entry.uid === 0 && entry.gid === 0 && entry.mtime === 0 && entry.uname === '' && entry.gname === '')).toBe(true)
  } finally {
    await archive.cleanup()
  }
})

it.each(fixture.refused)('refuses the shared context fixture at the builder call: $name', async (testCase) => {
  // Given an invalid local source or remote destination.
  await writeTree(root, testCase)
  // When the local step is added, then it fails before building.
  let failure: unknown
  try { contextBuilder(root, testCase) } catch (error) { failure = error }
  expect(failure).toBeInstanceOf(BadRequestError)
  expect(failure).toEqual(expect.objectContaining({ message: expect.stringContaining(testCase.error.replace('<root>', root)) }))
})

it.each(fixture.buildRefused)('refuses the shared context fixture when archiving: $name', async (testCase) => {
  // Given a checked directory whose contents include an unsafe link.
  await writeTree(root, testCase)
  const builder = contextBuilder(root, testCase)
  // When the context is read, then the link's absolute path is reported.
  const pending = archiveBuilder(builder)
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  await expect(pending).rejects.toMatchObject({ message: testCase.error.replace('<root>', root) })
})

it.each(['file', 'dir', 'requirements'] as const)('refuses %s local sources outside Node.js', (kind) => {
  // Given a runtime without Node's filesystem.
  const original = contextUploadRuntime.isNode
  contextUploadRuntime.isNode = () => false
  try {
    const builder = ImageBuilder.fromRegistry('alpine')
    const add = () => {
      switch (kind) {
        case 'file': return builder.addLocalFile('missing', '/x')
        case 'dir': return builder.addLocalDir('missing', '/x')
        case 'requirements': return builder.pipInstallFromRequirements('missing')
        default: throw new TypeError(`unknown local source: ${kind satisfies never}`)
      }
    }
    // When any local step is added, then it reports the runtime limitation.
    expect(add).toThrow(NotSupportedError)
  } finally {
    contextUploadRuntime.isNode = original
  }
})

it('keeps earlier builders unchanged when local steps are added', async () => {
  // Given two branches from one builder.
  await writeTree(root, { files: { 'a': 'A', 'b': 'B' } })
  const original = ImageBuilder.fromRegistry('alpine')
  const first = original.addLocalFile(join(root, 'a'), '/a')
  // When another local source and ordinary steps are appended.
  const second = first.runCommands('true').addLocalFile(join(root, 'b'), '/b')
  // Then each branch keeps its own positional context.
  expect(original.toRequestSource()).toEqual({ source: { type: 'registry', ref: 'alpine' } })
  expect(localSourcesOf(first)).toHaveLength(1)
  expect(localSourcesOf(second).map((source) => source.archivePath)).toEqual(['.daytona-context/1/a', '.daytona-context/2/b'])
  expect(first.toDockerfile()).toBe('FROM alpine\nCOPY [".daytona-context/1/a", "/a"]\n')
})

it('expands home paths without resolving a symlink at the call boundary', async () => {
  // Given a valid source accessible through a symlink beneath an expanded home path.
  await writeTree(root, { files: { 'real/a': 'A' } })
  await symlink('real', join(root, 'alias'))
  const path = join(root, 'alias/a')
  // When the builder records the path.
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(`~/${relative(homedir(), path)}`, '/x')
  // Then the absolute spelling is kept, rather than realpath.
  expect(localSourcesOf(builder)[0]?.localPath).toBe(path)
})

it('copies a symlinked source file as its content, not as a link', async () => {
  // Given a requirements file that is a symlink to a file beside it.
  await writeTree(root, { files: { 'real-requirements.txt': 'numpy\n' } })
  await symlink('real-requirements.txt', join(root, 'requirements.txt'))
  const builder = ImageBuilder.fromRegistry('alpine').pipInstallFromRequirements(join(root, 'requirements.txt'))
  // When the archive is generated.
  const archive = await archiveBuilder(builder)
  try {
    const entry = parseTar(await readFile(archive.path)).find((item) => item.name === '.daytona-context/1/requirements.txt')
    // Then the archive holds the file's bytes: a link would dangle under .daytona-context/1/.
    expect(entry?.contents.toString()).toBe('numpy\n')
  } finally {
    await archive.cleanup()
  }
})

it('reads local file bytes at build time and preserves executable permissions', async () => {
  // Given a source checked before its final contents are written.
  const path = join(root, 'script')
  await writeFile(path, 'old')
  await chmod(path, 0o751)
  const builder = ImageBuilder.fromRegistry('alpine').addLocalFile(path, '/script')
  await writeFile(path, 'new')
  // When the archive is generated.
  const archive = await archiveBuilder(builder)
  try {
    const entry = parseTar(await readFile(archive.path)).find((item) => item.name === '.daytona-context/1/script')
    // Then the current bytes and permission bits are kept.
    expect(entry?.contents.toString()).toBe('new')
    expect(entry?.mode).toBe(0o751)
  } finally {
    await archive.cleanup()
  }
})

it('uses PAX paths for long UTF-8 names without changing logical archive order', async () => {
  // Given a name over ustar's 100-byte name field.
  const name = 'é'.repeat(70)
  await writeTree(root, { files: { [`d/${name}`]: 'long' } })
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When it is archived.
  const archive = await archiveBuilder(builder)
  try {
    const entries = parseTar(await readFile(archive.path))
    // Then a tar reader sees the complete UTF-8 name in sorted order.
    expect(entries.map((entry) => entry.name)).toEqual(['.daytona-context/', '.daytona-context/1/', `.daytona-context/1/${name}`, 'Dockerfile'])
    expect(entries[2]?.contents.toString()).toBe('long')
  } finally {
    await archive.cleanup()
  }
})

const posixTest = process.platform === 'win32' ? it.skip : it
posixTest('refuses a FIFO without opening it when a directory is archived', async () => {
  // Given a named pipe, whose contents cannot be copied as a regular file.
  await writeTree(root, { files: { 'd/': '' } })
  const pipe = join(root, 'd/pipe')
  await promisify(execFile)('mkfifo', [pipe])
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When the context is archived, then the unsupported path is reported.
  const pending = archiveBuilder(builder)
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  await expect(pending).rejects.toThrow(pipe)
})

it('archives the deepest file when the tree is 1200 directories deep', async () => {
  // Given a real tree deeper than a synchronous call stack, below PATH_MAX.
  const directories = [join(root, 'source')]
  await mkdir(directories[0])
  for (let i = 0; i < 1200; i++) {
    const child = join(directories[directories.length - 1], 'd')
    await mkdir(child)
    directories.push(child)
  }
  const leaf = join(directories[directories.length - 1], 'f')
  await writeFile(leaf, 'deep')
  try {
    // When the builder archives the tree.
    const archive = await archiveBuilder(ImageBuilder.fromRegistry('alpine').addLocalDir(directories[0], '/app'))
    try {
      // Then traversal and PAX serialization retain the deepest file.
      const name = '.daytona-context/1/' + 'd/'.repeat(1200) + 'f'
      expect(parseTar(await readFile(archive.path)).find((entry) => entry.name === name)?.contents.toString()).toBe('deep')
    } finally { await archive.cleanup() }
  } finally {
    await rm(leaf)
    for (const directory of directories.reverse()) await rmdir(directory)
  }
}, 15000)

it('sorts complete archive names by UTF-8 bytes rather than walk order or UTF-16', async () => {
  // Given names whose directory order and UTF-16 order differ from archive order.
  await writeTree(root, { files: { 'd/a/entry': 'nested', 'd/a!': 'punctuation', 'd/😀': 'astral', 'd/\ue000': 'private' } })
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When the context is archived.
  const archive = await archiveBuilder(builder)
  try {
    // Then full names, including directory slashes, decide the ordering.
    expect(parseTar(await readFile(archive.path)).map((entry) => entry.name)).toEqual([
      '.daytona-context/', '.daytona-context/1/', '.daytona-context/1/a!', '.daytona-context/1/a/',
      '.daytona-context/1/a/entry', '.daytona-context/1/\ue000', '.daytona-context/1/😀', 'Dockerfile',
    ])
  } finally { await archive.cleanup() }
})

it('archives a parent-relative symlink when its target stays inside the source', async () => {
  // Given a link containing .. that stays within the added directory.
  await writeTree(root, { files: { 'd/target': 'T' }, symlinks: { 'd/sub/link': '../target' } })
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When the context is archived.
  const archive = await archiveBuilder(builder)
  try {
    const link = parseTar(await readFile(archive.path)).find((entry) => entry.name.endsWith('/link'))
    // Then the original link is kept, not dereferenced or over-rejected.
    expect(link).toMatchObject({ type: '2', link: '../target' })
  } finally { await archive.cleanup() }
})

it('uses a PAX path even when a long ASCII name also fits the ustar prefix fields', async () => {
  // Given an ASCII path over 100 bytes whose components fit ustar's split fields.
  const name = `${'a'.repeat(60)}/${'b'.repeat(60)}`
  await writeTree(root, { files: { [`d/${name}`]: 'long ASCII path' } })
  const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(root, 'd'), '/x')
  // When the context is archived.
  const archive = await archiveBuilder(builder)
  try {
    const tar = await readFile(archive.path)
    // Then the long path is explicitly represented by a PAX path record.
    expect(tar.includes(Buffer.from(` path=.daytona-context/1/${name}\n`))).toBe(true)
    expect(parseTar(tar).find((entry) => entry.name.endsWith(name))?.contents.toString()).toBe('long ASCII path')
  } finally { await archive.cleanup() }
})

it.each(escapeCases.cases.map((testCase) => [JSON.stringify(testCase.dockerfile), testCase] as const))('reads the escape directive as BuildKit does: %s', (_name, testCase) => {
  // The expectations come from BuildKit's own parser (shared with Python).
  expect(dockerfileEscape(testCase.dockerfile)).toBe(testCase.escape)
})
