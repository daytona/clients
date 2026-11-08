/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Unit tests for scripts/api-surface.mjs (run by `npm test`'s pretest).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import ts from 'typescript'
import { printSignature, printType } from './api-surface.mjs'

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
