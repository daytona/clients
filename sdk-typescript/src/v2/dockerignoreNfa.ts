/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertNever } from './builderText.js'

interface Token {
  readonly kind: 'atom' | 'star' | 'directory'
  readonly expression: RegExp
}

/** One atom in already validated translated source (never a repetition). */
function atomEnd(source: string, start: number): number {
  let end = start + 1
  if (source[start] === '[') {
    if (source[end] === '^') end++
    while (source[end] !== ']') end += source[end] === '\\' ? 2 : 1
    return end + 1
  }
  if (source[start] === '\\') {
    if (source[end] === 'x') return end + 3
    if ('upP'.includes(source[end]) && source[end + 1] === '{') return source.indexOf('}', end + 2) + 1
    return end + 1
  }
  if (source.startsWith('(?![\\s\\S])', start)) return start + '(?![\\s\\S])'.length
  // Iterating a string retains Unicode code points, including an unpaired surrogate.
  for (const char of source.slice(start, start + 2)) return start + char.length
  return end
}

/** Match the translated regexp subset by DP, never a backtracking path regex. @internal */
export function compileProgram(source: string): (path: string) => boolean {
  const tokens: Token[] = []
  for (let index = 0; index < source.length;) {
    if (source.startsWith('([^\\n]*/)?', index)) {
      tokens.push({ kind: 'directory', expression: /./uy })
      index += '([^\\n]*/)?'.length
      continue
    }
    const end = atomEnd(source, index)
    const repeated = source[end] === '*'
    tokens.push({ kind: repeated ? 'star' : 'atom', expression: new RegExp(source.slice(index, end), 'uy') })
    index = end + Number(repeated)
  }
  return (path) => {
    const chars = Array.from(path)
    const offsets = [0]
    for (const char of chars) offsets.push(offsets[offsets.length - 1] + char.length)
    let reachable = [true, ...chars.map(() => false)]
    // Two DP rows: O(path length) memory, O(path length × program size) time.
    // Atom regexes consume at most one code point or assert at a fixed offset.
    for (const token of tokens) {
      const following = chars.map(() => false)
      following.push(false)
      switch (token.kind) {
        case 'atom':
          for (let offset = 0; offset < reachable.length; offset++) {
            if (!reachable[offset]) continue
            token.expression.lastIndex = offsets[offset]
            const result = token.expression.exec(path)
            if (result !== null) following[offset + Number(result[0] !== '')] = true
          }
          break
        case 'star':
          following[0] = reachable[0]
          for (let offset = 1; offset < reachable.length; offset++) {
            token.expression.lastIndex = 0
            following[offset] = reachable[offset] || (following[offset - 1] && token.expression.test(chars[offset - 1]))
          }
          break
        case 'directory': {
          following[0] = reachable[0]
          let running = false
          for (let offset = 1; offset < reachable.length; offset++) {
            const char = chars[offset - 1]
            running = (running || reachable[offset - 1]) && char !== '\n'
            following[offset] = reachable[offset] || (running && char === '/')
          }
          break
        }
        default: assertNever(token.kind)
      }
      reachable = following
    }
    // An unterminated \\Q can quote the generated end anchor: retain prefix matching.
    return reachable.some(Boolean)
  }
}
