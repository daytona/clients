/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const provider = "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();";

it.each(['ts', 'js'])('reports the actual outside provider through re-exports in %s', async extension => {
  // Given a consumer whose imported client is constructed outside the scanned folder.
  const root = temporaryProject({
    'app/package.json': manifest,
    [`outside/provider.${extension}`]: provider,
    [`outside/barrel.${extension}`]: `export { client as api } from './provider.js';`,
    [`app/use.${extension}`]: "import { api } from '../outside/barrel.js'; api.create(); api.get('id');",
  });
  installFakeSdk(root);
  // When only the app folder is upgraded in strict mode.
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then the consumer migrates, while one actionable item identifies the real provider.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([expect.objectContaining({
    file: `use.${extension}`,
    message: `use.${extension} uses a Daytona client from ../outside/provider.${extension}, outside the scanned folder; migrate that file too.`,
  })]);
  expect(readFileSync(join(root, `app/use.${extension}`), 'utf8')).toContain("api.v0.create(); api.v0.get('id');");
  expect(readFileSync(join(root, `outside/provider.${extension}`), 'utf8')).toBe(provider);
  expect(result.code).toBe(1);
});

it.each([
  { factory: 'function make() { return new Daytona(); } export const client = make();', consumer: 'client.create();' },
  { factory: 'const make = () => new Daytona(); export const client = make();', consumer: 'const local = client; local.create();' },
  { factory: 'export function make() { return new Daytona(); }', consumer: 'make().create();' },
  { factory: 'export const make = () => new Daytona();', consumer: "'create' in make();" },
  { factory: 'export const client = new Daytona();', consumer: 'const { create } = client;' },
])('follows outside client factories and aliases: $consumer', async ({ factory, consumer }) => {
  // Given an outside provider with a real SDK type and a local factory or alias.
  const root = temporaryProject({ 'app/package.json': manifest,
    'outside/provider.ts': `import { Daytona } from '@daytonaio/sdk'; ${factory}`,
    'app/use.ts': `import { client, make } from '../outside/provider'; ${consumer}` });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then the outside provider is reported once, independently of other manual guidance.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.message.includes('outside the scanned folder'))).toEqual([
    expect.objectContaining({ file: 'use.ts', message: 'use.ts uses a Daytona client from ../outside/provider.ts, outside the scanned folder; migrate that file too.' }),
  ]);
  expect(result.code).toBe(1);
});

it('reports each outside provider once when a consumer uses two clients', async () => {
  // Given two external providers and repeated use of one client.
  const root = temporaryProject({ 'app/package.json': manifest,
    'outside/a.ts': provider, 'outside/b.ts': provider,
    'app/use.ts': "import { client as a } from '../outside/a'; import { client as b } from '../outside/b'; a.create(); a.get('id'); b.create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then both distinct providers contribute strict-mode manual items.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme').map(item => item.message)).toEqual([
    'use.ts uses a Daytona client from ../outside/a.ts, outside the scanned folder; migrate that file too.',
    'use.ts uses a Daytona client from ../outside/b.ts, outside the scanned folder; migrate that file too.',
  ]);
  expect(result.code).toBe(1);
});

it.each([
  { path: 'app/provider.ts', source: provider, imported: './provider', moved: true },
  { path: 'app-sibling/provider.ts', source: "export const client = { create() {} };", imported: '../app-sibling/provider', moved: false },
])('does not warn for scanned providers or unrelated clients: $path', async ({ path, source, imported, moved }) => {
  // Given either a scanned SDK provider or an unrelated outside provider.
  const root = temporaryProject({ 'app/package.json': manifest, [path]: source,
    'app/use.ts': `import { client } from '${imported}'; client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then SDK package declaration files and unrelated project clients never get warnings.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(readFileSync(join(root, 'app/use.ts'), 'utf8')).toContain(moved ? 'client.v0.create();' : 'client.create();');
  expect(result.code).toBe(0);
});

it.each([
  "import { client as imported } from '../app-sibling/provider'; function make() { return imported; } make().create();",
  "import * as outside from '../app-sibling/provider'; outside.client.create();",
  "import * as outside from '../app-sibling/provider'; outside['client'].create();",
  "import imported from '../app-sibling/default'; imported.create();",
])('follows the actual outside export through consumer wrappers: %s', async source => {
  // Given aliases, namespace exports, or a wrapper within the scanned root.
  const root = temporaryProject({ 'app/package.json': manifest, 'app-sibling/provider.ts': provider,
    'app-sibling/default.ts': "export { client as default } from './provider';", 'app/use.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then a sibling prefix is outside and the defining provider, not the barrel, is named.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([expect.objectContaining({
    message: 'use.ts uses a Daytona client from ../app-sibling/provider.ts, outside the scanned folder; migrate that file too.',
  })]);
  expect(readFileSync(join(root, 'app/use.ts'), 'utf8')).toContain('.v0.create();');
  expect(result.code).toBe(1);
});

it('reports manual work without failing when strict mode is absent', async () => {
  // Given an outside SDK provider.
  const root = temporaryProject({ 'app/package.json': manifest, 'outside/provider.ts': provider,
    'app/use.ts': "import { client } from '../outside/provider'; client.create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--json']);
  // Then the warning is retained while only strict mode turns it into failure.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(1);
  expect(result.code).toBe(0);
});

it('reports one provider file when two consumer files both migrate', async () => {
  // Given two consumers of the same outside SDK provider.
  const source = "import { client } from '../outside/provider'; client.create();";
  const root = temporaryProject({ 'app/package.json': manifest, 'outside/provider.ts': provider,
    'app/a.ts': source, 'app/b.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then the provider produces one manual item without suppressing either rewrite.
  const report: Report = JSON.parse(result.output);
  const manual = report.items.filter(item => item.kind === 'fixme');
  expect(manual).toHaveLength(1);
  for (const item of manual) expect(item.message).toBe(`${item.file} uses a Daytona client from ../outside/provider.ts, outside the scanned folder; migrate that file too.`);
  for (const file of ['a.ts', 'b.ts']) expect(readFileSync(join(root, 'app', file), 'utf8')).toContain('client.v0.create();');
  expect(result.code).toBe(1);
});

it('reports the same provider independently in separate invocations', async () => {
  // Given a reusable fixture that dry runs will leave unchanged.
  const root = temporaryProject({ 'app/package.json': manifest, 'outside/provider.ts': provider,
    'app/use.ts': "import { client } from '../outside/provider'; client.create();" });
  installFakeSdk(root);
  // When two CLI invocations analyze the same fixture in this process.
  const results = await Promise.all([0, 1].map(() => invoke(join(root, 'app'), ['--strict', '--json', '--dry'])));
  // Then neither invocation inherits the other's already-reported providers.
  for (const result of results) {
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(1);
    expect(result.code).toBe(1);
  }
});
