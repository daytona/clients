/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Run with `node --test scripts/gen-contract.test.mjs` (`npm test` runs it as
// `pretest`). Drives the wire-type generator against a synthetic contract
// tagged the way the real contract files are tagged (x-daytona-open-enum /
// x-daytona-exact-enum).
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'
import ts from 'typescript'
import { checkOutput, generateSurface, namedEnums, openMarker } from './gen-contract.mjs'

const open = (...values) => ({ type: 'string', enum: values, 'x-daytona-open-enum': true })
const exact = (reason, ...values) => ({ type: 'string', enum: values, 'x-daytona-exact-enum': reason })
const json = schema => ({ 'application/json': { schema } })

function contract() {
  return {
    openapi: '3.1.0',
    info: { title: 'fixture', version: '1' },
    paths: {
      '/x/{id}': {
        parameters: [{ name: 'id', in: 'path', required: true, schema: exact('parameter', 'a', 'b') }],
        get: {
          parameters: [{ name: 'resolution', in: 'query', schema: exact('parameter', 'h', 'd') }],
          responses: {
            200: { description: 'ok', content: json({ type: 'object', properties: { inline: open('p', 'q') } }) },
            default: { description: 'error', content: json({ type: 'object', properties: { code: open('A', 'B') } }) },
          },
          callbacks: {
            cb: { '{$request.body#/u}': { post: { requestBody: { content: json(open('c1', 'c2')) }, responses: { 200: { description: 'ok' } } } } },
          },
        },
      },
    },
    components: {
      pathItems: {
        Reusable: {
          parameters: [{ name: 'p', in: 'query', schema: exact('parameter', 'r1', 'r2') }],
          get: { responses: { 200: { description: 'ok', content: json({ type: 'object', properties: { item: open('i1', 'i2') } }) } } },
        },
      },
      schemas: {
        Named: { ...open('one', 'two'), description: 'Named doc' },
        Single: open('only'),
        Nullable: { type: ['string', 'null'], enum: ['u', 'v', null], 'x-daytona-open-enum': true },
        Fixed: { type: 'string', const: 'fixed', enum: ['fixed'], 'x-daytona-exact-enum': 'const' },
        default: open('named', 'default'),
        'Foo-Bar': open('x', 'y'),
        Obj: {
          type: 'object',
          required: ['phase'],
          properties: {
            phase: { ...open('a', 'b'), description: 'Phase doc', default: 'a' },
            parameters: open('not', 'a-parameter'),
            list: { type: 'array', items: open('i', 'j') },
            map: { type: 'object', additionalProperties: open('m', 'n') },
          },
        },
        Flattened: { allOf: [{ $ref: '#/components/schemas/Named' }, { properties: { mode: open('f', 's') } }] },
        Chunk: { type: 'object', required: ['type'], properties: { type: exact('discriminator', 'chunk'), kind: open('k1', 'k2') } },
        End: { type: 'object', required: ['type'], properties: { type: exact('discriminator', 'end') } },
        Event: { oneOf: [{ $ref: '#/components/schemas/Chunk' }, { $ref: '#/components/schemas/End' }] },
        // The spec writes a free-form JSON value as `{}`, which renders as `unknown`.
        WithValue: { anyOf: [open('first', 'second'), {}] },
        Conditional: { type: 'object', if: { properties: { a: { const: 'x' } } }, then: { properties: { b: open('t1', 't2') } } },
      },
    },
  }
}

const dirs = []
after(() => dirs.forEach(dir => rmSync(dir, { recursive: true, force: true })))

function fixture(body) {
  const dir = mkdtempSync(join(tmpdir(), 'gen-contract-'))
  dirs.push(dir)
  const file = join(dir, 'spec.json')
  writeFileSync(file, JSON.stringify(body))
  return file
}

test('every open-tagged enum renders open wherever it sits', async () => {
  const { source } = await generateSurface(fixture(contract()))
  for (const expected of [
    'Named: ("one" | "two") | (string & {});',
    'Single: "only" | (string & {});',
    'Nullable: ("u" | "v" | null) | (string & {});',
    'default: ("named" | "default") | (string & {});',
    '"Foo-Bar": ("x" | "y") | (string & {});',
    'phase: ("a" | "b") | (string & {});',
    'parameters?: ("not" | "a-parameter") | (string & {});',
    'list?: (("i" | "j") | (string & {}))[];',
    '[key: string]: ("m" | "n") | (string & {});',
    'mode?: ("f" | "s") | (string & {});',
    'kind?: ("k1" | "k2") | (string & {});',
    'inline?: ("p" | "q") | (string & {});',
    'code?: ("A" | "B") | (string & {});',
    'item?: ("i1" | "i2") | (string & {});',
    'WithValue: (("first" | "second") | (string & {})) | unknown;',
  ]) assert.ok(source.includes(expected), expected)
  // openapi-typescript emits no type for callbacks or `then` subschemas: their
  // open tags must neither appear nor trip the output check.
  for (const absent of ['"c1"', '"t1"']) assert.ok(!source.includes(absent), absent)
})

test('exact enums stay exact, and docs and defaults survive the wrapping', async () => {
  const { source } = await generateSurface(fixture(contract()))
  assert.match(source, /resolution\?: "h" \| "d";/)
  assert.match(source, /id: "a" \| "b";/)
  assert.match(source, /p\?: "r1" \| "r2";/)
  assert.match(source, /type: "chunk";/)
  assert.match(source, /Fixed: "fixed";/)
  assert.match(source, /@description Named doc/)
  assert.match(source, /@description Phase doc\s+\* @default a/)
})

test('the output check fails when the marker is not rendered', async () => {
  await assert.rejects(
    generateSurface(fixture(contract()), { transform: () => undefined }),
    /enum collapsed to plain string: components\.schemas\.Named/,
  )
})

test('the output check fails on an untagged response enum', async () => {
  const untagged = contract()
  delete untagged.components.schemas.Obj.properties.phase['x-daytona-open-enum']
  await assert.rejects(generateSurface(fixture(untagged)), /closed enum: components\.schemas\.Obj\.phase/)
})

test('the output check fails on a single-value named enum tagged open but rendered closed', async () => {
  const untagged = contract()
  // The marker is dropped for Single only; the other enums still render open.
  const dropSingle = (schema, options) => (options?.path?.startsWith('#/components/schemas/Single') ? undefined : openMarker(schema))
  await assert.rejects(generateSurface(fixture(untagged), { transform: dropSingle }), /closed enum: components\.schemas\.Single/)
})

test('the output check fails when the marker renders as unknown', async () => {
  const asUnknown = schema => (openMarker(schema) ? ts.factory.createKeywordTypeNode(ts.SyntaxKind.UnknownKeyword) : undefined)
  await assert.rejects(generateSurface(fixture(contract()), { transform: asUnknown }), /enum collapsed to plain string: components\.schemas\.Named/)
})

test('the output check fails on an open enum in a parameter', async () => {
  const opened = contract()
  opened.paths['/x/{id}'].get.parameters[0].schema = open('h', 'd')
  await assert.rejects(generateSurface(fixture(opened)), /open enum in a parameter: paths\.\/x\/\{id\}\.get\.parameters\.query\.resolution/)
})

test('checkOutput reads enum-shaped unions only', () => {
  const source = `export interface components { schemas: {
    Mixed: "a" | 1;
    Plain: string | null;
    Tag: "only";
    Closed: "a" | "b" | null;
    Collapsed: ("a" | "b") | string;
    WithValue: (("a" | "b") | (string & {})) | unknown;
  } }`
  assert.deepEqual(checkOutput(source), ['closed enum: components.schemas.Closed', 'enum collapsed to plain string: components.schemas.Collapsed'])
})

test('the known-value list file emits only names that are valid bindings', () => {
  const listed = namedEnums(contract()).map(([name]) => name)
  assert.ok(listed.includes('Named'))
  assert.ok(!listed.includes('default'))
  assert.ok(!listed.includes('Foo-Bar'))
})
