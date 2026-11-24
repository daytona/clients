/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { dirname, join, normalize } from 'node:path'
import ts from 'typescript'

// A Node-only consumer (lib es2022, no DOM, the Node types, skipLibCheck:
// false) compiles the published declarations: none may name a type only the
// DOM lib declares (AddEventListenerOptions, BodyInit, ...). The tarball smoke
// checks the packed package the same way (scripts/cjs-types-fixture,
// tsconfig.strict.node-only.json); this catches it before a build. The one
// diagnostic expected here is the root's `@daytona/sdk/v0` self-reference,
// which resolves only in a built package.
function expected(diagnostic: ts.Diagnostic): boolean {
  return diagnostic.code === 2307 && ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n').includes("'@daytona/sdk/v0'")
}

function emittedDeclarations(): { readonly outDir: string; readonly files: Map<string, string> } {
  const root = join(__dirname, '..')
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const outDir = join(root, '.node-declarations-check')
  const program = ts.createProgram([join(root, 'src', 'index.ts'), join(root, 'src', 'v2', 'index.ts')], {
    ...parsed.options, noEmit: false, declaration: true, emitDeclarationOnly: true, stripInternal: true, outDir,
  })
  const files = new Map<string, string>()
  program.emit(undefined, (fileName, text) => { files.set(normalize(fileName), text) })
  return { outDir, files }
}

it('compiles the published declarations without the DOM lib', () => {
  const { outDir, files } = emittedDeclarations()
  const options: ts.CompilerOptions = {
    module: ts.ModuleKind.NodeNext,
    moduleResolution: ts.ModuleResolutionKind.NodeNext,
    target: ts.ScriptTarget.ES2022,
    lib: ['lib.es2022.d.ts'],
    types: ['node'],
    strict: true,
    noEmit: true,
    skipLibCheck: false,
  }
  const host = ts.createCompilerHost(options)
  const virtual = (fileName: string): string | undefined => files.get(normalize(fileName))
  host.fileExists = (fileName) => virtual(fileName) !== undefined || ts.sys.fileExists(fileName)
  host.readFile = (fileName) => virtual(fileName) ?? ts.sys.readFile(fileName)
  host.directoryExists = (name) => normalize(name).startsWith(outDir) || ts.sys.directoryExists(name)
  const getSourceFile = host.getSourceFile.bind(host)
  host.getSourceFile = (fileName, languageVersion, onError, shouldCreate) => {
    const text = virtual(fileName)
    return text === undefined ? getSourceFile(fileName, languageVersion, onError, shouldCreate) : ts.createSourceFile(fileName, text, languageVersion, true)
  }
  const program = ts.createProgram([join(outDir, 'index.d.ts'), join(outDir, 'v2', 'index.d.ts')], options, host)
  expect(program.getSourceFiles().filter((file) => normalize(file.fileName).startsWith(outDir)).length).toBeGreaterThan(20)
  const errors = ts.getPreEmitDiagnostics(program)
    .filter((diagnostic) => !expected(diagnostic))
    .map((diagnostic) => `${diagnostic.file ? diagnostic.file.fileName.slice(dirname(outDir).length + 1) : ''}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')}`)
  expect(errors).toEqual([])
}, 120_000)
