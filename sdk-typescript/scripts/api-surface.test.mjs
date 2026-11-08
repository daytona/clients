/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Unit tests for scripts/api-surface.mjs (run by `npm test`'s pretest).
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import ts from 'typescript'
import { generate, printSignature, printType } from './api-surface.mjs'

function checkerFor(source) {
  const host = ts.createCompilerHost({})
  const original = host.getSourceFile
  host.getSourceFile = (file, version) => (file === 'probe.ts' ? ts.createSourceFile(file, source, version) : original(file, version))
  const program = ts.createProgram(['probe.ts'], { strict: true, noEmit: true }, host)
  return { checker: program.getTypeChecker(), file: program.getSourceFile('probe.ts') }
}

test('unions print sorted at every depth, including inside constructs with their own scope', () => {
  const { checker, file } = checkerFor(`
    export declare const value: {
      m(cb: (event: 'b' | 'a') => 'y' | 'x'): void
      ctor: new <T extends 'q' | 'p'>(z: 'd' | 'c') => Map<string, (n: 2 | 1) => void>
      maybe?: 'z' | null | 'w'
    }
  `)
  const symbol = checker.getSymbolAtLocation(file.statements[0].declarationList.declarations[0].name)
  const type = checker.getTypeOfSymbolAtLocation(symbol, file)
  const text = printType(checker, type, file)
  assert.match(text, /m\(cb: \(event: 'a' \| 'b'\) => 'x' \| 'y'\): void/)
  assert.match(text, /new <T extends 'p' \| 'q'>\(z: 'c' \| 'd'\) => Map<string, \(n: 1 \| 2\) => void>/)
  assert.match(text, /maybe\?: 'w' \| 'z' \| null/)
  const method = checker.getPropertyOfType(type, 'm')
  const [signature] = checker.getSignaturesOfType(checker.getTypeOfSymbolAtLocation(method, file), ts.SignatureKind.Call)
  assert.equal(printSignature(checker, signature, file), "(cb: (event: 'a' | 'b') => 'x' | 'y'): void")
})

function generateFixture(source) {
  const dir = mkdtempSync(join(tmpdir(), 'api-surface-'))
  try {
    mkdirSync(join(dir, 'src', 'v2'), { recursive: true })
    writeFileSync(join(dir, 'tsconfig.json'), JSON.stringify({ compilerOptions: { strict: true, target: 'ES2021', module: 'ESNext', moduleResolution: 'Bundler' } }))
    writeFileSync(join(dir, 'src', 'index.ts'), 'export const VERSION = 1\n')
    writeFileSync(join(dir, 'src', 'v2', 'index.ts'), source)
    return generate(dir)['api-surface/v2.api.txt'].split('\n')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

test('callback directions: arguments are handed out, return values are taken in', () => {
  const v2 = generateFixture(`
    export interface Ev1 { name: string }
    export interface Ev2 { name: string }
    export interface Ev3 { name: string }
    export interface Ev4 { name: string }
    export interface Ev5 { name: string }
    export interface Result { ok: boolean }
    export interface Hooks { onEvent(e: Ev1): Result }
    export interface Handler { (e: Ev2): void }
    export interface Opts { onEvent?: (e: Ev4) => void; after?: Ev4 }
    export declare class Handle { subscribe(cb: { handle(e: Ev5): void }): void }
    export declare function f1(h: Hooks, after?: Ev1): void
    export declare function f2(h: Handler, after?: Ev2): void
    export declare function f3(h: (e: Ev3) => void, after?: Ev3): void
    export declare function f4(o: Opts): void
    export declare function f5(after?: Ev5): Handle
  `)
  // Each event is both handed to a callback and passed in: never passed-in only.
  for (const name of ['Ev1', 'Ev2', 'Ev3', 'Ev4', 'Ev5']) {
    assert.ok(!v2.includes(`${name}: passed-in`), v2.join('\n'))
    assert.ok(v2.includes(`${name}: returned also-passed-in`), v2.join('\n'))
  }
  // A method callback's return value is the caller's: passed in.
  assert.ok(v2.includes('Result: passed-in'), v2.join('\n'))
  assert.ok(v2.includes('Hooks: passed-in'), v2.join('\n'))
})

test('a method of a type the SDK hands out takes its arguments from the caller', () => {
  const v2 = generateFixture(`
    export interface Query { q: string }
    export interface Rows { n: number }
    export interface Db { run(query: Query): Rows }
    export declare function open(): Db
  `)
  assert.ok(v2.includes('Query: passed-in'), v2.join('\n'))
  assert.ok(!v2.includes('Rows: passed-in'), v2.join('\n'))
})

// TypeScript lists a type the SDK hands to the caller (a return value, a class
// property, a callback argument) as returned, never as passed-in only: its
// members stay required for the readers. (Only a base interface of a passed-in
// type is listed `passed-in also-returned`.)
test('a type both passed in and handed to a callback is not passed-in only', () => {
  const dir = mkdtempSync(join(tmpdir(), 'api-surface-'))
  try {
    mkdirSync(join(dir, 'src', 'v2'), { recursive: true })
    writeFileSync(join(dir, 'tsconfig.json'), JSON.stringify({ compilerOptions: { strict: true, target: 'ES2021', module: 'ESNext', moduleResolution: 'Bundler' } }))
    writeFileSync(join(dir, 'src', 'index.ts'), 'export const VERSION = 1\n')
    writeFileSync(
      join(dir, 'src', 'v2', 'index.ts'),
      [
        'export interface Event { name: string }',
        'export interface WatchOptions { after?: Event }',
        'export interface Filter { name: string }',
        'export declare function watch(onEvent: (event: Event) => void, options?: WatchOptions): void',
        'export declare function find(filter: Filter): void',
        '',
      ].join('\n'),
    )
    const v2 = generate(dir)['api-surface/v2.api.txt'].split('\n')
    assert.ok(!v2.some((line) => line.startsWith('Event: passed-in')), v2.join('\n'))
    assert.ok(v2.includes('WatchOptions: passed-in'), v2.join('\n'))
    assert.ok(v2.includes('Filter: passed-in'), v2.join('\n'))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('a recursive callback interface the caller passes in stays passed in', () => {
  const v2 = generateFixture(`
    export interface Ev { name: string }
    export interface Handler { onEvent(e: Ev): Handler | undefined }
    export declare function listen(h: Handler): void
  `)
  assert.ok(v2.includes('Handler: passed-in'), v2.join('\n'))
  assert.ok(!v2.includes('Handler: returned also-passed-in'), v2.join('\n'))
})

test('passed-in forms: a type parameter constraint, a function value, a settable class property', () => {
  const v2 = generateFixture(`
    export interface GOpts { a: string }
    export interface COpts { a: string }
    export interface SOpts { a: string }
    export declare function configure<T extends GOpts>(o: T): void
    export const make = (o: COpts): number => o.a.length
    export declare class Client { config: SOpts }
    export declare function client(): Client
  `)
  assert.ok(v2.includes('GOpts: passed-in'), v2.join('\n'))
  assert.ok(v2.includes('COpts: passed-in'), v2.join('\n'))
  // Callers read it and may assign it: both ways.
  assert.ok(v2.includes('SOpts: returned also-passed-in'), v2.join('\n'))
})

test('a call signature the caller implements gaining an overload is reported, end to end', () => {
  const base = generateFixture(`
    export interface ProgressFn { (bytes: number): void }
    export interface ProgressSink { report(bytes: number): void }
    export declare function track(fn: ProgressFn, sink: ProgressSink): void
  `)
  const head = generateFixture(`
    export interface ProgressFn { (bytes: number): void; (event: { bytes: number; total: number }): void }
    export interface ProgressSink { report(bytes: number): void; report(event: { bytes: number; total: number }): void }
    export declare function track(fn: ProgressFn, sink: ProgressSink): void
  `)
  const checker = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'breaking', 'breaking_changes.py')
  const driver = [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(dirname(checker))})`,
    'import breaking_changes as bc',
    'old, new = sys.stdin.read().split("\\0")',
    'r = bc.Report()',
    'bc.compare_surface("sdk-typescript/api-surface/v2.api.txt", old, new, r)',
    'print(len(r.breaking))',
  ].join('\n')
  const found = spawnSync('python3', ['-c', driver], { input: `${base.join('\n')}\0${head.join('\n')}`, encoding: 'utf8' })
  assert.equal(found.status, 0, found.stderr)
  assert.equal(found.stdout.trim(), '2', `${base.join('\n')}\n---\n${head.join('\n')}`)
})
