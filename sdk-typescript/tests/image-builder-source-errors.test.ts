/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs'
import promises from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'
import { archiveBuilder, archiveDirectory } from '../src/v2/contextArchive.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'

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

const permissionTest = process.geteuid?.() === 0 ? it.skip : it
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
