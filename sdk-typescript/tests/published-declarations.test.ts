/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { dirname, join, normalize } from 'node:path'
import ts from 'typescript'

// The declarations the package publishes (emitted with stripInternal, as the
// build does) must stand on their own: no declaration file reachable from the
// package entry points may reach the generated wire types, axios or the
// internal transports, constructors included. Every relative import is
// followed, so an imported declaration counts as much as an exported one; only
// the generated wire files themselves are exempt. ALLOWED holds the files that
// still do so, with their count of offending lines, until their area adopts
// the rule. It only shrinks: a new offender and a stale entry both fail.
const ALLOWED: Readonly<Record<string, number>> = {
  // The toolbox namespaces' declarations (wire types in toolbox signatures).
  'v2/FileSystem.d.ts': 2,
  'v2/Git.d.ts': 5,
  'v2/Interpreter.d.ts': 3,
  'v2/Lsp.d.ts': 11,
  'v2/Process.d.ts': 3,
  'v2/ProcessHandle.d.ts': 2,
  'v2/ProcessRun.d.ts': 3,
  'v2/ProcessWire.d.ts': 7,
  'v2/filesystemTransfers.d.ts': 3,
  // The toolbox aliases in the shared types: TerminalSize, FileMatch, GitCommitResponse.
  'v2/types.d.ts': 4,
  // The shared transport module (reached through the exported OtelApiLike) and
  // the error hierarchy's AxiosError import.
  'v2/http.d.ts': 6,
  // The shared transport's bounded adapter, imported by http.d.ts (its axios types).
  'v2/boundedAdapter.d.ts': 1,
  'v2/errors/DaytonaError.d.ts': 1,
}
// The contract's vocabularies (`gen/*-enums.js`) are value lists, not wire types.
const FORBIDDEN = [/from '\.\.?\/(?:v2\/)?gen\/(?![a-z]+-enums\.js')/, /from 'axios'/, /\bHttpTransport\b/, /\bAxiosInstance\b/, /\b(?:api|toolbox)\.[A-Z]/]

function emittedDeclarations(): { readonly root: string; readonly files: Map<string, string> } {
  const root = join(__dirname, '..')
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const entries = [join(root, 'src', 'index.ts'), join(root, 'src', 'v2', 'index.ts')]
  const outDir = join(root, '.declarations-check')
  const program = ts.createProgram(entries, {
    ...parsed.options, noEmit: false, declaration: true, emitDeclarationOnly: true, stripInternal: true, outDir,
  })
  const files = new Map<string, string>()
  program.emit(undefined, (fileName, text) => { files.set(normalize(fileName), text) })
  return { root: outDir, files }
}

function reachable(root: string, files: Map<string, string>): string[] {
  const queue = [join(root, 'index.d.ts'), join(root, 'v2', 'index.d.ts')].map(normalize)
  const seen = new Set<string>()
  while (queue.length > 0) {
    const file = queue.pop() as string
    if (seen.has(file) || !files.has(file)) continue
    seen.add(file)
    for (const match of (files.get(file) ?? '').matchAll(/from '(\.\.?\/[^']+)'/g)) {
      queue.push(normalize(join(dirname(file), (match[1] ?? '').replace(/\.js$/, '.d.ts'))))
    }
  }
  return [...seen].sort()
}

it('keeps generated wire types, axios and transports out of every published declaration', () => {
  const { root, files } = emittedDeclarations()
  const found: Record<string, number> = {}
  const reached = reachable(root, files)
  expect(reached.length).toBeGreaterThan(20)
  for (const file of reached) {
    const relative = file.slice(root.length + 1).replace(/\\/g, '/')
    if (relative.startsWith('v2/gen/')) continue
    const offending = (files.get(file) ?? '').split('\n').filter((line) => FORBIDDEN.some((pattern) => pattern.test(line))).length
    if (offending > 0) found[relative] = offending
  }
  expect(found).toEqual(ALLOWED)
}, 120_000)
