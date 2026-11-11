/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'
import { compileProgram } from './dockerignoreNfa.js'
import { translateClass } from './dockerignoreClass.js'

/** POSIX path.Clean, without loading Node in browser bundles. @internal */
export function cleanPath(text: string): string {
  const parts: string[] = []
  const rooted = text.startsWith('/')
  for (const part of text.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..' && parts.length > 0 && parts[parts.length - 1] !== '..') parts.pop()
    else if (part !== '..' || !rooted) parts.push(part)
  }
  return (rooted ? '/' : '') + parts.join('/') || '.'
}

/** Go's TrimSpace deliberately does not remove a byte-order mark. @internal */
export function trimSpace(text: string): string {
  return text.replace(/^[\t-\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\t-\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '')
}

/** @internal */
export function withoutTrailingGlob(text: string): string {
  return text.replace(/\/\*\*$/, '').replace(/\/\*$/, '')
}

/** filepath.Match checks syntax only in chunks it reaches against ".". @internal */
export function checkPattern(text: string, raw: string): void {
  // Port of Go's filepath.Match(text, "."), on code points like Go's runes and
  // without a RegExp: inside a class an escaped character is literal (`\W` is W).
  const syntax = (): never => { throw badRequest(`invalid pattern ${JSON.stringify(raw)}: syntax error in pattern`) }
  let remaining = Array.from(text)
  let name = ['.']
  while (remaining.length > 0) {
    const star = remaining[0] === '*'
    while (remaining[0] === '*') remaining = remaining.slice(1)
    let index = 0
    let inRange = false
    for (; index < remaining.length; index++) {
      const ch = remaining[index]
      if (ch === '\\' && index + 1 < remaining.length) { index++; continue }
      if (ch === '[') inRange = true
      if (ch === ']') inRange = false
      if (ch === '*' && !inRange) break
    }
    const chunk = remaining.slice(0, index)
    remaining = remaining.slice(index)
    if (star && chunk.length === 0) return
    let [tail, ok] = matchChunk(chunk, name, syntax)
    if (ok && (tail.length === 0 || remaining.length > 0)) { name = tail; continue }
    if (star && name.length > 0) {
      ;[tail, ok] = matchChunk(chunk, [], syntax)
      if (ok && (tail.length === 0 || remaining.length > 0)) { name = tail; continue }
    }
    return
  }
}

/** Go's matchChunk: validates the whole chunk even after the match has failed. */
function matchChunk(chunk: readonly string[], name: readonly string[], syntax: () => never): [string[], boolean] {
  let rest = [...chunk]
  let left = [...name]
  let failed = false
  const escaped = (): string => {
    if (rest.length === 0 || rest[0] === '-' || rest[0] === ']') syntax()
    if (rest[0] === '\\') rest = rest.slice(1)
    if (rest.length < 2) syntax()
    const ch = rest[0]
    rest = rest.slice(1)
    return ch
  }
  while (rest.length > 0) {
    failed = failed || left.length === 0
    const ch = rest[0]
    rest = rest.slice(1)
    if (ch === '[') {
      const rune: number = failed ? 0 : left[0].codePointAt(0) ?? 0
      if (!failed) left = left.slice(1)
      const negated = rest[0] === '^'
      if (negated) rest = rest.slice(1)
      let count = 0
      let matched = false
      while (true) {
        if (rest[0] === ']' && count > 0) { rest = rest.slice(1); break }
        const lo = escaped().codePointAt(0) ?? 0
        let hi = lo
        if (rest[0] === '-') { rest = rest.slice(1); hi = escaped().codePointAt(0) ?? 0 }
        matched = matched || (lo <= rune && rune <= hi)
        count++
      }
      failed = failed || matched === negated
    } else if (ch === '?') {
      if (!failed) { failed = left[0] === '/'; left = left.slice(1) }
    } else {
      let literal = ch
      if (ch === '\\') {
        if (rest.length === 0) syntax()
        literal = rest[0]
        rest = rest.slice(1)
      }
      if (!failed) { failed = literal !== left[0]; left = left.slice(1) }
    }
  }
  return [left, !failed]
}

function escapeLiteral(text: string): string {
  return text.replace(/[\\^$.*+?()[\]{}|]/g, '\\$&')
}

/** Port of the CLI's patternmatcher compilation, including prefix/suffix fast paths. @internal */
export function compilePattern(text: string, raw = text): (rel: string) => boolean {
  const runes = Array.from(text.replace(/^\uFEFF/, ''))
  let source = '^'
  let kind: 'exact' | 'prefix' | 'suffix' | 'regexp' = 'exact'
  for (let i = 0, token = 0; i < runes.length; i++, token++) {
    const ch = runes[i]
    if (ch === '*' && runes[i + 1] === '*') {
      i++
      if (runes[i + 1] === '/') i++
      if (i + 1 === runes.length) {
        if (kind === 'exact') kind = 'prefix'
        else { source += '.*'; kind = 'regexp' }
      } else { source += '(.*/)?'; kind = 'regexp' }
      if (token === 0) kind = 'suffix'
    } else if (ch === '*') { source += '[^/]*'; kind = 'regexp' }
    else if (ch === '?') { source += '[^/]'; kind = 'regexp' }
    else if ('.+()|{}$'.includes(ch)) source += `\\${ch}`
    else if (ch === '\\') {
      if (i + 1 < runes.length) {
        i++
        source += `\\${runes[i]}`
        kind = 'regexp'
      } else source += '\\\\'
    } else if (ch === '[' || ch === ']') { source += ch; kind = 'regexp' }
    else source += ch
  }
  // Matching the original text (including a BOM) is intentional for exact patterns.
  switch (kind) {
    case 'exact': return (rel) => rel === text
    case 'prefix': return (rel) => rel.startsWith(text.replace(/\*\*$/, ''))
    case 'suffix': {
      // Go slices strings by byte, even across a retained UTF-8 BOM.
      const encoder = new TextEncoder()
      const suffix = encoder.encode(text).slice(2)
      return (rel) => {
        const name = encoder.encode(rel)
        const start = name.length - suffix.length
        return (start >= 0 && suffix.every((byte, index) => name[start + index] === byte)) ||
          (suffix[0] === 47 && name.length === suffix.length - 1 && name.every((byte, index) => byte === suffix[index + 1]))
      }
    }
    case 'regexp': {
      try {
        const translated = translateRegexp(source + '$', raw)
        new RegExp(translated, 'u') // Validation only: retain the existing errors.
        return compileProgram(translated)
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error
        throw badRequest(`invalid pattern ${JSON.stringify(raw)}: invalid regular expression`)
      }
    }
  }
}

/** Translate the CLI's generated RE2 source, not the user's glob syntax. */
function translateRegexp(source: string, pattern: string): string {
  const invalid: () => never = () => { throw badRequest(`invalid pattern ${JSON.stringify(pattern)}: invalid regular expression`) }
  let translated = ''
  for (let i = 0; i < source.length; i++) {
    const ch = source[i]
    if (ch === '[') {
      const [translatedClass, end] = translateClass(source, i, pattern)
      translated += translatedClass
      i = end - 1
      continue
    }
    if (ch !== '\\') {
      if (ch === ']') translated += '\\]'
      else if (ch === '.') translated += '[^\\n]'
      else if (ch === '$') translated += '$(?![\\s\\S])'
      else translated += ch
      continue
    }
    const escaped = source[++i]
    if (escaped === undefined) invalid()
    if (escaped === 'Q') {
      const end = source.indexOf('\\E', i + 1)
      translated += escapeLiteral(source.slice(i + 1, end === -1 ? source.length : end))
      i = end === -1 ? source.length : end + 1
    } else if (/^[0-7]$/.test(escaped)) {
      const octal = /^[0-7]{1,3}/.exec(source.slice(i))?.[0]
      if (octal === undefined || (octal.length === 1 && octal !== '0')) invalid()
      translated += `\\u{${Number.parseInt(octal, 8).toString(16)}}`
      i += octal.length - 1
    } else if (escaped === 'x') {
      // The glob compiler escapes braces, so only the two-digit form can reach RE2.
      const hex = source.slice(i + 1, i + 3)
      if (!/^[0-9a-fA-F]{2}$/.test(hex)) invalid()
      translated += `\\x${hex}`
      i += 2
    } else if (escaped === 'p' || escaped === 'P') {
      // Braced categories/scripts cannot survive the glob compiler's brace escaping.
      const category = source[++i]
      if (!/^[CLMNPSZclmnpsz]$/.test(category)) invalid()
      // Go 1.25+ (the CLI's toolchain) includes unassigned code points (Cn)
      // in C, as JavaScript's C does.
      translated += `\\${escaped}{${category.toUpperCase()}}`
    } else if (escaped === 'a') translated += '\\x07'
    else if ('fnrtv'.includes(escaped)) translated += `\\${escaped}`
    else if (escaped === 's') translated += '[\\t\\n\\f\\r ]'
    else if (escaped === 'S') translated += '[^\\t\\n\\f\\r ]'
    else if ('dDwW'.includes(escaped)) translated += `\\${escaped}`
    else if (escaped === 'b' || escaped === 'B') translated += `\\${escaped}`
    else if (escaped === 'A') translated += '^'
    else if (escaped === 'z') translated += '(?![\\s\\S])'
    else if (/^[\x00-\x7f]$/.test(escaped) && !/^[A-Za-z0-9]$/.test(escaped)) {
      translated += escapeLiteral(escaped)
    } else invalid()
  }
  return translated
}
