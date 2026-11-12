/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from './errors/DaytonaError.js'
import { badRequest } from './options.js'
import { checkPattern, cleanPath, compilePattern, trimSpace, withoutTrailingGlob } from './dockerignorePattern.js'
import type { ArchiveFilter, ArchiveDecision } from './archiveWalk.js'

export { walkArchiveEntries } from './archiveWalk.js'
export type { ArchiveEntry, ArchiveFilter, ArchiveDecision } from './archiveWalk.js'

/** @internal */
export function readPatterns(text: string): readonly string[] {
  const patterns: string[] = []
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index++) {
    let line = lines[index]
    // bufio.Scanner's limit includes the line terminator.
    if (new TextEncoder().encode(line).length >= 64 * 1024) throw badRequest('dockerignore line exceeds 64 KiB')
    if (index === 0) line = line.replace(/^\uFEFF/, '')
    if (line.startsWith('#')) continue
    line = trimSpace(line)
    if (line === '') continue
    const invert = line.startsWith('!')
    let pattern = invert ? trimSpace(line.slice(1)) : line
    if (pattern !== '') {
      pattern = cleanPath(pattern)
      if (pattern.length > 1 && pattern.startsWith('/')) pattern = pattern.slice(1)
    }
    patterns.push((invert ? '!' : '') + pattern)
  }
  return patterns
}

interface Pattern {
  readonly text: string
  readonly exclusion: boolean
  readonly match: (rel: string) => boolean
}

/** @internal */
export interface Results {
  readonly matched: readonly boolean[]
}

/** @internal */
export interface MatchResult {
  readonly excluded: boolean
  readonly results: Results
}

/** @internal */
export class Matcher {
  private readonly patterns: readonly Pattern[]
  private readonly exclusions: boolean
  private readonly wildcardExceptions: boolean

  constructor(patterns: readonly string[]) {
    this.patterns = patterns.filter((raw) => trimSpace(raw) !== '').map((raw) => {
      const cleaned = cleanPath(trimSpace(raw))
      const exclusion = cleaned.startsWith('!')
      if (cleaned === '!') throw badRequest(`invalid pattern ${JSON.stringify(raw)}: illegal exclusion pattern`)
      const text = exclusion ? cleaned.slice(1) : cleaned
      checkPattern(text, raw)
      try { return { text, exclusion, match: compilePattern(text, raw) } }
      catch (error) {
        if (!(error instanceof BadRequestError)) throw error
        // Pattern compilation errors are raised only if matching evaluates this line.
        return { text, exclusion, match: (): boolean => { throw error } }
      }
    })
    this.exclusions = this.patterns.some((pattern) => pattern.exclusion)
    this.wildcardExceptions = this.patterns.some((pattern) => pattern.exclusion && /[*\[\]?^\\]/.test(withoutTrailingGlob(pattern.text)))
  }

  match(relative: string, parent?: Results): MatchResult {
    const rel = cleanPath(relative)
    const matched = this.patterns.map(() => false)
    const known = parent !== undefined && parent.matched.length === this.patterns.length && this.patterns.length > 0
    let excluded = false
    this.patterns.forEach((pattern, index) => {
      let match = known && parent.matched[index]
      if (!match) {
        if (pattern.exclusion !== excluded) return
        match = pattern.match(rel)
        if (!match && !known) {
          const dirs = rel.split('/').slice(0, -1)
          for (let i = 0; !match && i < dirs.length; i++) match = pattern.match(dirs.slice(0, i + 1).join('/'))
        }
      }
      matched[index] = match
      if (match) excluded = !pattern.exclusion
    })
    return { excluded, results: { matched } }
  }

  excluded(relative: string): boolean {
    const rel = cleanPath(relative)
    if (rel === '.') return false
    let parent: Results | undefined
    let excluded = false
    const parts = rel.split('/')
    for (let i = 0; i < parts.length; i++) {
      const result = this.match(parts.slice(0, i + 1).join('/'), parent)
      parent = result.results
      excluded = result.excluded
    }
    return excluded
  }

  mayReinclude(dir: string): boolean {
    if (!this.exclusions) return false
    if (this.wildcardExceptions) return true
    const prefix = cleanPath(dir) + '/'
    return this.patterns.some((pattern) => pattern.exclusion && (withoutTrailingGlob(pattern.text) + '/').startsWith(prefix))
  }
}

/** @internal */
export class Filter implements ArchiveFilter {
  private readonly always: ReadonlySet<string>
  private readonly alwaysDirs: ReadonlySet<string>

  constructor(private readonly matcher: Matcher, always: readonly string[] = []) {
    this.always = new Set(always.map(cleanPath))
    const dirs = new Set<string>()
    for (const rel of this.always) {
      const parts = rel.split('/')
      for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/'))
    }
    this.alwaysDirs = dirs
  }

  keep(rel: string, isDir: boolean, parent?: Results): ArchiveDecision {
    // Always-kept paths still evaluate patterns, just like the CLI filter.
    const { excluded, results } = this.matcher.match(rel, parent)
    const keep = !excluded || this.always.has(rel)
    return { keep, descend: keep || (isDir && (this.alwaysDirs.has(rel) || this.matcher.mayReinclude(rel))), state: isDir ? results : undefined }
  }
}
