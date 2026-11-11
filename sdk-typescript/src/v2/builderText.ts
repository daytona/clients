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

// Use Python's whitespace set for parity; JSON-rendered values stay as given.
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
      return step.entries.map(([key, value]) => `ENV ${key}=${JSON.stringify(value)}`).join('\n')
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
  if (text.endsWith('\\')) throw badRequest(`${label} ${JSON.stringify(text)} must not end with a backslash (it would continue onto the next instruction)`)
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
