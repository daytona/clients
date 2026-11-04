/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import type { Report } from '../src/model.js';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([true, false].flatMap(installed => [
  'const app = express(); app.use(express.json()); return app;',
  'return cached ??= express();',
  'return cached ||= express();',
  'return cached || express();',
  'return cached ?? express();',
  'return flag ? express() : express();',
].map(body => ({ installed, body }))))('leaves Express routes untouched when factory is imported (installed=$installed): $body', async ({ installed, body }) => {
  // Given a project factory in its own module, with or without Express installed.
  const routes = "import { app } from './app.js'; app.get('/a', handler); app.get('/b', handler); app.delete('/c', handler);";
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'factory.js': `import express from 'express'; let cached; export function createApp() { ${body} }`,
    'app.js': "import { createApp } from './factory.js'; export const app = createApp();",
    'routes.js': routes,
    ...(installed ? { 'node_modules/express/package.json': '{"name":"express","main":"index.js"}',
      'node_modules/express/index.js': 'module.exports = function express() { return {}; };' } : {}),
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then no route gets a Daytona edit or review item.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['routes.js']).toBe(routes);
  expect(result.code).toBe(0);
});

it.each([true, false])('preserves imported cached Daytona clients when typed=%s', async typed => {
  // Given a cached client factory in a different module from the exported client.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'factory.ts': "import { Daytona } from '@daytonaio/sdk'; let cached: Daytona | undefined; export function make() { return cached ??= new Daytona(); }",
    'provider.ts': "import { make } from './factory'; export const client = make();",
    'use.ts': "import { client } from './provider'; client.create();" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then typed clients migrate; absent types require manual review.
  expect(contents(root)['use.ts']).toContain(typed ? 'client.v0.create();' : 'FIXME(daytona-upgrade)');
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.ts' && item.kind === 'fixme')).toHaveLength(typed ? 0 : 1);
  expect(result.code).toBe(typed ? 0 : 1);
});

it.each([
  "import { Daytona } from '@daytonaio/sdk';",
  "import { Daytona } from '@daytona/sdk';",
  "import { Sandbox } from '@daytona/sdk/v0';",
  "const { Daytona } = require('@daytonaio/sdk');",
  "const sdk = require('@daytona/sdk/v0');",
  "import { Daytona } from '@daytonaio/sdk/v0';",
  '',
])('ignores unrelated SDK imports on an unknown factory path: %s', async sdk => {
  // Given an unknown value with no receiver-linked use of the factory module's SDK import.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'factory.js': `${sdk} export function make() { return unresolved; }`,
    'provider.js': "import { make } from './factory.js'; export const client = make();",
    'use.js': "import { client } from './provider.js'; client.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then a free identifier alone never establishes an unresolved module.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['use.js']).toBe("import { client } from './provider.js'; client.create();");
  // Namespace requires and v0 module imports retain their independent manual items.
  const fallback = sdk.includes('require(');
  const privateImport = sdk.includes("from '@daytonaio/sdk/v0'");
  expect(report.items.filter(item => item.kind === 'types-unresolved')).toHaveLength(fallback ? 1 : 0);
  expect(report.items.filter(item => item.kind === 'private-import')).toHaveLength(privateImport ? 1 : 0);
  expect(result.code).toBe(fallback || privateImport ? 1 : 0);
});

it.each([
  { body: 'return express();', expected: 'not-daytona' },
  { body: 'return flag ? express() : express();', expected: 'not-daytona' },
  { body: 'return flag ? new Daytona() : new Daytona();', expected: 'daytona' },
  { body: 'return flag ? express() : new Daytona();', expected: 'unknown' },
  { body: 'return cached || express();', expected: 'not-daytona' },
  { body: 'return cached ?? express();', expected: 'not-daytona' },
  { body: 'return cached ||= express();', expected: 'not-daytona' },
  { body: 'return cached ??= express();', expected: 'not-daytona' },
  { body: 'return cached ??= new Daytona();', expected: 'unknown' },
])('traces imported factory branches strictly as $expected: $body', ({ body, expected }) => {
  // Given a known Express cache and SDK involvement so unknown cannot be suppressed.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  project.createSourceFile('/factory.js', `import express from 'express'; import { Daytona } from '@daytonaio/sdk';
    let cached = express(); export function make() { ${body} }`);
  project.createSourceFile('/provider.js', "import { make } from './factory.js'; export const client = make();");
  const source = project.createSourceFile('/use.js', "import { client } from './provider.js'; client.create();");
  // When
  const provenance = new ExportProvenance(loadMapping(), []).hint({ source, module: './provider.js', name: 'client' });
  // Then agreeing branches are proven, while mixed origins stay unknown.
  expect(provenance).toBe(expected);
});

it.each([
  { factory: "export default function make() { return express(); }", imported: "import make from './factory.js';", call: 'make()' },
  { factory: "export const make = () => express();", imported: "import { make as build } from './factory.js';", call: 'build()' },
  { factory: "export function make() { return express(); }", imported: "import * as factory from './factory.js';", call: 'factory.make()' },
  { factory: "export function make() { return express(); }", imported: "import { make } from './barrel.js';", call: 'make()' },
  { factory: "exports.make = function () { return express(); };", imported: "const { make } = require('./factory.js');", call: 'make()' },
])('follows project function export forms when called as $call through $imported', ({ factory, imported, call }) => {
  // Given SDK involvement on the path and a non-client function behind varying exports.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  project.createSourceFile('/factory.js', `import express from 'express'; import { Daytona } from '@daytonaio/sdk'; ${factory}`);
  project.createSourceFile('/barrel.js', "export { make } from './factory.js';");
  project.createSourceFile('/provider.js', `${imported} export const client = ${call};`);
  const source = project.createSourceFile('/use.js', "import { client } from './provider.js'; client.create();");
  // When
  const provenance = new ExportProvenance(loadMapping(), []).hint({ source, module: './provider.js', name: 'client' });
  // Then tracing, rather than merely suppressing unknown, proves the non-client origin.
  expect(provenance).toBe('not-daytona');
});

it.each([
  { barrel: "import { Daytona } from '@daytonaio/sdk'; export { client } from './provider.js';", provider: 'export const client = unresolved;', fixmes: 0 },
  { barrel: "export { client } from './missing.js';", provider: '', fixmes: 0 },
  { barrel: "export { client } from './provider.js';", provider: "import { make } from './missing.js'; export const client = make();", fixmes: 0 },
  { barrel: "export { client } from './provider.js';", provider: 'export const client = unresolved;', fixmes: 0 },
])('uses traced module evidence through re-exports when $barrel and $provider', async ({ barrel, provider, fixmes }) => {
  // Given an unknown export with SDK evidence, missing modules, or neither on its path.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'barrel.js': barrel, 'provider.js': provider,
    'use.js': "import { client } from './barrel.js'; client.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then unrelated imports and missing generic paths do not establish receiver evidence.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(fixmes);
  expect(contents(root)['use.js']).toBe("import { client } from './barrel.js'; client.create();");
  expect(result.code).toBe(fixmes ? 1 : 0);
});
