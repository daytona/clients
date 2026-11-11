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
import { contextUploadServer } from './contextUploadServer.js'
import escapeCases from '../../sdk-python/tests/fixtures/dockerfile-escape.json'

let root: string
beforeEach(async () => { root = await fs.mkdtemp(join(tmpdir(), 'builder-escape-')) })
afterEach(async () => { await fs.rm(root, { recursive: true, force: true }) })

it.each([
  '# escape=`\nFROM alpine',
  '  # EsCaPe = `\nFROM alpine',
  '# syntax=docker/dockerfile:1\n# check=skip=all\n# escape=`\nFROM alpine',
  '# escape=x\nFROM alpine',
])('refuses non-default parser escapes before reading local sources: %s', async (dockerfile) => {
  // Given a builder whose source disappears after declaration.
  const source = join(root, 'input')
  await fs.writeFile(source, 'content')
  const builder = ImageBuilder.fromDockerfile(dockerfile).addLocalFile(source, '/out/')
  await fs.unlink(source)
  // When archived, then directive validation wins over the missing-file error.
  await expect(archiveBuilder(builder)).rejects.toMatchObject({
    constructor: BadRequestError, message: 'local files need the default Dockerfile escape character',
  })
})

it.each([
  '# escape=\nFROM alpine',
  '# escape=\n# escape=`\nFROM alpine',
  '# escape=\\\nFROM alpine',
  '# escape=\\\r\nFROM alpine',
  '# syntax=docker/dockerfile:1\n# escape=\\\nFROM alpine',
  '# ordinary comment\n# escape=`\nFROM alpine',
  '# syntax=docker/dockerfile:1\n\n# escape=`\nFROM alpine',
  '# unsupported=value\n# escape=`\nFROM alpine',
  'FROM alpine\n# escape=`',
])('allows default escapes or directives after scanning stops: %s', async (dockerfile) => {
  // Given a default escape or a comment that is no longer a parser directive.
  const source = join(root, 'input')
  await fs.writeFile(source, 'content')
  const builder = ImageBuilder.fromDockerfile(dockerfile).addLocalFile(source, '/out/')
  // When archived.
  const archive = await archiveBuilder(builder)
  try {
    // Then the local file remains part of the build context.
    expect(parseTar(await fs.readFile(archive.path)).find((entry) => entry.name.endsWith('/input'))?.contents.toString()).toBe('content')
  } finally { await archive.cleanup() }
})

it('leaves a non-default escape unchanged when there are no local sources', async () => {
  // Given a raw Dockerfile-only builder using the alternate escape character.
  const builder = ImageBuilder.fromDockerfile('# escape=`\nFROM alpine')
  const api = await contextUploadServer()
  try {
    // When built, then the Dockerfile is submitted without local-context validation.
    await api.images.build(builder)
    expect(api.json('POST', '/images')).toEqual({ dockerfile: '# escape=`\nFROM alpine\n' })
  } finally { await api.close() }
})

describe.each(['name', 'mapping'] as const)('env via %s', (kind) => {
  it.each(escapeCases.cases)('uses BuildKit parser directives for $dockerfile', (testCase) => {
    // Given: BuildKit's shared cases include empty directives, unknown names,
    // BOMs and Unicode whitespace that main's former simple regex misread.
    const builder = ImageBuilder.fromDockerfile(testCase.dockerfile)
    // When: env adds a literal value under the selected parser escape.
    const add = () => kind === 'name' ? builder.env('TOKEN', '$HOME') : builder.env({ TOKEN: '$HOME' })
    // Then: only backslash escapes can preserve the value literally.
    if (testCase.escape === '\\') {
      expect(add().toDockerfile().split('\n').slice(-2)).toEqual(['ENV TOKEN="\\$HOME"', ''])
    } else {
      expect(add).toThrow(expect.objectContaining({
        constructor: BadRequestError,
        message: 'ImageBuilder.env cannot keep values literal under a Dockerfile escape directive other than a backslash',
      }))
    }
  })

  it.each([...Array.from({ length: 32 }, (_, code) => code), 127])('refuses control character %i without echoing the value', (code) => {
    // Given: a secret value containing a C0 or DEL character.
    const builder = ImageBuilder.fromRegistry('alpine')
    const value = `secret${String.fromCharCode(code)}value`
    // When: either env overload receives it, then the refusal never includes it.
    const add = () => kind === 'name' ? builder.env('TOKEN', value) : builder.env({ TOKEN: value })
    expect(add).toThrow(expect.objectContaining({
      constructor: BadRequestError, message: 'ImageBuilder.env value must not contain a control character',
    }))
  })
})
