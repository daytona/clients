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
  readonly source: { readonly registry?: string; readonly dockerfile?: string }
  readonly steps: ReadonlyArray<readonly [string, unknown]>
  readonly dockerfile: string
  readonly request: unknown
}
interface Refused {
  readonly name: string
  readonly source: Case['source']
  readonly steps: Case['steps']
  readonly error: string
  readonly tsError?: string
}
const fixture = JSON.parse(readFileSync(join(__dirname, '..', '..', 'sdk-python', 'tests', 'fixtures', 'image-builder-parity.json'), 'utf8')) as { readonly cases: readonly Case[]; readonly refused: readonly Refused[] }

function build(testCase: Pick<Case, 'source' | 'steps'>): ImageBuilder {
  let builder = testCase.source.registry !== undefined ? ImageBuilder.fromRegistry(testCase.source.registry) : ImageBuilder.fromDockerfile(testCase.source.dockerfile ?? '')
  for (const [op, argument] of testCase.steps) {
    if (op === 'run') builder = builder.runCommands(...(argument as string[]))
    else if (op === 'pip') builder = builder.pipInstall(...(argument as string[]))
    else if (op === 'env') builder = builder.env(argument as Record<string, string>)
    else if (op === 'env-name') builder = builder.env(...(argument as [string, string]))
    // A Map is not a plain object, as Python refuses a non-mapping.
    else if (op === 'env-map') builder = builder.env(new Map(argument as Array<[string, string]>) as never)
    else builder = builder.workdir(argument as string)
  }
  return builder
}

it.each(fixture.cases.map((testCase) => [testCase.name, testCase] as const))('renders the shared fixture: %s', (_name, testCase) => {
  const builder = build(testCase)
  expect(builder.toDockerfile()).toBe(testCase.dockerfile)
  expect(builder.toRequestSource()).toEqual(testCase.request)
})

it.each(fixture.refused.map((testCase) => [testCase.name, testCase] as const))('refuses the shared fixture: %s', (_name, testCase) => {
  let error: unknown
  try { build(testCase) } catch (failure) { error = failure }
  expect(error).toBeInstanceOf(BadRequestError)
  expect(error).toMatchObject({ message: testCase.tsError ?? testCase.error })
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

// `user()` is TypeScript-only for now; it renders raw into a USER line, so it
// follows the same rule as the shared steps.
it('refuses a user name with a control character', () => {
  expect(() => ImageBuilder.fromRegistry('alpine').user('root\nRUN x')).toThrow(expect.objectContaining({
    name: 'BadRequestError', message: 'ImageBuilder.user name "root\\nRUN x" must not contain a control character',
  }))
})
