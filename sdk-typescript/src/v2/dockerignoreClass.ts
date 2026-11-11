/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertNever } from './builderText.js'
import { badRequest } from './options.js'

type Range = readonly [number, number]
type Item =
  | { readonly kind: 'range'; readonly low: number; readonly high: number }
  | { readonly kind: 'ascii'; readonly ranges: readonly Range[]; readonly complement: boolean }
  | { readonly kind: 'unicode'; readonly name: string; readonly positive: boolean }

// RE2's POSIX and Perl classes use ASCII tables, even in Unicode patterns.
const POSIX = {
  alnum: [[48, 57], [65, 90], [97, 122]], alpha: [[65, 90], [97, 122]],
  ascii: [[0, 127]], blank: [[9, 9], [32, 32]], cntrl: [[0, 31], [127, 127]],
  digit: [[48, 57]], graph: [[33, 126]], lower: [[97, 122]], print: [[32, 126]],
  punct: [[33, 47], [58, 64], [91, 96], [123, 126]], space: [[9, 13], [32, 32]],
  upper: [[65, 90]], word: [[48, 57], [65, 90], [95, 95], [97, 122]],
  xdigit: [[48, 57], [65, 70], [97, 102]],
} as const satisfies Readonly<Record<string, readonly Range[]>>
const PERL = { d: POSIX.digit, s: [[9, 10], [12, 13], [32, 32]], w: POSIX.word } as const

/** parseClassChar calls parseEscape, never a shorthand-set parser. */
function classChar(source: string, start: number, invalid: () => never): readonly [number, number] {
  const char = source.codePointAt(start)
  if (char === undefined) invalid()
  if (source[start] !== '\\') return [char, start + String.fromCodePoint(char).length]
  const index = start + 1
  const escaped = source[index]
  if (escaped === undefined) invalid()
  if (/^[0-7]$/.test(escaped)) {
    const octal = /^[0-7]{1,3}/.exec(source.slice(index))?.[0]
    if (octal === undefined || (octal.length === 1 && octal !== '0')) invalid()
    return [Number.parseInt(octal, 8), index + octal.length]
  }
  if (escaped === 'x') {
    const hex = source.slice(index + 1, index + 3)
    if (!/^[0-9a-fA-F]{2}$/.test(hex)) invalid()
    return [Number.parseInt(hex, 16), index + 3]
  }
  const control = new Map([['a', 7], ['f', 12], ['n', 10], ['r', 13], ['t', 9], ['v', 11]]).get(escaped)
  if (control !== undefined) return [control, index + 1]
  if (/^[\x00-\x7f]$/.test(escaped) && !/^[A-Za-z0-9]$/.test(escaped)) return [escaped.charCodeAt(0), index + 1]
  return invalid()
}

/** Parse Go's Perl-mode item loop before emitting any host class contents. @internal */
export function translateClass(source: string, start: number, pattern: string): readonly [string, number] {
  const invalid: () => never = () => { throw badRequest(`invalid pattern ${JSON.stringify(pattern)}: invalid regular expression`) }
  let index = start + 1
  const negated = source[index] === '^'
  if (negated) index++
  const items: Item[] = []
  while (index < source.length && (source[index] !== ']' || items.length === 0)) {
    if (source.startsWith('[:', index)) {
      const end = source.indexOf(':]', index + 2)
      if (end !== -1) {
        const name = source.slice(index + 2, end)
        const ranges = Object.entries(POSIX).find(([key]) => key === name.replace(/^\^/, ''))?.[1]
        if (ranges === undefined) invalid()
        items.push({ kind: 'ascii', ranges, complement: name.startsWith('^') })
        index = end + 2
        continue
      }
    }
    if (source.startsWith('\\p', index) || source.startsWith('\\P', index)) {
      const name = source[index + 2]
      if (name === undefined || !/^[CLMNPSZclmnpsz]$/.test(name)) invalid()
      items.push({ kind: 'unicode', name: name.toUpperCase(), positive: source[index + 1] === 'p' })
      index += 3
      continue
    }
    if (source[index] === '\\' && /^[dDsSwW]$/.test(source[index + 1] ?? '')) {
      const escaped = source[index + 1]
      const ranges = Object.entries(PERL).find(([key]) => key === escaped.toLowerCase())?.[1]
      if (ranges === undefined) invalid()
      items.push({ kind: 'ascii', ranges, complement: escaped === escaped.toUpperCase() })
      index += 2
      continue
    }
    const [low, next] = classChar(source, index, invalid)
    index = next
    let high = low
    if (source[index] === '-' && index + 1 < source.length && source[index + 1] !== ']') {
      const endpoint = classChar(source, index + 1, invalid)
      high = endpoint[0]
      index = endpoint[1]
      if (high < low) invalid()
    }
    items.push({ kind: 'range', low, high })
  }
  if (index >= source.length) invalid()
  const emitted: string[] = []
  for (const item of items) {
    switch (item.kind) {
      case 'range':
        emitted.push(`\\u{${item.low.toString(16)}}-\\u{${item.high.toString(16)}}`)
        break
      case 'ascii': {
        let next = 0
        for (const [low, high] of item.ranges) {
          if (item.complement) {
            if (next < low) emitted.push(`\\u{${next.toString(16)}}-\\u{${(low - 1).toString(16)}}`)
            next = high + 1
          } else emitted.push(`\\u{${low.toString(16)}}-\\u{${high.toString(16)}}`)
        }
        if (item.complement) emitted.push(`\\u{${next.toString(16)}}-\\u{10ffff}`)
        break
      }
      case 'unicode':
        emitted.push(`\\${item.positive ? 'p' : 'P'}{${item.name}}`)
        break
      default: assertNever(item)
    }
  }
  return ['[' + (negated ? '^' : '') + emitted.join('') + ']', index + 1]
}
