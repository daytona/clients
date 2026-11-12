/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { contextEntries } from '../src/v2/contextEntries.js'
import { writeTar } from '../src/v2/contextTar.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { writeTree } from './imageBuilderContextFixture.js'
import { parseTar } from './contextTar.js'

let root: string
beforeEach(async () => { root = await fs.mkdtemp(join(tmpdir(), 'builder-swap-')) })
afterEach(async () => { jest.restoreAllMocks(); await fs.rm(root, { recursive: true, force: true }) })

it.each(['file', 'parent', 'replacement'] as const)('refuses a %s swap after the archive walk', async (kind) => {
  // Given equal-sized inside/outside files already recorded by the walk.
  await writeTree(root, { files: { 'source/sub/f': 'inside', 'outside/f': 'secret' } })
  const victim = join(root, 'source/sub/f')
  const entries = await contextEntries(join(root, 'source'))
  switch (kind) {
    case 'file': await fs.unlink(victim); await fs.symlink(join(root, 'outside/f'), victim); break
    case 'parent': await fs.rename(join(root, 'source/sub'), join(root, 'old')); await fs.symlink(join(root, 'outside'), join(root, 'source/sub')); break
    case 'replacement': await fs.rename(victim, join(root, 'old')); await fs.copyFile(join(root, 'outside/f'), victim); break
    default: throw new TypeError(`unknown swap ${kind satisfies never}`)
  }
  const output = await fs.open(join(root, 'context.tar'), 'wx')
  try {
    // When opened for archiving, then replacement content is never accepted.
    await expect(writeTar(output, entries)).rejects.toEqual(expect.objectContaining({
      constructor: BadRequestError,
      message: `build context changed: ${JSON.stringify(victim)}`,
    }))
  } finally { await output.close() }
})

it.each(['ELOOP', 'ENOENT', 'ENXIO', 'ENOTDIR', 'EACCES', 'EPERM'])('maps %s opening an archive entry to BadRequestError', async (code) => {
  // Given a walked file and a specific OS open failure at the I/O boundary.
  await writeTree(root, { files: { 'source/f': 'inside' } })
  const entries = await contextEntries(join(root, 'source'))
  const output = await fs.open(join(root, 'context.tar'), 'wx')
  jest.spyOn(fs, 'open').mockRejectedValueOnce(Object.assign(new Error('open failed'), { code }))
  try {
    // When writing the file, then expected race errors use the SDK hierarchy.
    await expect(writeTar(output, entries)).rejects.toEqual(expect.objectContaining({
      constructor: BadRequestError,
      message: `${code === 'EACCES' || code === 'EPERM' ? 'build context file is not readable' : 'build context changed'}: ${JSON.stringify(join(root, 'source/f'))}`,
    }))
  } finally { await output.close() }
})

it('propagates unrelated archive entry open failures unchanged', async () => {
  // Given a walked file and an unrelated I/O failure.
  await writeTree(root, { files: { 'source/f': 'inside' } })
  const entries = await contextEntries(join(root, 'source'))
  const output = await fs.open(join(root, 'context.tar'), 'wx')
  const failure = Object.assign(new Error('device failed'), { code: 'EIO' })
  jest.spyOn(fs, 'open').mockRejectedValueOnce(failure)
  try {
    // When the archive opens it, then the original failure is preserved.
    await expect(writeTar(output, entries)).rejects.toBe(failure)
  } finally { await output.close() }
})

it('copies the opened size when a file grows after walking', async () => {
  // Given an entry whose inode is unchanged but whose size has grown.
  await writeTree(root, { files: { 'source/f': 'inside' } })
  const entries = await contextEntries(join(root, 'source'))
  await fs.appendFile(join(root, 'source/f'), ' appended')
  const filename = join(root, 'context.tar')
  const output = await fs.open(filename, 'wx')
  // When the archive is written.
  try { await writeTar(output, entries) } finally { await output.close() }
  // Then the header and copied content describe the size observed at open.
  expect(parseTar(await fs.readFile(filename)).find((entry) => entry.name === 'f')?.contents.toString()).toBe('inside appended')
})

it.each(['append', 'truncate'] as const)('handles an in-place %s mid-read by the opened size', async (change) => {
  // Given a multi-chunk file changed after the first read.
  const victim = join(root, 'source/f')
  const content = 'x'.repeat(64 * 1024 + 6)
  await writeTree(root, { files: { 'source/f': content } })
  const entries = await contextEntries(join(root, 'source'))
  const filename = join(root, 'context.tar')
  const output = await fs.open(filename, 'wx')
  const input = await fs.open(victim, 'r')
  const read = input.read.bind(input)
  jest.spyOn(input, 'read').mockImplementationOnce(async (...args) => {
    const result = await read(...args)
    switch (change) {
      case 'append': await fs.appendFile(victim, ' ignored'); break
      case 'truncate': await fs.truncate(victim, 0); break
      default: throw new TypeError(`unknown change ${change satisfies never}`)
    }
    return result
  })
  jest.spyOn(fs, 'open').mockResolvedValueOnce(input)
  try {
    // When copying, then growth is ignored but an incomplete read is refused.
    const pending = writeTar(output, entries)
    switch (change) {
      case 'append':
        await pending
        expect(parseTar(await fs.readFile(filename)).find((entry) => entry.name === 'f')?.contents.toString()).toBe(content)
        break
      case 'truncate':
        await expect(pending).rejects.toMatchObject({ constructor: BadRequestError, message: `build context changed: ${JSON.stringify(victim)}` })
        break
      default: throw new TypeError(`unknown change ${change satisfies never}`)
    }
  } finally { await output.close() }
})

const permissionTest = process.geteuid?.() === 0 ? it.skip : it
permissionTest('reports an unreadable file as a typed permission refusal', async () => {
  // Given an unreadable regular file inside a readable directory.
  await writeTree(root, { files: { 'source/f': 'private' } })
  const victim = join(root, 'source/f')
  await fs.chmod(victim, 0)
  const entries = await contextEntries(join(root, 'source'))
  const output = await fs.open(join(root, 'context.tar'), 'wx')
  try {
    // When the archive reads it, then the error names the actual permission issue.
    await expect(writeTar(output, entries)).rejects.toMatchObject({
      constructor: BadRequestError, message: `build context file is not readable: ${JSON.stringify(victim)}`,
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
  } finally { await output.close(); await fs.chmod(victim, 0o600) }
})
