/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['let', 'var'].flatMap(binding => [
  { binding, assignment: 'client = new Daytona();', fixmes: 1 },
  { binding, assignment: 'client = unknownFactory();', fixmes: 0 },
  { binding, assignment: '', fixmes: 0 },
  { binding, assignment: "client = axios.create();", fixmes: 0 },
  { binding, assignment: "client = { create() {} };", fixmes: 0 },
  { binding, assignment: 'client = 1;', fixmes: 0 },
  { binding, assignment: 'client = unknownFactory(); client = new Daytona();', fixmes: 1 },
  { binding, assignment: 'client = unknownFactory(); client = {};', fixmes: 0 },
  { binding, assignment: 'function nested() { let client; client = new Daytona(); } client = {};', fixmes: 0 },
]))('preserves provenance for exported $binding with $assignment', async ({ binding, assignment, fixmes }) => {
  // Given an untyped lazy provider and an imported consumer.
  const source = "import { client } from './provider.js'; client.create();";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0","axios":"*"}}',
    'provider.js': `import { Daytona } from '@daytonaio/sdk'; import axios from 'axios';
      ${binding} client; export function init() { ${assignment} }; export { client };`, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only receiver-linked Daytona flow requires review; unrelated imports do not.
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string; readonly file: string }) =>
    item.kind === 'fixme' && item.file === 'index.ts')).toHaveLength(fixmes);
  expect(contents(root)['index.ts']).not.toContain('client.v0.create();');
  if (fixmes === 0) expect(contents(root)['index.ts']).toBe(source);
  else expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
});

it.each([
  { assignments: 'client = new Daytona();', expected: 'daytona' },
  // Mixed origins need review: unknown, never proven Daytona.
  { assignments: 'client = unknownFactory(); client = new Daytona();', expected: 'unknown' },
  { assignments: 'client = {}; client = 1;', expected: 'not-daytona' },
  { assignments: "client = axios.create();", expected: 'not-daytona' },
  { assignments: 'client = unknownFactory(); client = {};', expected: 'unknown' },
  { assignments: '', expected: 'unknown' },
  { assignments: 'function nested() { let client; client = new Daytona(); }', expected: 'unknown' },
])('classifies later assignments as $expected when $assignments', ({ assignments, expected }) => {
  // Given a module binding and assignments, with a same-name shadow distinguished by symbol.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  project.createSourceFile('/provider.js', `import { Daytona } from '@daytonaio/sdk'; import axios from 'axios';
    let client; export function init() { ${assignments} }; export { client };`);
  const source = project.createSourceFile('/index.ts', "import { client } from './provider.js'; client.create();");
  // When
  const provenance = new ExportProvenance(loadMapping(), []).hint({ source, module: './provider.js', name: 'client' });
  // Then
  expect(provenance).toBe(expected);
});

it('keeps a review item when a typed consumer casts a lazy JS export', async () => {
  // Given mixed JS/TS modules, with installed SDK types.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"allowJs":true,"checkJs":false,"strict":true,"noImplicitAny":false}}',
    'd.js': "import { Daytona } from '@daytonaio/sdk'; let client; export function init() { client = new Daytona(); } export { client };",
    'use.ts': "import type { Daytona } from '@daytonaio/sdk'; import { client, init } from './d.js'; init(); (client as Daytona).create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'use.ts', kind: 'fixme' }));
  expect(contents(root)['use.ts']).toContain('(client as Daytona).create();');
  expect(result.code).toBe(1);
});
