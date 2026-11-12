/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile, chmod, readdir } from 'node:fs/promises'
import os, { tmpdir } from 'node:os'
import { join } from 'node:path'
import { archiveDirectory } from '../src/v2/contextArchive.js'
import { ImageBuilder } from '../src/v2/Image.js'
import { localSourcesOf } from '../src/v2/ImageBuilder.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { writeTree } from './imageBuilderContextFixture.js'
import { parseTar } from './contextTar.js'
import { contextUploadServer } from './contextUploadServer.js'

interface DirectoryCase {
  readonly name: string
  readonly files: Readonly<Record<string, string>>
  readonly symlinks?: Readonly<Record<string, string>>
  readonly fifos?: readonly string[]
  readonly ignoreLineLength?: number
  readonly contextPath?: string
  readonly dockerfile?: string
  readonly entries?: readonly string[]
  readonly error: string
  readonly sha256: string
}

const fixtures = join(__dirname, '../../sdk-python/tests/fixtures')
const directories: { readonly directoryCases: readonly DirectoryCase[]; readonly directoryRefused: readonly DirectoryCase[] } = JSON.parse(readFileSync(join(fixtures, 'image-builder-directories.json'), 'utf8'))
const archives: { readonly cases: readonly DirectoryCase[] } = JSON.parse(readFileSync(join(fixtures, 'image-builder-archives.json'), 'utf8'))
let root: string
beforeEach(async () => { root = await mkdtemp(join(tmpdir(), 'builder-directory-')) })
afterEach(async () => { jest.restoreAllMocks(); await rm(root, { recursive: true, force: true }) })

it.each(directories.directoryCases)('archives directory inputs when $name', async (testCase) => {
  // Given the shared directory tree, including a symlinked context root.
  await writeTree(root, testCase)
  // When archived using the selected Dockerfile.
  const archive = await archiveDirectory(join(root, testCase.contextPath ?? '.'), testCase.dockerfile ?? 'Dockerfile')
  try {
    // Then no entries are lost or incorrectly included.
    expect(parseTar(await readFile(archive.path)).map((entry) => entry.name)).toEqual(testCase.entries)
  } finally { await archive.cleanup() }
})

it.each(directories.directoryRefused.filter((testCase) => process.platform !== 'win32' || !testCase.fifos?.length))('refuses directory inputs when $name', async (testCase) => {
  // Given invalid inputs shared with the Python suite.
  await writeTree(root, testCase)
  // When archived, then failure is local, typed and has the shared message.
  const pending = archiveDirectory(join(root, testCase.contextPath ?? '.'), testCase.dockerfile ?? 'Dockerfile')
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  await expect(pending).rejects.toMatchObject({ message: testCase.error.replace('<root>', root) })
})

it.each(archives.cases)('matches the cross-SDK archive digest when $name', async (testCase) => {
  // Given a tree with explicit modes, independent of umask.
  await writeTree(root, testCase)
  const setModes = async (directory: string): Promise<void> => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) { await chmod(path, 0o755); await setModes(path) }
      else if (entry.isFile()) await chmod(path, 0o644)
    }
  }
  await setModes(root)
  // When CPython-compatible PAX_FORMAT bytes are produced.
  const archive = await archiveDirectory(root, 'Dockerfile')
  try {
    // Then the full bytes, including PAX headers and record padding, match.
    const digest = createHash('sha256').update(await readFile(archive.path)).digest('hex')
    expect(digest).toBe(testCase.sha256)
  } finally { await archive.cleanup() }
})

it('replaces invalid UTF-8 when reading ignore files', async () => {
  // Given malformed UTF-8 beside a valid exclusion.
  await writeTree(root, { files: { Dockerfile: 'FROM alpine\n', drop: 'excluded' } })
  await writeFile(join(root, '.dockerignore'), Buffer.from([255, 10, ...Buffer.from('drop\n')]))
  // When archived, then the invalid byte does not prevent filtering.
  const archive = await archiveDirectory(root, 'Dockerfile')
  try {
    expect(parseTar(await readFile(archive.path)).map((entry) => entry.name)).toEqual(['.dockerignore', 'Dockerfile'])
  } finally { await archive.cleanup() }
})

describe.each([false, true])('directory context: %s', (directory) => {
  it.each([false, true])('uses the home rule when named-user spelling is %s', async (namedUser) => {
    // Given a home-relative path or a literal ~root name in the working directory.
    const previousCwd = process.cwd()
    // Jest's sandboxed process.env is not Node's native environment, so
    // replace only the home lookup rather than pretending to change HOME.
    jest.spyOn(os, 'homedir').mockReturnValue(root)
    process.chdir(root)
    const contextPath = namedUser ? '~root/context' : '~/context'
    const relative = namedUser ? '~root/context' : 'context'
    const api = await contextUploadServer()
    try {
      await writeTree(root, { files: directory ? { [`${relative}/Dockerfile`]: 'FROM alpine\n' } : { [relative]: 'opaque tar content' } })
      // When the real build entry point detects a directory or opens a tar path.
      await api.images.buildFromContext(contextPath)
      // Then the intended local bytes were uploaded in either branch.
      const puts = api.uploads()
      const bytes = Buffer.concat(puts.map((request) => request.body))
      if (directory) expect(parseTar(bytes).map((entry) => entry.name)).toEqual(['Dockerfile'])
      else expect(bytes.toString()).toBe('opaque tar content')
    } finally {
      await api.close()
      process.chdir(previousCwd)
    }
  })
})

it('keeps a named-user prefix literal when a builder records a source', async () => {
  // Given a literal ~root directory in cwd, distinct from root's actual home.
  const previousCwd = process.cwd()
  await writeTree(root, { files: { '~root/input': 'literal' } })
  process.chdir(root)
  try {
    // When recorded, then named-user lookup is not performed.
    const builder = ImageBuilder.fromRegistry('alpine').addLocalFile('~root/input', '/input')
    expect(localSourcesOf(builder)[0]?.localPath).toBe(join(root, '~root/input'))
  } finally { process.chdir(previousCwd) }
})

it('expands bare ~ when building a context directory', async () => {
  // Given a home containing a Dockerfile.
  jest.spyOn(os, 'homedir').mockReturnValue(root)
  await writeTree(root, { files: { Dockerfile: 'FROM alpine\n' } })
  const api = await contextUploadServer()
  try {
    // When submitted as bare ~, then the home directory was archived.
    await api.images.buildFromContext('~')
    expect(parseTar(Buffer.concat(api.uploads().map((request) => request.body))).map((entry) => entry.name)).toEqual(['Dockerfile'])
  } finally {
    await api.close()
  }
})
