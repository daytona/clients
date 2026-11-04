/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync } from 'node:fs';
import { join } from 'node:path';
import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const sdk = "import { Daytona } from '@daytona/sdk';\n";
const cases = [
  { name: 'seeded other with conditional Daytona set', linked: true,
    setup: "const m = new Map([['d', other]]); if (condition) m.set('d', new Daytona());" },
  { name: 'other set before conditional Daytona set', linked: true,
    setup: "const m = new Map(); m.set('d', other); if (condition) m.set('d', new Daytona());" },
  { name: 'only Daytona set', linked: true,
    setup: "const m = new Map(); m.set('d', new Daytona());" },
  { name: 'no Daytona set', linked: false,
    setup: "const m = new Map([['d', other]]); m.set('d', other);" },
  { name: 'Daytona set at another key', linked: false,
    setup: "const m = new Map([['d', other]]); m.set('other', new Daytona());" },
] as const;
const reads = [
  { name: 'direct', expression: "m.get('d')" },
  { name: 'alias', expression: "selected", setup: "const selected = m.get('d');" },
  { name: 'factory return', expression: 'select()', setup: "function select() { return m.get('d'); }" },
] as const;

it.each(cases.flatMap(testCase => reads.map(read => ({ ...testCase, read }))))(
  'retains unknown origin when $name reaches a $read.name receiver', ({ setup, linked, read }) => {
    // Given a container read, including aliases and factory-return propagation.
    const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
    const source = project.createSourceFile('/use.js',
      `${sdk}const other = { create() {} }; ${setup}\n${'setup' in read ? read.setup : ''}\nconst result = ${read.expression};`);
    const result = source.getVariableDeclarationOrThrow('result').getInitializerOrThrow();
    const trace = new ExportProvenance(loadMapping(), []);
    // When the receiver's value flow is traced.
    const origin = trace.client(result);
    // Then matching insertions link evidence without proving the retrieved value.
    expect({ origin, linked: trace.linkedDaytona }).toEqual({ origin: 'unknown', linked });
  });

it.each(cases.flatMap(testCase => [false, true].map(typed => ({ ...testCase, typed }))))(
  'preserves container calls when $name (typed=$typed)', async ({ setup, linked, typed }) => {
    // Given an opaque payload type: the runtime seed is a non-Daytona object.
    const source = `${sdk}const other: any = { create() {} };\ndeclare const condition: boolean;\n${setup}\nm.get('d').create();\n`;
    const root = temporaryProject({
      'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
      'use.ts': source,
    });
    if (typed) {
      installFakeSdk(root);
      cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
    }
    // When the real upgrade runs in strict mode.
    const result = await invoke(root, ['--strict', '--json']);
    // Then linked reads receive only a FIXME; unrelated reads stay byte-identical.
    const after = contents(root)['use.ts'];
    if (linked || typed) expect(result.code, result.output).toBe(linked ? 1 : 0);
    expect(after?.includes('FIXME(daytona-upgrade)')).toBe(linked);
    const code = after?.replace(/^[\t ]*\/\/ FIXME\(daytona-upgrade\):[^\r\n]*\r?\n/gm, '');
    expect(code).not.toContain('.v0');
    expect(code).toBe(source);
  });
