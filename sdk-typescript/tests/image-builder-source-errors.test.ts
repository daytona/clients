/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs'
import promises from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'
import { ImagesService } from '../src/v2/Images.js'
import { INTERNAL } from '../src/v2/internal.js'
import { archiveBuilder, archiveDirectory } from '../src/v2/contextArchive.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { parseTar } from './contextTar.js'

const methods = ['addLocalFile', 'addLocalDir', 'pipInstallFromRequirements'] as const
let root: string
beforeEach(async () => { root = await promises.mkdtemp(join(tmpdir(), 'builder-source-errors-')) })
afterEach(async () => {
  jest.restoreAllMocks()
  await promises.rm(root, { recursive: true, force: true })
})

it.each(methods)('refuses a NUL local path when declaring %s', (method) => {
  // Given a local source spelling the filesystem cannot accept.
  const builder = ImageBuilder.fromRegistry('alpine')
  const call = () => method === 'pipInstallFromRequirements' ? builder[method]('a\0b') : builder[method]('a\0b', '/out')
  // When declared, then the SDK rejects it without exposing the NUL in its message.
  expect(call).toThrow(expect.objectContaining({
    constructor: BadRequestError, message: 'ImageBuilder localPath must not contain a NUL character',
  }))
})

describe.each(methods)('%s declaration', (method) => {
  it.each(['EACCES', 'EPERM', 'EIO'])('classifies a stat failure when the OS reports %s', (code) => {
    // Given an OS stat failure, independent of the process's privileges.
    const source = join(root, 'source"é')
    const failure = Object.assign(new Error('test stat failure'), { code })
    jest.spyOn(fs, 'statSync').mockImplementation(() => { throw failure })
    const builder = ImageBuilder.fromRegistry('alpine')
    const call = () => method === 'pipInstallFromRequirements' ? builder[method](source) : builder[method](source, '/out')
    // When declared, then permissions are typed and unrelated I/O still propagates.
    expect(call).toThrow(code === 'EIO' ? failure : expect.objectContaining({
      constructor: BadRequestError, message: `build context file is not readable: ${JSON.stringify(source)}`,
    }))
  })
})

describe.each(methods)('%s archive', (method) => {
  it('reports a missing source when replaced by a self-referential symlink', async () => {
    // Given a valid declaration whose source becomes a symlink loop.
    const source = join(root, 'source"é')
    const base = ImageBuilder.fromRegistry('alpine')
    let builder: ImageBuilder
    switch (method) {
      case 'addLocalDir':
        await promises.mkdir(source)
        builder = base[method](source, '/out')
        await promises.rmdir(source)
        break
      case 'addLocalFile':
      case 'pipInstallFromRequirements':
        await promises.writeFile(source, 'content')
        builder = method === 'addLocalFile' ? base[method](source, '/out') : base[method](source)
        await promises.unlink(source)
        break
      default: throw new TypeError(`unknown local source ${method satisfies never}`)
    }
    await promises.symlink('source"é', source)
    // When archived, then ELOOP is an SDK bad request, not a raw filesystem error.
    await expect(archiveBuilder(builder)).rejects.toMatchObject({
      constructor: BadRequestError, message: `local source does not exist: ${JSON.stringify(source)}`,
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
  })

  it.each([false, true])('reports a vanished local source when parent replacement is %s', async (replaceParent) => {
    // Given a declared source removed before the archive is prepared.
    const parent = join(root, 'parent')
    await promises.mkdir(parent)
    const source = join(parent, 'source"é')
    const base = ImageBuilder.fromRegistry('alpine')
    let builder: ImageBuilder
    switch (method) {
      case 'addLocalDir':
        await promises.mkdir(source)
        builder = base[method](source, '/out')
        await promises.rmdir(source)
        break
      case 'addLocalFile':
      case 'pipInstallFromRequirements':
        await promises.writeFile(source, 'content')
        builder = method === 'addLocalFile' ? base[method](source, '/out') : base[method](source)
        await promises.unlink(source)
        break
      default: throw new TypeError(`unknown local source ${method satisfies never}`)
    }
    if (replaceParent) {
      await promises.rmdir(parent)
      await promises.writeFile(parent, 'replacement')
    }
    // When archived, then the original declared path is named in a typed error.
    await expect(archiveBuilder(builder)).rejects.toMatchObject({
      constructor: BadRequestError, message: `local source does not exist: ${JSON.stringify(source)}`,
    })
  })
})

const permissionTest = process.platform === 'win32' || process.geteuid?.() === 0 ? it.skip : it
permissionTest.each(['', 'data\n', 'data\n!**/keep\n', 'data\n!data/keep\n'])('handles an unreadable directory when ignore is %j', async (ignore) => {
  // Given a kept or excluded mode-000 directory, including negations that attempt descent.
  const data = join(root, 'data"é')
  await promises.mkdir(data)
  await promises.writeFile(join(data, 'input'), 'private')
  await promises.writeFile(join(root, 'Dockerfile'), 'FROM alpine\n')
  await promises.writeFile(join(root, '.dockerignore'), ignore.replaceAll('data', 'data"é'))
  await promises.chmod(data, 0)
  try {
    // When archived, then only kept unreadable directories fail with the shared typed error.
    const pending = archiveDirectory(root, 'Dockerfile')
    if (ignore === '') await expect(pending).rejects.toMatchObject({
      constructor: BadRequestError, message: `build context directory is not readable: ${JSON.stringify(data)}`,
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
    else await (await pending).cleanup()
  } finally { await promises.chmod(data, 0o755) }
})

const posixNonRoot = process.platform !== 'win32' && process.getuid?.() !== 0 ? it : it.skip
posixNonRoot('refuses an unreadable context root or ignore file with a typed error', async () => {
  // Given a context root that cannot be listed, and one whose .dockerignore cannot be read.
  const fs = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { tmpdir } = await import('node:os')
  const base = await fs.mkdtemp(join(tmpdir(), 'unreadable-'))
  const locked = join(base, 'locked')
  const ignored = join(base, 'ignored')
  await fs.mkdir(locked)
  await fs.writeFile(join(locked, 'Dockerfile'), 'FROM alpine\n')
  await fs.mkdir(ignored)
  await fs.writeFile(join(ignored, 'Dockerfile'), 'FROM alpine\n')
  await fs.writeFile(join(ignored, '.dockerignore'), 'x\n')
  await fs.chmod(locked, 0)
  await fs.chmod(join(ignored, '.dockerignore'), 0)
  try {
    // When archived, then both are BadRequestErrors naming the path.
    const { archiveDirectory } = await import('../src/v2/contextArchive.js')
    const { BadRequestError } = await import('../src/v2/errors/DaytonaError.js')
    const typed = { constructor: BadRequestError, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' }
    await expect(archiveDirectory(locked, 'Dockerfile')).rejects.toMatchObject({ ...typed, message: expect.stringContaining('build context directory is not readable') })
    await expect(archiveDirectory(ignored, 'Dockerfile')).rejects.toMatchObject({ ...typed, message: expect.stringContaining('build context file is not readable') })
  } finally {
    await fs.chmod(locked, 0o755)
    await fs.chmod(join(ignored, '.dockerignore'), 0o644)
    await fs.rm(base, { recursive: true, force: true })
  }
})

posixNonRoot('treats an unreachable tar path as a tar path, not a directory', async () => {
  // Given a tar path under a directory the user cannot search, and a link loop.
  const fs = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { tmpdir } = await import('node:os')
  const base = await fs.mkdtemp(join(tmpdir(), 'unreachable-'))
  await fs.mkdir(join(base, 'noexec'))
  await fs.writeFile(join(base, 'noexec', 'c.tar'), 'x')
  await fs.chmod(join(base, 'noexec'), 0o600)
  await fs.symlink(join(base, 'loop2'), join(base, 'loop1'))
  await fs.symlink(join(base, 'loop1'), join(base, 'loop2'))
  try {
    // When probed, then none of them raises: the tar upload owns the typed error.
    const { isContextDirectory } = await import('../src/v2/contextArchive.js')
    for (const path of [join(base, 'noexec', 'c.tar'), join(base, 'loop1'), join(base, 'x'.repeat(300))]) {
      await expect(isContextDirectory(path)).resolves.toBe(false)
    }
  } finally {
    await fs.chmod(join(base, 'noexec'), 0o755)
    await fs.rm(base, { recursive: true, force: true })
  }
})

it('refuses a blank context path and an ImageBuilder passed as the context', async () => {
  // A blank path is a missing tar file (never the working directory), and a
  // builder is built with images.build(): both are refused before any request.
  const axios = (await import('axios')).default
  const requests: string[] = []
  const http = axios.create({ adapter: async (config) => { requests.push(String(config.url)); throw new Error('no request expected') } })
  const images = new ImagesService(INTERNAL, http)
  await expect(images.buildFromContext('')).rejects.toBeInstanceOf(BadRequestError)
  await expect(images.buildFromContext(ImageBuilder.fromRegistry('alpine') as never)).rejects.toBeInstanceOf(BadRequestError)
  expect(requests).toEqual([])
})

it('explains a builder passed to buildFromContext before checking dockerfile (as Python)', async () => {
  const axios = (await import('axios')).default
  const requests: string[] = []
  const http = axios.create({ adapter: async (config) => { requests.push(String(config.url)); throw new Error('no request expected') } })
  const images = new ImagesService(INTERNAL, http)
  // SAFETY: plain JavaScript can pass a builder; the type refuses it.
  await expect(images.buildFromContext(ImageBuilder.fromRegistry('alpine') as never, { dockerfile: '' })).rejects.toMatchObject({
    message: 'buildFromContext() takes the bytes, Blob or path of a build context; build an ImageBuilder with images.build()',
    statusCode: 400,
    code: 'BAD_REQUEST',
    source: 'SDK',
  })
  expect(requests).toEqual([])
})

for (const kind of ['file', 'dir'] as const) {
  posixNonRoot(`refuses a ${kind} source whose parent became unsearchable as not readable`, async () => {
    // Given a source added, then its parent made unsearchable before the build.
    const base = await promises.mkdtemp(join(tmpdir(), 'unsearchable-'))
    const parent = join(base, 'p')
    try {
      await promises.mkdir(join(parent, 'd'), { recursive: true })
      await promises.writeFile(join(parent, 'f.txt'), 'f')
      const builder = kind === 'file'
        ? ImageBuilder.fromRegistry('alpine').addLocalFile(join(parent, 'f.txt'), '/f.txt')
        : ImageBuilder.fromRegistry('alpine').addLocalDir(join(parent, 'd'), '/d')
      await promises.chmod(parent, 0o600)
      // When archived, then it is the typed error addLocal* raises for it.
      await expect(archiveBuilder(builder)).rejects.toMatchObject({ message: expect.stringMatching(/^build context file is not readable: /), code: 'BAD_REQUEST' })
    } finally {
      await promises.chmod(parent, 0o755).catch(() => undefined)
      await promises.rm(base, { recursive: true, force: true })
    }
  })
}

posixNonRoot('refuses a source redirected by a symlink after it was added', async () => {
  // Given a directory added to a builder, then replaced by a link elsewhere.
  const fs = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { tmpdir } = await import('node:os')
  const base = await fs.mkdtemp(join(tmpdir(), 'redirected-'))
  try {
    await fs.mkdir(join(base, 'secret'))
    await fs.writeFile(join(base, 'secret', 'key'), 'k')
    await fs.mkdir(join(base, 'project'))
    const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(base, 'project'), '/app')
    await fs.rmdir(join(base, 'project'))
    await fs.symlink(join(base, 'secret'), join(base, 'project'))
    // When archived, then the redirected path is refused, not followed.
    await expect(archiveBuilder(builder)).rejects.toThrow(/local source changed since it was added/)
  } finally { await fs.rm(base, { recursive: true, force: true }) }
})

posixNonRoot('archives sources under an unchanged symlinked ancestor', async () => {
  // Given sources added through a directory link that stays as it was (a
  // symlinked home or workspace).
  const fs = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { tmpdir } = await import('node:os')
  const base = await fs.mkdtemp(join(tmpdir(), 'linked-'))
  try {
    await fs.mkdir(join(base, 'real', 'sub'), { recursive: true })
    await fs.writeFile(join(base, 'real', 'sub', 'f.txt'), 'f')
    await fs.symlink(join(base, 'real'), join(base, 'link'))
    const builder = ImageBuilder.fromRegistry('alpine')
      .addLocalFile(join(base, 'link', 'sub', 'f.txt'), '/f.txt')
      .addLocalDir(join(base, 'link', 'sub'), '/sub')
    // When archived, then both sources are copied.
    const archive = await archiveBuilder(builder)
    try {
      const names = parseTar(await fs.readFile(archive.path)).map((entry) => entry.name)
      expect(names).toEqual(expect.arrayContaining(['.daytona-context/1/f.txt', '.daytona-context/2/f.txt']))
    } finally { await archive.cleanup() }
  } finally { await fs.rm(base, { recursive: true, force: true }) }
})

posixNonRoot('refuses a source whose parent was swapped for a symlink after it was added', async () => {
  // Given proj/src added to a builder, then proj replaced by a link to another
  // tree that also has a src.
  const fs = await import('node:fs/promises')
  const { join } = await import('node:path')
  const { tmpdir } = await import('node:os')
  const base = await fs.mkdtemp(join(tmpdir(), 'swapped-'))
  try {
    await fs.mkdir(join(base, 'other', 'src'), { recursive: true })
    await fs.writeFile(join(base, 'other', 'src', 'secret.txt'), 's')
    await fs.mkdir(join(base, 'proj', 'src'), { recursive: true })
    const builder = ImageBuilder.fromRegistry('alpine').addLocalDir(join(base, 'proj', 'src'), '/app')
    await fs.rename(join(base, 'proj'), join(base, 'proj.old'))
    await fs.symlink(join(base, 'other'), join(base, 'proj'))
    // When archived, then the redirected source is refused, not followed.
    await expect(archiveBuilder(builder)).rejects.toThrow(/local source changed since it was added/)
  } finally { await fs.rm(base, { recursive: true, force: true }) }
})
