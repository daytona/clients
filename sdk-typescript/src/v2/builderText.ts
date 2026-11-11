/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { hasLoneSurrogate } from './pathSegment.js'
import { badRequest } from './options.js'
import { hasSpace, isBlank, strip } from './pyText.js'

/** @internal */
export type BuilderSource =
  | { readonly type: 'registry'; readonly ref: string }
  | { readonly type: 'dockerfile'; readonly dockerfile: string }

/** @internal */
export type BuilderStep =
  | { readonly type: 'run'; readonly commands: readonly string[] }
  | { readonly type: 'env'; readonly entries: readonly (readonly [string, string])[] }
  | { readonly type: 'workdir'; readonly path: string }
  | { readonly type: 'user'; readonly name: string }
  | { readonly type: 'cmd'; readonly args: readonly string[] }
  | { readonly type: 'entrypoint'; readonly args: readonly string[] }
  | { readonly type: 'copy'; readonly archivePath: string; readonly remotePath: string }

// Use Python's whitespace set for parity; env values stay literal, as given.
/** @internal */
export function assertText(value: unknown, label: string): string {
  if (typeof value !== 'string' || isBlank(value)) throw badRequest(`${label} must be a non-empty string`)
  if (hasLoneSurrogate(value)) throw badRequest(`${label} must not contain a lone surrogate`)
  return value
}

/** @internal */
export function rawText(value: unknown, label: string): string {
  return strip(assertText(value, label))
}

/** @internal */
export function assertTextList(values: readonly string[], label: string): readonly string[] {
  if (!Array.isArray(values) || values.length === 0) throw badRequest(`${label} requires at least one value`)
  values.forEach((value) => { assertText(value, label) })
  return values
}

function renderJsonArray(values: readonly string[]): string {
  return `[${values.map((value) => JSON.stringify(value)).join(', ')}]`
}

/** @internal */
export function renderStep(step: BuilderStep): string {
  switch (step.type) {
    case 'run':
      return step.commands.map((command) => `RUN ${command}`).join('\n')
    case 'env':
      return step.entries.map(([key, value]) => `ENV ${key}=${JSON.stringify(value).replace(/\$/g, () => '\\$')}`).join('\n')
    case 'workdir':
      return `WORKDIR ${step.path}`
    case 'user':
      return `USER ${step.name}`
    case 'cmd':
      return `CMD ${renderJsonArray(step.args)}`
    case 'entrypoint':
      return `ENTRYPOINT ${renderJsonArray(step.args)}`
    case 'copy':
      return `COPY ${renderJsonArray([step.archivePath, step.remotePath])}`
    default:
      return assertNever(step)
  }
}

/** @internal */
export function assertNever(value: never): never {
  throw badRequest(`unknown image builder step: ${JSON.stringify(value)}`)
}

/** Raw text for one Dockerfile line: C0 and DEL would change the instruction. @internal */
export function lineText(value: unknown, label: string): string {
  const text = rawText(value, label)
  if (/[\x00-\x1f\x7f]/.test(text)) throw badRequest(`${label} ${JSON.stringify(text)} must not contain a control character`)
  // A raw line would continue onto the next instruction, and BuildKit's word
  // lexer runs over JSON-form COPY paths too, where it is dropped.
  if (text.endsWith('\\')) throw badRequest(`${label} ${JSON.stringify(text)} must not end with a backslash (the Dockerfile parser reads it as an escape)`)
  return text
}

/** Python's `shlex.quote`, byte for byte. @internal */
export function shellQuote(value: string): string {
  if (value === '') return "''"
  if (/^[A-Za-z0-9@%+=:,./_-]+$/.test(value)) return value
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

/** @internal */
export function envName(value: string, label: string): string {
  const name = rawText(value, label)
  if (/[=\x00-\x1f\x7f]/.test(name) || hasSpace(name)) {
    throw badRequest(`${label} ${JSON.stringify(name)} must not contain '=', whitespace or a control character`)
  }
  return name
}

/** Env values are literal Dockerfile words; C0 and DEL have no literal form. @internal */
export function envValue(value: unknown, label: string): string {
  const text = assertText(value, label)
  // Do not echo the value: it may be a secret.
  if (/[\x00-\x1f\x7f]/.test(text)) throw badRequest(`${label} must not contain a control character`)
  return text
}

// Go's unicode.IsSpace (Latin-1 and the Unicode White_Space set).
const GO_SPACE = /^[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/

/**
 * The escape character BuildKit reads from the parser directives: a port of
 * BuildKit's processLine + DirectiveParser.ParseLine (v0.31.2), as Python's
 * dockerfile_escape. Shared by env rendering and local COPY escaping. @internal
 */
export function dockerfileEscape(dockerfile: string): string {
  let escape = '\\'
  const seen = new Set<string>()
  for (const [index, raw] of dockerfile.split('\n').entries()) {
    const line = (index === 0 ? raw.replace(/^\ufeff/, '') : raw).replace(/[\r\n]+$/, '').replace(GO_SPACE, '')
    if (!line.startsWith('#')) break
    const match = /^([a-zA-Z][a-zA-Z0-9]*)[\t\n\f\r ]*=[\t\n\f\r ]*([^\n]+?)[\t\n\f\r ]*$/.exec(line.slice(1).replace(GO_SPACE, ''))
    if (match === null) break
    const name = match[1].toLowerCase()
    if (!['syntax', 'escape', 'check'].includes(name) || seen.has(name)) break
    seen.add(name)
    if (name === 'escape') escape = match[2]
  }
  return escape
}

/**
 * Whether the last instruction line ends with the escape character: BuildKit
 * joins it with the next line, so a step appended to the base would become
 * part of its last instruction. A comment line is not a continuation. Python
 * reads the same. @internal
 */
export function endsWithContinuation(dockerfile: string): boolean {
  const escape = dockerfileEscape(dockerfile)
  for (const line of dockerfile.split('\n').reverse()) {
    const text = line.replace(/[ \t\r]+$/, '')
    if (text === '' || text.replace(/^[ \t]+/, '').startsWith('#')) continue
    return text.endsWith(escape)
  }
  return false
}
