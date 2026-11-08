/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The published declarations (what `npm run build` emits, with
// `stripInternal`) expose no transport or wire type on a public class: no
// `HttpTransport`, `ComputerUseTransport`, axios type or other internal
// construction input, and no generated `api.` / `toolbox.` type. Classes the
// SDK builds itself declare `constructor(token: Internal, ...init: never[])`
// publicly and keep their real signature `@internal`. ALLOWED lists the
// classes that still expose one; an entry that no longer does fails too, so
// the list only shrinks.

import { join } from 'node:path'
import ts from 'typescript'

// Client, sandbox lifecycle and platform services, settled with their area.
const ALLOWED: ReadonlySet<string> = new Set([
  'Daytona', 'DockerService', 'Image', 'ImageBuilder', 'ImagesService', 'MacModelsService', 'PortsService',
  'ReservationsService', 'Sandbox', 'SandboxVolumesService', 'SecretService', 'Snapshot', 'SnapshotsService',
  'VolumeService',
])

const INTERNAL_TYPES = /\b(?:HttpTransport|HttpTransportConfig|HttpClientOptions|ComputerUseTransport|ProcessBinding|ContextExecutionQueue|Axios[A-Za-z]*|toolbox\.|api\.)/

function emittedDeclarations(): Map<string, string> {
  const root = join(__dirname, '..')
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const program = ts.createProgram(parsed.fileNames, {
    ...parsed.options,
    declaration: true,
    emitDeclarationOnly: true,
    noEmit: false,
    outDir: join(root, 'declarations-check'),
  })
  const files = new Map<string, string>()
  program.emit(undefined, (fileName, text) => {
    if (fileName.endsWith('.d.ts')) files.set(fileName, text)
  })
  return files
}

/** `Class: member` for every public class member line that names an internal type. */
function violations(files: Map<string, string>): Map<string, string[]> {
  const found = new Map<string, string[]>()
  for (const [fileName, text] of files) {
    // The transport module itself is internal (no package entry exports it).
    if (!fileName.includes(join('declarations-check', 'v2')) || fileName.includes(join('v2', 'gen')) || fileName.endsWith(join('v2', 'http.d.ts'))) continue
    for (const match of text.matchAll(/export declare class (\w+)[^{]*\{([\s\S]*?)\n\}/g)) {
      const [, name, body] = match as unknown as [string, string, string]
      const lines = body.split('\n').map((line) => line.trim())
        .filter((line) => !/^(private |#|\*|\/)/.test(line) && INTERNAL_TYPES.test(line))
      if (lines.length > 0) found.set(name, lines)
    }
  }
  return found
}

it('exposes no internal construction or wire type on a public class declaration', () => {
  const found = violations(emittedDeclarations())
  const unexpected = [...found].filter(([name]) => !ALLOWED.has(name))
  expect(unexpected).toEqual([])
  const stale = [...ALLOWED].filter((name) => !found.has(name))
  expect(stale).toEqual([])
}, 120_000)
