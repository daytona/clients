/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Regenerates the vendored wire types src/v2/gen/{toolbox,api}.ts from the
// contracts (../contract/{toolbox,api}-openapi.json) with
// openapi-typescript, plus src/v2/gen/{toolbox,api}-enums.ts (the known values of
// every named string enum).
//
// Open enums. An OpenAPI string enum normally becomes a closed literal union
// (`"a" | "b"`), so every value the server adds to a response enum would be a
// type-level breaking change. Which enums are open is decided once, in the
// contract: every string enum carries
// `x-daytona-open-enum: true` or `x-daytona-exact-enum: <reason>`. This
// script only reads that tag:
//
// 1. Before generation it wraps every open-tagged schema in place as
//    `anyOf: [<the enum>, <marker>]`, keeping its docs and default on the
//    wrapper.
// 2. openapi-typescript's public `transform` hook renders the marker as
//    `(string & {})`, so the type reads `("a" | "b") | (string & {})`: editors
//    still autocomplete the known values, and any string is assignable.
// 3. After generation, checkOutput() re-parses the emitted TypeScript and fails
//    on any union of two or more string literals that has no `(string & {})`
//    outside an operation parameter (or one collapsed to plain `string`), and
//    on any open union inside one.
//    It checks the artifact itself, so a generator upgrade that renders the
//    marker differently fails loudly instead of shipping a closed enum. A
//    one-literal union looks like a discriminator tag in the output, so for
//    named schemas checkNamedSchemas() also holds each open tag to the output.
//
// Nothing here walks the spec to predict what openapi-typescript will emit.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString, COMMENT_HEADER } from 'openapi-typescript'
import ts from 'typescript'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

const OPEN_TAG = 'x-daytona-open-enum'
const MARKER = 'x-daytona-open-member'
// Keys that describe the enum's values; everything else (description, title,
// default, deprecated, examples, …) stays on the wrapper, where
// openapi-typescript reads docs and defaults from.
const VALUE_KEYS = new Set(['type', 'enum', 'nullable', 'format'])

// `string & {}`: assignable from any string, but not collapsed into `string`
// by the compiler, so the literal members survive for autocompletion.
const OPEN_STRING = ts.factory.createParenthesizedType(
  ts.factory.createIntersectionTypeNode([
    ts.factory.createKeywordTypeNode(ts.SyntaxKind.StringKeyword),
    ts.factory.createTypeLiteralNode([]),
  ]),
)

// Returns a copy of `spec` with every open-tagged schema wrapped.
export function wrapOpenEnums(spec) {
  const wrap = node => {
    if (Array.isArray(node)) return node.map(wrap)
    if (node === null || typeof node !== 'object') return node
    const out = {}
    for (const [key, value] of Object.entries(node)) out[key] = wrap(value)
    if (out[OPEN_TAG] !== true) return out
    const inner = {}
    const outer = {}
    for (const [key, value] of Object.entries(out)) {
      if (key === OPEN_TAG) continue
      if (VALUE_KEYS.has(key)) inner[key] = value
      else outer[key] = value
    }
    outer.anyOf = [inner, { type: 'string', [MARKER]: true }]
    return outer
  }
  return wrap(spec)
}

export function openMarker(schemaObject) {
  if (schemaObject !== null && typeof schemaObject === 'object' && schemaObject[MARKER] === true) return OPEN_STRING
  return undefined
}

const propertyName = node => {
  const name = node.name
  return name && (ts.isIdentifier(name) || ts.isStringLiteral(name)) ? name.text : undefined
}

// An operation (or path-level, or component) parameter in openapi-typescript's
// output: `paths[p].parameters`, `paths[p][method].parameters`,
// `operations[op].parameters`, the same under `webhooks` and
// `components.pathItems`, and `components.parameters`.
function inParameter(names) {
  const [scope] = names
  if (scope === 'paths' || scope === 'webhooks') return names[2] === 'parameters' || names[3] === 'parameters'
  if (scope === 'operations') return names[2] === 'parameters'
  if (scope !== 'components') return false
  if (names[1] === 'pathItems') return names[3] === 'parameters' || names[4] === 'parameters'
  return names[1] === 'parameters'
}

const isOpenMember = node =>
  ts.isIntersectionTypeNode(node) &&
  node.types.length === 2 &&
  node.types[0].kind === ts.SyntaxKind.StringKeyword &&
  ts.isTypeLiteralNode(node.types[1]) &&
  node.types[1].members.length === 0
const isPlainString = node => node.kind === ts.SyntaxKind.StringKeyword
const isTopType = node => node.kind === ts.SyntaxKind.UnknownKeyword || node.kind === ts.SyntaxKind.AnyKeyword
// Whether the literals' union was collapsed: a bare `string` beside them
// always collapses it (no autocompletion); `unknown`/`any` mean the marker
// rendered as a top type only when no `(string & {})` is present — beside the
// marker they come from an unconstrained schema in the spec itself
// (`anyOf: [<enum>, {}]`).
const isCollapsed = members => members.some(isPlainString) || (!members.some(isOpenMember) && members.some(isTopType))
const isStringLiteral = node => ts.isLiteralTypeNode(node) && ts.isStringLiteral(node.literal)
const isNull = node => ts.isLiteralTypeNode(node) && node.literal.kind === ts.SyntaxKind.NullKeyword

// The members of a union with nested and parenthesized unions flattened.
function flatten(node) {
  if (ts.isParenthesizedTypeNode(node)) return flatten(node.type)
  if (ts.isUnionTypeNode(node)) return node.types.flatMap(flatten)
  return [node]
}

// Re-parses emitted TypeScript and returns every enum-shaped union in the wrong
// state: closed (or collapsed to plain `string`) outside a parameter, or open
// inside one.
export function checkOutput(source) {
  const file = ts.createSourceFile('gen.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const problems = []
  const visit = (node, names) => {
    if (ts.isInterfaceDeclaration(node) || ts.isPropertySignature(node)) {
      const name = ts.isInterfaceDeclaration(node) ? node.name.text : propertyName(node)
      names = name === undefined ? names : [...names, name]
    }
    if (ts.isUnionTypeNode(node)) {
      const members = flatten(node)
      const strings = members.filter(isStringLiteral).length
      const open = members.some(isOpenMember)
      // A bare `string` beside the literals: the marker was rendered as plain
      // `string`, which collapses the union and loses autocompletion.
      const plain = isCollapsed(members)
      const isEnumUnion = members.every(
        member => isStringLiteral(member) || isNull(member) || isOpenMember(member) || isPlainString(member) || isTopType(member),
      )
      if (isEnumUnion && strings >= 2) {
        const where = names.join('.')
        if (inParameter(names)) {
          if (open || plain) problems.push(`open enum in a parameter: ${where}`)
        } else if (plain) {
          problems.push(`enum collapsed to plain string: ${where}`)
        } else if (!open) {
          problems.push(`closed enum: ${where}`)
        }
      }
      for (const member of members) if (!isStringLiteral(member) && !isNull(member)) visit(member, names)
      return
    }
    ts.forEachChild(node, child => visit(child, names))
  }
  visit(file, [])
  return problems
}

// The type of every `components.schemas.<name>` in emitted TypeScript, by name.
function schemaTypes(source) {
  const file = ts.createSourceFile('gen.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const types = new Map()
  const visit = (node, names) => {
    if (ts.isInterfaceDeclaration(node) || ts.isPropertySignature(node)) {
      const name = ts.isInterfaceDeclaration(node) ? node.name.text : propertyName(node)
      names = name === undefined ? names : [...names, name]
      if (ts.isPropertySignature(node) && names.length === 3 && names[0] === 'components' && names[1] === 'schemas' && node.type) {
        types.set(names[2], node.type)
        return
      }
    }
    ts.forEachChild(node, child => visit(child, names))
  }
  visit(file, [])
  return types
}

// checkOutput() reads unions of two or more literals only: a one-literal union
// is indistinguishable from a discriminator tag in the output. Every top-level
// named schema is emitted at `components.schemas.<name>`, so for those the tag
// itself is checked against the output, whatever the number of values: an
// open-tagged named schema must render open.
export function checkNamedSchemas(spec, source) {
  const types = schemaTypes(source)
  const problems = []
  for (const [name, schema] of Object.entries(spec.components?.schemas ?? {})) {
    if (schema?.[OPEN_TAG] !== true) continue
    const type = types.get(name)
    const members = type ? flatten(type) : []
    if (!members.some(isOpenMember) || isCollapsed(members)) problems.push(`closed enum: components.schemas.${name}`)
  }
  return problems
}

// A schema name usable as an `export const` binding: an identifier that is not
// a reserved word (`default`, `Foo-Bar` are valid schema names but not
// bindings). Other names are left out of the list file; their wire types are
// still opened.
function isBindingName(name) {
  if (!/^[A-Za-z_$][\w$]*$/.test(name)) return false
  try {
    new Function(`'use strict'; const ${name} = 0`)
    return true
  } catch {
    return false
  }
}

// Runtime lists of the known values of every named string enum, so the SDK's
// boundary normalizers stay in lockstep with the contract.
export function namedEnums(spec) {
  const out = []
  for (const [name, schema] of Object.entries(spec.components?.schemas ?? {})) {
    if (!Array.isArray(schema?.enum) || !isBindingName(name)) continue
    const values = schema.enum.filter(v => typeof v === 'string')
    if (values.length > 0) out.push([name, values])
  }
  return out.sort(([a], [b]) => a.localeCompare(b))
}

// Generates one surface's wire types and checks the output. `transform` is
// injectable so the tests can prove the check fires.
export async function generateSurface(file, { transform = openMarker } = {}) {
  const spec = JSON.parse(readFileSync(file, 'utf8'))
  const ast = await openapiTS(wrapOpenEnums(spec), { transform })
  const source = `${COMMENT_HEADER}${astToString(ast)}`
  const problems = [...new Set([...checkOutput(source), ...checkNamedSchemas(spec, source)])]
  if (problems.length > 0) throw new Error(`generated types are wrong:\n  ${problems.join('\n  ')}`)
  return { spec, source }
}

// Preserve explicit nulls in volume results, including manifestId.
const VOLUME_ALIASES = {
  api: [
    ['CreateVolumeRequest', 'Schema<"CreateVolumeRequest">'],
    ['ListVolumesResponse', 'Schema<"VolumePage">'],
    ['Volume', 'Schema<"Volume">'],
  ],
  toolbox: [
    // Callers may omit the wait even though the contract supplies a default.
    ['PullVolumeRequest', 'Omit<Schema<"PullVolumeRequest">, "waitMs"> & { readonly waitMs?: Schema<"PullVolumeRequest">["waitMs"] }'],
    ['PullVolumeResponse', 'Schema<"PullVolumeResponse">'],
  ],
}

function writeVolumeAliases(surface) {
  const file = join(root, 'src', 'v2', 'gen', `${surface}-types.ts`)
  let source = readFileSync(file, 'utf8')
  for (const [alias, type] of VOLUME_ALIASES[surface]) {
    const line = `export type ${alias} = ${type}`
    const existing = new RegExp(`^export type ${alias} = .*\\n?`, 'm')
    source = existing.test(source) ? source.replace(existing, `${line}\n`) : `${source.trimEnd()}\n${line}\n`
  }
  writeFileSync(file, source)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const surface of ['toolbox', 'api']) {
    const file = join(root, '..', 'contract', `${surface}-openapi.json`)
    const { spec, source } = await generateSurface(file).catch(error => {
      throw new Error(`${surface}: ${error.message}`)
    })
    writeFileSync(join(root, 'src', 'v2', 'gen', `${surface}.ts`), source)
    writeVolumeAliases(surface)
    const lists = namedEnums(spec).map(([name, values]) => `export const ${name} = ${JSON.stringify(values)} as const`)
    writeFileSync(
      join(root, 'src', 'v2', 'gen', `${surface}-enums.ts`),
      `// Generated by scripts/gen-contract.mjs from contract/${surface}-openapi.json — do not edit.\n// Known values of every named string enum.\n${lists.join('\n')}\n`,
    )
  }
}
