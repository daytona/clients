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

it.each([
  { factory: 'export const clients = { make() { return new Daytona(); } };', call: 'factory.clients.make()', expected: 'daytona' },
  { factory: 'export const clients = { make: () => new Daytona() };', call: 'factory.clients.make()', expected: 'daytona' },
  { factory: 'export const clients = { nested: { make: () => new Daytona() } };', call: "factory['clients'].nested['make']()", expected: 'daytona' },
  { factory: 'export const clients = unresolved;', call: 'factory.clients.make()', expected: 'unknown' },
  { factory: 'export const clients = {};', call: 'factory.clients.make()', expected: 'unknown' },
  { factory: '', call: 'factory.clients.make()', expected: 'unknown' },
  { factory: 'export const clients = { make: () => new Daytona() };', call: 'factory.clients[key]()', expected: 'unknown' },
  { factory: 'export const clients = { make: () => express() };', call: 'factory.clients.make()', expected: 'not-daytona' },
])('resolves the whole namespace factory path as $expected when $factory / $call', ({ factory, call, expected }) => {
  // Given a decoy top-level export whose name matches the final path segment.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  project.createSourceFile('/factory.js', `import { Daytona } from '@daytonaio/sdk'; import express from 'express';
    export function make() { return express(); } ${factory}`);
  project.createSourceFile('/provider.js', `import * as factory from './factory.js'; export const client = ${call};`);
  const source = project.createSourceFile('/use.js', "import { client } from './provider.js'; client.create();");
  // When
  const trace = new ExportProvenance(loadMapping(), []);
  const origin = trace.hint({ source, module: './provider.js', name: 'client' });
  // Then only the selected factory value can establish a Daytona link.
  expect(origin).toBe(expected);
  expect(trace.linkedDaytona).toBe(expected === 'daytona');
});

it.each([true, false].flatMap(typed => [
  'make() { return new Daytona(); }',
  'make: () => new Daytona()',
].flatMap(member => [true, false].map(decoy => ({ typed, member, decoy })))))
('migrates nested namespace factories when typed=$typed, decoy=$decoy, member=$member', async ({ typed, member, decoy }) => {
  // Given a nested object factory, optionally alongside an unrelated make export.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'factory.ts': `import { Daytona } from '@daytonaio/sdk'; import express from 'express';
      ${decoy ? 'export function make() { return express(); }' : ''}
      export const clients = { ${member} };`,
    'provider.ts': "import * as factory from './factory'; export const client = factory.clients.make();",
    'use.ts': "import { client } from './provider'; client.create();",
  });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then checker-proven clients migrate, while unresolved SDK types get a FIXME.
  const report: Report = JSON.parse(result.output);
  expect(contents(root)['use.ts']).toContain(typed ? 'client.v0.create();' : 'FIXME(daytona-upgrade)');
  expect(report.items.filter(item => item.file === 'use.ts' && item.kind === 'fixme')).toHaveLength(typed ? 0 : 1);
  expect(result.code).toBe(typed ? 0 : 1);
});

it.each([
  'export const clients = unresolved;',
  'export const clients = {};',
  '',
])('ignores unrelated SDK imports when a namespace factory segment is unresolved: %s', async factory => {
  // Given an SDK import in the target module, but no receiver-linked member path.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'factory.js': `import { Daytona } from '@daytonaio/sdk'; import express from 'express';
      export function make() { return express(); } ${factory}`,
    'provider.js': "import * as factory from './factory.js'; export const client = factory.clients.make();",
    'use.js': "import { client } from './provider.js'; client.create();",
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then neither the unrelated top-level make nor SDK import proves receiver evidence.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['use.js']).toBe("import { client } from './provider.js'; client.create();");
  expect(result.code).toBe(0);
});
