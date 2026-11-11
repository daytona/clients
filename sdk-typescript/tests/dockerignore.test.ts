/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { mkdtemp, mkdir, writeFile, rm, symlink, unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, isAbsolute } from 'node:path'
import cases from '../../cli/internal/dockerignore/testdata/buildkit_cases.json'
import edgeCases from '../../sdk-python/tests/fixtures/dockerignore-edge-cases.json'
import refusedCases from '../../sdk-python/tests/fixtures/dockerignore-refused.json'
import { Filter, Matcher, readPatterns, walkArchiveEntries } from '../src/v2/dockerignore.js'
import { BadRequestError, NotSupportedError } from '../src/v2/errors/DaytonaError.js'
import { contextUploadRuntime } from '../src/v2/contextUpload.js'

test.each(refusedCases)('refuses what Go refuses: $ignore', ({ ignore, error }) => {
  // Given an ignore file Go's filepath.Match validation refuses (shared with the
  // Go and Python suites), then building the matcher refuses it too.
  expect(() => new Matcher(readPatterns(ignore))).toThrow(new BadRequestError(error, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' }))
})

test.each(edgeCases)('matches Go semantics when edge pattern $ignore', ({ ignore, paths, excluded }) => {
  // Given valid Go patterns, including byte-sliced BOM suffixes and literal sets.
  const started = performance.now()
  const matcher = new Matcher(readPatterns(ignore))
  // When evaluating each path.
  const actual = paths.map((path) => matcher.excluded(path))
  const elapsed = performance.now() - started
  // Then the shared Go expectations decide membership.
  expect(actual).toEqual(excluded)
  expect(elapsed).toBeLessThan(1000)
})

describe('BuildKit dockerignore cases', () => {
  test.each(cases.map((fixture, index) => ({ ...fixture, index })))('matches shared case $index', async (fixture) => {
    // Given the shared CLI tree and ignore file.
    const root = await mkdtemp(join(tmpdir(), 'daytona-ignore-'))
    try {
      for (const file of fixture.files) {
        const current = join(root, file)
        if (file.endsWith('/')) await mkdir(current, { recursive: true })
        else {
          await mkdir(dirname(current), { recursive: true })
          await writeFile(current, 'fixture')
        }
      }
      // When the matcher walks the tree.
      const action = async (): Promise<readonly string[]> => {
        const filter = new Filter(new Matcher(readPatterns(fixture.ignore)))
        const entries = await walkArchiveEntries(root, filter)
        return entries.map((entry) => entry.rel + (entry.info.isDirectory() ? '/' : '')).sort()
      }
      // Then the archive agrees with BuildKit, including delayed failures.
      if (fixture.fails) await expect(action()).rejects.toBeInstanceOf(BadRequestError)
      else await expect(action()).resolves.toEqual(fixture.uploaded)
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
})

test('preserves later BOMs while normalizing ignore-file paths', () => {
  // Given a BOM, comments and unnormalized paths.
  const text = '\uFEFF# comment\n\n  /secret.txt  \n! /keep/./me\n./a//b/\n\uFEFF*.log\n'
  // When parsing the file.
  const patterns = readPatterns(text)
  // Then only the first BOM is stripped.
  expect(patterns).toEqual(['secret.txt', '!keep/me', 'a/b', '\uFEFF*.log'])
})

test('rejects scanner lines at the 64 KiB limit', () => {
  // Given a line too large for Go's scanner.
  const text = 'é'.repeat(32 * 1024)
  // When parsing, then reject it using the SDK error hierarchy.
  expect(() => readPatterns(text)).toThrow(BadRequestError)
})

test('accepts a long literal pattern below the scanner limit', () => {
  // Given a valid literal too long for V8 to compile as a regular expression.
  const pattern = 'a'.repeat(40000)
  // When constructing and evaluating the matcher.
  const matcher = new Matcher(readPatterns(pattern))
  // Then exact matching works without a raw regex exception.
  expect([matcher.excluded('different'), matcher.excluded(pattern)]).toEqual([false, true])
})

test.each([
  { pattern: '**foo', path: 'x/barfoo', excluded: true },
  { pattern: '*.log^', path: 'app.log^', excluded: false },
  { pattern: ']', path: ']', excluded: true },
  { pattern: 'a?', path: 'a😀', excluded: true },
  { pattern: 'a\\a', path: 'a\u0007', excluded: true },
  { pattern: 'a\\s', path: 'a\u00a0', excluded: false },
  { pattern: 'a?b', path: 'a\nb', excluded: true },
  { pattern: 'a/**/b', path: 'a/x\ry/b', excluded: true },
  { pattern: 'a*b', path: 'axb\n', excluded: false },
  { pattern: 'a\\Qfoo\\E', path: 'afoo', excluded: true },
  { pattern: 'a\\Q.\\E', path: 'a\\.', excluded: true },
  { pattern: 'a\\Qfoo', path: 'afoo$', excluded: true },
  { pattern: '\\Qfoo\\E*', path: 'foo-tail', excluded: true },
  { pattern: '\\141', path: 'a', excluded: true },
  { pattern: '\\12', path: '\n', excluded: true },
  { pattern: '\\400', path: 'Ā', excluded: true },
  { pattern: '\\777', path: 'ǿ', excluded: true },
  { pattern: '\\1234', path: 'S4', excluded: true },
  { pattern: '\\0', path: '\u0000', excluded: true },
  { pattern: '\\x41', path: 'A', excluded: true },
  { pattern: '\\pL', path: 'λ', excluded: true },
  { pattern: '\\PL', path: '1', excluded: true },
  { pattern: '[\\pL]', path: 'λ', excluded: true },
  { pattern: '[\\141]', path: 'a', excluded: true },
  { pattern: '\\pC', path: '\u0378', excluded: true },
  { pattern: '\\PC', path: '\u0378', excluded: false },
  { pattern: '[\\pC]', path: '\u0378', excluded: true },
  { pattern: '[[]', path: '[', excluded: true },
  { pattern: '[a[]', path: '[', excluded: true },
])('matches Go pattern $pattern against $path', ({ pattern, path, excluded }) => {
  // Given a CLI matcher pattern.
  const matcher = new Matcher([pattern])
  // When evaluating the path.
  const result = matcher.excluded(path)
  // Then character and anchor semantics match Go's engine.
  expect(result).toBe(excluded)
})

test.each(['\\x4\\101', 'a\\x4', '\\u0041', '\\1', '\\8', '\\x{41}', '\\p{Greek}', '\\pG', '[\\b]', '\\E', '[[:bogus:]]'])('rejects RE2-invalid escape %s only when evaluated', (pattern) => {
  // Given a source escape which RE2 rejects, even if JavaScript would accept it.
  const matcher = new Matcher([pattern])
  // When evaluating the pattern, then report the delayed SDK pattern error.
  expect(() => matcher.excluded('A')).toThrow(BadRequestError)
})

test.each(['[[::]', '[[:foo:]]', 'ax[[::]', '[a*[]', 'x[ -\\pL\\pL]*', '[z\\101-\\pL]x'])('rejects malformed class %s only when evaluated', (pattern) => {
  // Given a class accepted by filepath.Match but refused by Go's regexp compiler.
  const matcher = new Matcher([pattern])
  // When evaluating it, then report the delayed SDK pattern error.
  expect(() => matcher.excluded('x')).toThrow(BadRequestError)
})

describe.each([
  { name: 'alnum', ascii: /^[0-9A-Za-z]$/ },
  { name: 'alpha', ascii: /^[A-Za-z]$/ },
  { name: 'ascii', ascii: /^[\x00-\x7f]$/ },
  { name: 'blank', ascii: /^[\t ]$/ },
  { name: 'cntrl', ascii: /^[\x00-\x1f\x7f]$/ },
  { name: 'digit', ascii: /^[0-9]$/ },
  { name: 'graph', ascii: /^[\x21-\x7e]$/ },
  { name: 'lower', ascii: /^[a-z]$/ },
  { name: 'print', ascii: /^[\x20-\x7e]$/ },
  { name: 'punct', ascii: /^[!-/:-@[-`{-~]$/ },
  { name: 'space', ascii: /^[\t\n\v\f\r ]$/ },
  { name: 'upper', ascii: /^[A-Z]$/ },
  { name: 'word', ascii: /^[0-9A-Z_a-z]$/ },
  { name: 'xdigit', ascii: /^[0-9A-Fa-f]$/ },
])('RE2 POSIX class $name', ({ name, ascii }) => {
  test.each([false, true])('matches the ASCII table with complement=%s', (complement) => {
    // Given every ASCII character and a non-ASCII letter, wrapped as normal paths.
    const chars = [...Array.from({ length: 128 }, (_, index) => String.fromCodePoint(index)), 'λ']
    const matcher = new Matcher([`x[[:${complement ? '^' : ''}${name}:]]y`])
    // When matching the POSIX class or its complement.
    const actual = chars.map((char) => matcher.excluded(`x${char}y`))
    // Then the entire ASCII table and Unicode complement agree with RE2.
    expect(actual).toEqual(chars.map((char) => ascii.test(char) !== complement))
  })
})

test('unions POSIX classes within a single bracket expression', () => {
  // Given a class containing two POSIX named classes.
  const matcher = new Matcher(['[[:alpha:][:digit:]]'])
  // When matching letters, digits, punctuation and a non-ASCII digit.
  const actual = ['A', '7', '!', '٧'].map((path) => matcher.excluded(path))
  // Then the union retains ASCII-only named-class semantics.
  expect(actual).toEqual([true, true, false, false])
})

test('defers a compilation error until an excluded path evaluates its exception', () => {
  // Given an invalid exception that is skipped for included paths.
  const matcher = new Matcher(readPatterns('a\n!foo\\q\n'))
  // When matching a path that remains included.
  const excluded = matcher.excluded('b')
  // Then the unused exception is harmless.
  expect(excluded).toBe(false)
})

test('raises a deferred compilation error when its exception is evaluated', () => {
  // Given an invalid exception following an exclusion.
  const matcher = new Matcher(readPatterns('a\n!foo\\q\n'))
  // When matching the excluded path, then evaluate and reject the exception.
  expect(() => matcher.excluded('a')).toThrow(BadRequestError)
})

test.each(['!', '[a-', 'x*/['])('rejects invalid pattern %s at its appropriate boundary', (pattern) => {
  // Given malformed input.
  const action = (): boolean => new Matcher([pattern]).excluded('x')
  // When constructing or evaluating it, then reject it.
  expect(action).toThrow(BadRequestError)
})

test.each(['x[a-😀-|]', 'x[𐐀-😀-a]', 'x[\\9-😀-|]'])('rejects malformed astral range %s at initialization', (pattern) => {
  // Given a malformed range after a code point represented by two UTF-16 units.
  // When constructing the matcher, then filepath.Match syntax fails immediately.
  expect(() => new Matcher([pattern])).toThrow(BadRequestError)
})

test('keeps forced paths beneath ignored parents in archive order', async () => {
  // Given ignored directories with one forced nested Dockerfile.
  const root = await mkdtemp(join(tmpdir(), 'daytona-forced-'))
  try {
    await mkdir(join(root, 'nested', 'empty'), { recursive: true })
    await writeFile(join(root, 'nested', 'Dockerfile'), 'FROM scratch')
    await writeFile(join(root, 'nested', 'secret'), 'excluded')
    const filter = new Filter(new Matcher(['*']), ['nested/Dockerfile'])
    // When walking with that forced path.
    const entries = await walkArchiveEntries(root, filter)
    // Then the parent precedes the kept child and other ignored entries stay out.
    expect(entries.map((entry) => entry.rel)).toEqual(['nested', 'nested/Dockerfile'])
    expect(entries.every((entry) => isAbsolute(entry.current))).toBe(true)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('does not follow symbolic links during the walk', async () => {
  // Given a link to a directory within a context.
  const root = await mkdtemp(join(tmpdir(), 'daytona-link-'))
  try {
    await mkdir(join(root, 'target'))
    await writeFile(join(root, 'target', 'file'), 'content')
    await symlink('target', join(root, 'link'))
    // When walking the context.
    const entries = await walkArchiveEntries(root)
    // Then the link itself, not its children, is reported.
    expect(entries.map((entry) => [entry.rel, entry.info.isSymbolicLink()])).toEqual([
      ['link', true], ['target', false], ['target/file', false],
    ])
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('rejects filesystem traversal outside Node', async () => {
  // Given a browser runtime.
  const runtime = jest.spyOn(contextUploadRuntime, 'isNode').mockReturnValue(false)
  try {
    // When walking, then raise the supported-runtime error before importing builtins.
    await expect(walkArchiveEntries('.')).rejects.toBeInstanceOf(NotSupportedError)
  } finally { runtime.mockRestore() }
})

test('skips an ignored entry when it vanishes after listing', async () => {
  // Given: a listed file removed while an earlier kept entry is visited.
  const root = await mkdtemp(join(tmpdir(), 'daytona-ignore-race-'))
  try {
    await writeFile(join(root, 'a'), 'kept')
    await writeFile(join(root, 'z'), 'ignored')
    const filter = new Filter(new Matcher(['z']))
    const visited: string[] = []
    // When: the walker reaches the removed ignored entry.
    await walkArchiveEntries(root, filter, async (entry) => {
      visited.push(entry.rel)
      await unlink(join(root, 'z'))
    })
    // Then: no metadata probe of that entry aborts the walk.
    expect(visited).toEqual(['a'])
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('does not retain visited entries when a callback consumes the walk', async () => {
  // Given: an ignored parent forced into the archive by a kept child.
  const root = await mkdtemp(join(tmpdir(), 'daytona-walk-stream-'))
  try {
    await mkdir(join(root, 'nested'))
    await writeFile(join(root, 'nested', 'Dockerfile'), 'FROM scratch')
    const filter = new Filter(new Matcher(['*']), ['nested/Dockerfile'])
    const visited: string[] = []
    // When: the callback consumes each kept entry in archive order.
    const entries = await walkArchiveEntries(root, filter, async (entry) => { visited.push(entry.rel) })
    // Then: no duplicate collection is retained after callback delivery.
    expect({ entries, visited }).toEqual({ entries: [], visited: ['nested', 'nested/Dockerfile'] })
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('walks a directory when readdir returns Buffers instead of Dirents (Bun)', async () => {
  // Given a runtime whose readdir ignores withFileTypes with a buffer encoding,
  // as Bun does: every entry arrives as a plain Buffer of unknown type.
  const root = await mkdtemp(join(tmpdir(), 'walk-bun-'))
  try {
    await mkdir(join(root, 'sub'))
    await writeFile(join(root, 'sub', 'a'), 'a')
    await writeFile(join(root, 'b'), 'b')
    const fs = await import('node:fs/promises')
    const readdir = fs.default.readdir
    // Plain Uint8Arrays (not Buffers), as some runtime versions return.
    const spy = jest.spyOn(fs.default, 'readdir').mockImplementation((async (path: string) => (await readdir(path, { encoding: 'buffer' })).map((name) => new Uint8Array(name))) as never)
    try {
      // When walked, then lstat decides each type and the order is unchanged.
      const entries = await walkArchiveEntries(root)
      expect(entries.map((entry) => entry.rel)).toEqual(['b', 'sub', 'sub/a'])
    } finally { spy.mockRestore() }
  } finally { await rm(root, { recursive: true, force: true }) }
})
