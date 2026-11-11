/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { BadRequestError, ImageBuilder } from '../src/v2/index'
import { imageWire } from './artifactFixtures'
import { clientAt } from './helpers/client'
import { sentRequest, startFakeApi } from './networkPolicyTestServer'

// One ImageBuilder rule in both SDKs: every value is a string that is not blank
// by Python's whitespace set, and text rendered raw into a Dockerfile line is
// stripped with that set. The fixture is shared with
// sdk-python/tests/test_image_builder_parity.py.
interface Case {
  readonly name: string
  readonly source: { readonly registry?: string; readonly dockerfile?: string; readonly debianSlim?: string | null }
  readonly steps: readonly Step[]
  readonly dockerfile: string
  readonly request: unknown
}
type Step =
  | readonly ['run' | 'pip' | 'cmd' | 'entrypoint', readonly string[]]
  | readonly ['env', Record<string, string>]
  | readonly ['env-name', readonly [string, string]]
  | readonly ['env-map', readonly (readonly [string, string])[]]
  | readonly ['workdir' | 'user', string]
interface Refused {
  readonly name: string
  readonly source: Case['source']
  readonly steps: Case['steps']
  readonly error: string
  readonly tsError?: string
}
const fixture = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'image-builder-parity.json'), 'utf8')) as { readonly cases: readonly Case[]; readonly refused: readonly Refused[] }

function build(testCase: Pick<Case, 'source' | 'steps'>): ImageBuilder {
  let builder = 'debianSlim' in testCase.source
    ? testCase.source.debianSlim == null ? ImageBuilder.debianSlim() : ImageBuilder.debianSlim(testCase.source.debianSlim)
    : testCase.source.registry !== undefined ? ImageBuilder.fromRegistry(testCase.source.registry) : ImageBuilder.fromDockerfile(testCase.source.dockerfile ?? '')
  for (const [op, argument] of testCase.steps) {
    switch (op) {
      case 'run': builder = builder.runCommands(...argument); break
      case 'pip': builder = builder.pipInstall(...argument); break
      case 'env': builder = builder.env(argument); break
      case 'env-name': builder = builder.env(...argument); break
      // SAFETY: a Map is deliberately not a plain object, as Python refuses a non-mapping.
      case 'env-map': builder = builder.env(new Map(argument) as never); break
      case 'workdir': builder = builder.workdir(argument); break
      case 'user': builder = builder.user(argument); break
      case 'cmd': builder = builder.cmd(...argument); break
      case 'entrypoint': builder = builder.entrypoint(...argument); break
      default: throw new TypeError(`unknown fixture operation: ${op satisfies never}`)
    }
  }
  return builder
}

it.each(fixture.cases.map((testCase) => [testCase.name, testCase] as const))('renders the shared fixture: %s', (_name, testCase) => {
  const builder = build(testCase)
  expect(builder.toDockerfile()).toBe(testCase.dockerfile)
  expect(builder.toRequestSource()).toEqual(testCase.request)
})

it.each([
  [{ b: '1', '2': '2', a: '3' }, ['ENV 2="2"', 'ENV b="1"', 'ENV a="3"']],
  [{ b: '1', ' 2': '2', a: '3' }, ['ENV b="1"', 'ENV 2="2"', 'ENV a="3"']],
] as const)('preserves mapping enumeration order when rendering env %j', (values, expected) => {
  // Given: JS enumerates integer keys first; Python dicts retain insertion
  // order. Stripping a key must not reorder the caller's enumerated pairs.
  const builder = ImageBuilder.fromRegistry('alpine')
  // When: the mapping is rendered.
  const dockerfile = builder.env(values).toDockerfile()
  // Then: rendering preserves Object.entries order, including stripped keys.
  expect(dockerfile.trimEnd().split('\n').slice(1)).toEqual(expected)
})

it.each(fixture.refused.map((testCase) => [testCase.name, testCase] as const))('refuses the shared fixture: %s', (_name, testCase) => {
  let error: unknown
  try { build(testCase) } catch (failure) { error = failure }
  expect(error).toBeInstanceOf(BadRequestError)
  expect(error).toMatchObject({ message: testCase.tsError ?? testCase.error })
})

it.each(['3.10', '3.11', '3.12', '3.13', '3.14'])('renders the supported Python series %s after Python whitespace stripping', (series) => {
  // Given a series padded with characters Python strips but JS trim does not.
  const padded = `\u001c${series}\u0085`
  // When the base is rendered.
  const dockerfile = ImageBuilder.debianSlim(padded).toDockerfile()
  // Then the requested series, not the default, selects the base image.
  expect(dockerfile.split('\n')[0]).toBe(`FROM python:${series}-slim-bookworm`)
})

it('keeps U+FEFF in an unsupported Python series, as Python strip does', () => {
  // Given a BOM, which is not Python whitespace.
  const series = '\ufeff3.13'
  // When it selects a base, then it is refused rather than trimmed away.
  expect(() => ImageBuilder.debianSlim(series)).toThrow('ImageBuilder.debianSlim pythonVersion must be one of 3.10, 3.11, 3.12, 3.13, 3.14 (got "\ufeff3.13")')
})

it('reports a stripped blank Python series as an unsupported series', () => {
  // Given an explicitly blank series rather than an omitted argument.
  const series = ' \u001c\u0085 '
  // When it selects a base, then the version error quotes the stripped value.
  expect(() => ImageBuilder.debianSlim(series)).toThrow('ImageBuilder.debianSlim pythonVersion must be one of 3.10, 3.11, 3.12, 3.13, 3.14 (got "")')
})

// SAFETY: every `as never` below is a deliberately invalid plain-JavaScript argument.
it.each([
  ['fromRegistry null', () => ImageBuilder.fromRegistry(null as never)],
  ['fromRegistry U+0085', () => ImageBuilder.fromRegistry('\u0085')],
  ['fromDockerfile number', () => ImageBuilder.fromDockerfile(5 as never)],
  ['fromDockerfile blank', () => ImageBuilder.fromDockerfile(' \u001c ')],
  ['runCommands null', () => ImageBuilder.fromRegistry('alpine').runCommands(null as never)],
  ['pipInstall number', () => ImageBuilder.fromRegistry('alpine').pipInstall(5 as never)],
  ['env value number', () => ImageBuilder.fromRegistry('alpine').env({ A: 5 as never })],
  ['env value undefined', () => ImageBuilder.fromRegistry('alpine').env('A', undefined as never)],
  ['env number', () => ImageBuilder.fromRegistry('alpine').env(5 as never)],
  ['workdir null', () => ImageBuilder.fromRegistry('alpine').workdir(null as never)],
] as const)('refuses %s with BadRequestError', (_name, call) => {
  let error: unknown
  try { call() } catch (failure) { error = failure }
  expect(error).toBeInstanceOf(BadRequestError)
  expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
})

it('strips an images.pull ref as create does, and refuses a non-string before any request', async () => {
  const api = await startFakeApi([{ status: 200, body: { id: 'img', status: 'pending' } }, { status: 200, body: imageWire({ id: 'img', status: 'pending' }) }])
  const client = clientAt(api.url)
  try {
    await expect(client.v2.images.pull(5 as never, { wait: false })).rejects.toBeInstanceOf(BadRequestError)
    expect(api.requests).toEqual([])
    await client.v2.images.pull(' python:3.12 ', { wait: false })
    expect(JSON.parse(sentRequest(api, 2, 0).body)).toMatchObject({ source: { type: 'registry', ref: 'python:3.12' } })
  } finally {
    await client.close()
    await api.close()
  }
})

describe.each(['addLocalFile', 'addLocalDir', 'pipInstallFromRequirements'] as const)('%s local paths', (method) => {
  it.each([null, undefined, 5, false, Buffer.from('/etc/hostname'), [], {}])('refuses a non-string localPath: %p', (path) => {
    // Given a local-source method and a runtime value that is not a string path.
    const builder = ImageBuilder.fromRegistry('alpine')
    const args = method === 'pipInstallFromRequirements' ? [path] : [path, '/app']
    // When the source is added, then it fails before filesystem access.
    // oxlint-disable-next-line anti-slop/no-reflect-apply -- Deliberately pass malformed JavaScript arguments that the public TypeScript signatures forbid.
    expect(() => Reflect.apply(builder[method], builder, args)).toThrow(expect.objectContaining({
      name: 'BadRequestError', message: 'ImageBuilder localPath must be a non-empty string',
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    }))
  })
})

// `user()` renders raw into a USER line, so it follows the shared line rule.
it('refuses a user name with a control character', () => {
  expect(() => ImageBuilder.fromRegistry('alpine').user('root\nRUN x')).toThrow(expect.objectContaining({
    name: 'BadRequestError', message: 'ImageBuilder.user name "root\\nRUN x" must not contain a control character',
  }))
})

it('refuses env() with a mapping and a second argument, as Python does', () => {
  const values = { A: '1' }
  // SAFETY: exercise a plain-JavaScript call with a mapping and an extra argument.
  expect(() => ImageBuilder.fromRegistry('alpine').env(values as never, '2')).toThrow(expect.objectContaining({
    name: 'BadRequestError', message: 'ImageBuilder.env takes a name and a value, or a mapping of names to values',
  }))
})

it('refuses an env mapping with an enumerable symbol key, which no mapping has', () => {
  const values = { A: '1', [Symbol('hidden')]: '2' }
  expect(() => ImageBuilder.fromRegistry('alpine').env(values)).toThrow(expect.objectContaining({ name: 'BadRequestError' }))
  // A non-enumerable symbol is not part of the mapping.
  const hidden = Object.defineProperty({ A: '1' }, Symbol('meta'), { value: 'x', enumerable: false })
  expect(ImageBuilder.fromRegistry('alpine').env(hidden).toDockerfile()).toContain('ENV A="1"')
})
