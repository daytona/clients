/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path'
import ts from 'typescript'

// The declarations the package publishes (emitted with stripInternal, as the
// build does) must stand on their own: no handle, model or service class of
// the client, lifecycle and platform-service area may reach the generated
// wire types, axios or the internal transports, constructors included. A new
// reference fails this test.
const AREA = ['Daytona', 'Sandbox', 'Image', 'Images', 'Snapshots', 'Volume', 'Secret', 'Reservations', 'MacModels', 'Ports', 'Docker', 'SandboxVolumes']
const FORBIDDEN = [/from '\.\/gen\//, /from 'axios'/, /\bHttpTransport\b/, /\bAxiosInstance\b/, /\bapi\.[A-Z]/]

function emittedDeclarations(): Map<string, string> {
  const root = join(__dirname, '..')
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const entries = [join(root, 'src', 'index.ts'), join(root, 'src', 'v2', 'index.ts')]
  const program = ts.createProgram(entries, {
    ...parsed.options, noEmit: false, declaration: true, emitDeclarationOnly: true, stripInternal: true, outDir: join(root, '.declarations-check'),
  })
  const out = new Map<string, string>()
  program.emit(undefined, (fileName, text) => { out.set(fileName.replace(/\\/g, '/'), text) })
  return out
}

it('keeps generated wire types, axios and transports out of the published area declarations', () => {
  const declarations = emittedDeclarations()
  const offenders: string[] = []
  for (const name of AREA) {
    const entry = [...declarations.entries()].find(([file]) => file.endsWith(`/v2/${name}.d.ts`))
    expect(entry).toBeDefined()
    const [, text] = entry ?? ['', '']
    for (const pattern of FORBIDDEN) {
      for (const line of text.split('\n')) if (pattern.test(line)) offenders.push(`${name}.d.ts: ${line.trim()}`)
    }
  }
  expect(offenders).toEqual([])
}, 120_000)
