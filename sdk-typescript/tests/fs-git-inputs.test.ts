/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileSystemService } from '../src/v2/FileSystem'
import { GitService } from '../src/v2/Git'
import { HttpTransport } from '../src/v2/http'
import { INTERNAL } from '../src/v2/internal'

function fixture() {
  const transport = new HttpTransport('https://toolbox.invalid', 'token')
  const requests = [
    jest.spyOn(transport, 'get'), jest.spyOn(transport, 'post'),
    jest.spyOn(transport, 'delete'), jest.spyOn(transport, 'postRaw'),
    jest.spyOn(transport, 'fetch'),
  ]
  return { transport, requests, fs: new FileSystemService(INTERNAL, transport), git: new GitService(INTERNAL, transport) }
}

// Invoke as JavaScript would, without assertions that falsify the SDK types.
async function invoke(service: FileSystemService | GitService, name: string, args: readonly unknown[]): Promise<unknown> {
  const method: unknown = Reflect.get(service, name)
  if (typeof method !== 'function') throw new TypeError(name)
  const result: unknown = await method.call(service, ...args)
  return result
}

const PATH_CALLS: readonly (readonly [string, readonly unknown[]])[] = [
  ['readBytes', []], ['readText', []], ['writeBytes', [new Uint8Array([1])]],
  ['writeText', ['x']], ['presignedDownloadUrl', []], ['presignedUploadUrl', []],
  ['list', []], ['stat', []], ['mkdir', []], ['remove', []], ['move', ['/b']],
  ['grep', ['x']], ['glob', ['*']], ['chmod', [{}]], ['watchDir', [() => undefined]],
  ['downloadFile', ['local']],
]

describe.each(PATH_CALLS)('%s absolute paths', (method, rest) => {
  it.each(['relative', '.', '', null, 42, {}])('refuses %p before requesting', async (path) => {
    // Given
    const { fs, requests } = fixture()
    // When / Then
    await expect(invoke(fs, method, [path, ...rest])).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
      message: expect.stringMatching(/path|source/),
    })
    requests.forEach((request) => expect(request).not.toHaveBeenCalled())
  })
})

const REFUSED: readonly (readonly [string, readonly unknown[], string])[] = [
  ['move', ['/a', 'relative'], 'destination'],
  ['uploadFile', ['missing-local', 'relative'], 'remotePath'],
  ['writeMany', [{ relative: new Uint8Array([1]) }], 'files'],
  ['readMany', [['/a', 'relative']], 'paths'],
  ['replace', [['/a', 'relative'], 'x', 'y'], 'files'],
  ['writeBytes', ['/a', 'text'], 'use writeText() for strings'],
  ['writeBytes', ['/a', [1, 2, 3]], 'data'],
  ['writeBytes', ['/a', {}], 'data'],
  ['writeBytes', ['/a', null], 'data'],
  ['writeText', ['/a', 42], 'text'],
  ['writeText', ['/a', {}], 'text'],
  ['writeText', ['/a', null], 'text'],
  ['writeText', ['/a', new Uint8Array([120])], 'text'],
  ['writeMany', [{ '/a': 'text' }], 'files'],
  ['writeMany', [[['/a', new Uint8Array([1])]]], 'files'],
  ['writeMany', [new Map()], 'files'],
  ['writeMany', [null], 'files'],
  ['writeMany', [{ [Symbol('hidden')]: new Uint8Array([1]) }], 'files'],
  ['list', ['/', { depth: '2' }], 'depth'],
  ['list', ['/', { depth: true }], 'depth'],
  ['list', ['/', { depth: 1.5 }], 'depth'],
  ['watchDir', ['/', null], 'onEvent'],
  ['watchDir', ['/', () => undefined, { recursive: 'yes' }], 'recursive'],
]

it.each(REFUSED)('%s refuses wrong input %p before requesting', async (method, args, parameter) => {
  // Given
  const { fs, requests } = fixture()
  // When / Then
  await expect(invoke(fs, method, args)).rejects.toMatchObject({
    statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(parameter),
  })
  requests.forEach((request) => expect(request).not.toHaveBeenCalled())
})

describe.each(['readMany', 'replace', 'add', 'restore', 'reset'])('%s path sequences', (method) => {
  it.each(['/a', null, 42, new Set(['/a']), ['/a', 42], new Array(1)])('refuses %p before requesting', async (files) => {
    // Given
    const { fs, git, requests } = fixture()
    const calls: Readonly<Record<string, readonly unknown[]>> = {
      readMany: [files], replace: [files, 'x', 'y'], add: ['repo', files],
      restore: ['repo', files], reset: ['repo', { files }],
    }
    const args = calls[method]
    if (args === undefined) throw new TypeError(method)
    // When / Then
    await expect(invoke(method === 'readMany' || method === 'replace' ? fs : git, method, args)).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(/files|paths/),
    })
    requests.forEach((request) => expect(request).not.toHaveBeenCalled())
  })
})

const backing = new Uint8Array([0, 120, 121, 122, 0])
const BUFFERS = [
  new Uint8Array([120, 121, 122]), new Uint8Array([120, 121, 122]).buffer,
  new Uint8Array(backing.buffer, 1, 3), new DataView(backing.buffer, 1, 3),
  new Int8Array(backing.buffer, 1, 3),
]

describe.each(['writeBytes', 'writeMany'])('%s binary content', (method) => {
  it.each(BUFFERS)('writes the exact byte range of %p', async (content) => {
    // Given
    const { fs, transport } = fixture()
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ files: [], errors: [] })
    // When
    await invoke(fs, method, method === 'writeMany' ? [{ '/a': content }] : ['/a', content])
    // Then
    const form: unknown = post.mock.calls[0]?.[1]
    if (!(form instanceof FormData)) throw new TypeError('expected multipart body')
    const file = form.get(method === 'writeMany' ? 'files[0].file' : 'file')
    if (!(file instanceof Blob)) throw new TypeError('expected file part')
    expect(new Uint8Array(await file.arrayBuffer())).toEqual(new Uint8Array([120, 121, 122]))
  })
})

it.each(['add', 'restore', 'reset'])('%s keeps relative git pathspecs', async (method) => {
  // Given
  const { git, transport } = fixture()
  const post = jest.spyOn(transport, 'post').mockResolvedValue(undefined)
  // When
  await invoke(git, method, ['repo', method === 'reset' ? { files: ['relative'] } : ['relative']])
  // Then
  expect(post.mock.calls[0]?.[1]).toMatchObject({ path: 'repo', files: ['relative'] })
})
