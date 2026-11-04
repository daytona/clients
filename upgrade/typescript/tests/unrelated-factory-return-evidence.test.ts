/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([true, false].flatMap(installed => [
  'function createApp() { const app = express(); app.use(express.json()); return app; }',
  'function createApp() { const app = (0, express)(); return app; }',
  'function createApp() { return (prepare(), express()); }',
  'const createApp = () => express();',
  'const createApp = () => { const app = express(); return app; };',
  'const createApp = function () { return express(); };',
].map(factory => ({ installed, factory }))))('keeps external factory routes clean (installed=$installed): $factory', async ({ installed, factory }) => {
  // Given a local Express factory, with or without an installed untyped package.
  const routes = "import { app } from './app.js'; app.get('/a', handler); app.get('/b', handler); app.delete('/c', handler);";
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'app.js': `import express from 'express'; ${factory} export const app = createApp();`,
    'routes.js': routes,
    ...(installed ? { 'node_modules/express/package.json': '{"name":"express","main":"index.js"}',
      'node_modules/express/index.js': 'module.exports = function express() { return {}; };' } : {}),
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['routes.js']).toBe(routes);
  expect(result.code).toBe(0);
});

it.each([true, false])('retains Daytona factory handling (typed=%s)', async typed => {
  // Given a local factory returning a Daytona client.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'provider.ts': "import { Daytona } from '@daytonaio/sdk'; function make() { const client = new Daytona(); return client; } export const client = make();",
    'use.ts': "import { client } from './provider.js'; client.create();" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['use.ts']).toContain(typed ? 'client.v0.create();' : 'FIXME(daytona-upgrade)');
  expect(result.code).toBe(typed ? 0 : 1);
});

it.each([
  { factory: 'function make() { return unresolved; }', expected: 'unknown' },
  { factory: 'function make() { return; }', expected: 'unknown' },
  { factory: 'function make() {}', expected: 'unknown' },
  { factory: 'function make() { return make(); }', expected: 'unknown' },
  { factory: 'function make() { class Internal {} return new Internal(); }', expected: 'not-daytona' },
  { factory: 'function make() { if (flag) return express(); return new Other(); }', expected: 'not-daytona' },
  { factory: 'function make() { if (flag) return express(); return unresolved; }', expected: 'unknown' },
  { factory: 'function make() { if (flag) return express(); return new Daytona(); }', expected: 'unknown' },
  { factory: 'function make() { if (flag) return unresolved; return new Daytona(); }', expected: 'unknown' },
  { factory: 'function make() { function nested() { return new Daytona(); } return express(); }', expected: 'not-daytona' },
  { factory: 'function make() { const inner = () => new Daytona(); return inner(); }', expected: 'daytona' },
  { factory: 'function first() { return express(); } const second = () => first(); function third() { return second(); } const make = () => third();', expected: 'not-daytona' },
  { factory: 'function first() { return express(); } const second = () => first(); function third() { return second(); } const fourth = () => third(); const make = () => fourth();', expected: 'unknown' },
])('combines bounded local factory returns as $expected: $factory', ({ factory, expected }) => {
  // Given an untyped module so return provenance cannot be inferred from SDK types.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  project.createSourceFile('/provider.js', `import express from 'express'; import { Other } from 'other';
    import { Daytona } from '@daytonaio/sdk'; ${factory} export const client = make();`);
  const source = project.createSourceFile('/use.js', "import { client } from './provider.js'; client.create();");
  // When
  const origin = new ExportProvenance(loadMapping(), []).hint({ source, module: './provider.js', name: 'client' });
  // Then
  expect(origin).toBe(expected);
});

it.each([true, false])('ignores an unrelated provider import for an unresolved factory value (sdk=%s)', async sdk => {
  // Given an imported result whose factory has no receiver-linked Daytona evidence.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'provider.js': `${sdk ? "import { Daytona } from '@daytonaio/sdk';" : ''}
      function make() { return unresolved; } export const client = make();`,
    'use.ts': "import { client } from './provider.js'; client.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(0);
  expect(result.code).toBe(0);
  expect(contents(root)['use.ts']).toBe("import { client } from './provider.js'; client.create();");
});
