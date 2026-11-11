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
const GO_SPACE = new Set('\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000')
// The whitespace DirectiveParser trims around `=` and after the value.
const DIRECTIVE_SPACE = new Set('\t\n\f\r ')

// Linear scans (no backtracking regex on caller text).
function trimStart(text: string, chars: ReadonlySet<string>): string {
  let start = 0
  while (start < text.length && chars.has(text[start])) start++
  return text.slice(start)
}

function trimEnd(text: string, chars: ReadonlySet<string>): string {
  let end = text.length
  while (end > 0 && chars.has(text[end - 1])) end--
  return text.slice(0, end)
}

/** A line as bufio.ScanLines yields it: the first line's BOM dropped, trailing CR/LF removed. */
function scannedLine(raw: string, index: number): string {
  return trimEnd(index === 0 && raw.startsWith('\ufeff') ? raw.slice(1) : raw, new Set('\r\n'))
}

/** DirectiveParser's `name = value` (after `#`), or null when the line is not one. */
function directive(text: string): readonly [string, string] | null {
  let end = 0
  while (end < text.length && (/[a-zA-Z]/.test(text[end]) || (end > 0 && /[0-9]/.test(text[end])))) end++
  if (end === 0) return null
  const rest = trimStart(text.slice(end), DIRECTIVE_SPACE)
  if (!rest.startsWith('=')) return null
  const after = rest.slice(1)
  if (after === '' || after.includes('\n')) return null
  // BuildKit's `\s*=\s*(.+?)\s*$` backtracks so a value of only space keeps
  // its last character: `# syntax= ` is a directive (and parsing goes on).
  return [text.slice(0, end), trimEnd(trimStart(after, DIRECTIVE_SPACE), DIRECTIVE_SPACE) || after[after.length - 1]]
}

/**
 * The escape character BuildKit reads from the parser directives: a port of
 * BuildKit's processLine + DirectiveParser.ParseLine (v0.31.2), as Python's
 * dockerfile_escape. Shared by env rendering and local COPY escaping. @internal
 */
export function dockerfileEscape(dockerfile: string): string {
  let escape = '\\'
  const seen = new Set<string>()
  for (const [index, raw] of dockerfile.split('\n').entries()) {
    const line = trimStart(scannedLine(raw, index), GO_SPACE)
    if (!line.startsWith('#')) break
    const match = directive(trimStart(line.slice(1), GO_SPACE))
    if (match === null) break
    const name = match[0].toLowerCase()
    if (!['syntax', 'escape', 'check'].includes(name) || seen.has(name)) break
    seen.add(name)
    if (name === 'escape') escape = match[1]
  }
  return escape
}

/**
 * Whether BuildKit would join a line appended to `dockerfile` into its last
 * instruction: a port of BuildKit v0.31.2's Parse loop (continuation rule
 * `([^E])E[ \t]*$|^E[ \t]*$`, comment and whitespace-only lines skipped inside a
 * continuation, Go's unicode.IsSpace). An escape token other than `\` or a
 * backtick joins nothing: BuildKit refuses that Dockerfile. Python reads the
 * same. Heredocs are not followed. @internal
 */
export function endsWithContinuation(dockerfile: string): boolean {
  const escape = dockerfileEscape(dockerfile)
  if (escape !== '\\' && escape !== '`') return false
  // pending: the last instruction continues; carried: it holds text, so a
  // joined line would change it (an escape alone on a line carries nothing).
  let pending = false
  let carried = false
  for (const [index, raw] of dockerfile.split('\n').entries()) {
    const line = scannedLine(raw, index)
    const stripped = trimStart(line, GO_SPACE)
    // A comment ends no instruction and continues none.
    if (stripped.startsWith('#')) continue
    if (pending && stripped === '') continue
    const text = trimEnd(pending ? line : stripped, new Set(' \t'))
    // BuildKit's ReplaceAll(line, "$1"): what the line adds to the instruction.
    const before = text.slice(0, -1)
    const matched = text.endsWith(escape) && !before.endsWith(escape)
    const added = matched ? before : text
    carried = (carried && pending) || trimEnd(trimStart(added, GO_SPACE), GO_SPACE) !== ''
    pending = matched
  }
  return pending && carried
}
