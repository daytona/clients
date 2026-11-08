/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Unit tests for scripts/api-surface.mjs (run by `npm test`'s pretest).
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
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
