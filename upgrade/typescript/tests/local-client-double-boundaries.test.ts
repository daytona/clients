/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const dependency = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const sdk = "import type { Daytona } from '@daytonaio/sdk';\n";
const build = 'async function build(client: Daytona, name: string) { return await client.snapshot.get(name); }\n';

it('keeps the migrated mock executable when its setup and consumer move together', async () => {
  // Given
  const root = temporaryProject({
    'package.json': dependency,
    'index.test.ts': `${sdk}${build}(async () => {
      const daytona = {} as any;
      daytona.snapshot = { get: async (name: string) => name + '-snapshot' };
      const result = await build(daytona, 'base');
      return [result, await daytona.snapshot.get('assertion')];
    })();\n`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  const migrated = contents(root)['index.test.ts'] ?? '';
  expect(migrated).toContain('daytona.v0.snapshot =');
  expect(migrated).toContain('client.v0.snapshot.get(name)');
  expect(migrated).toContain("daytona.v0.snapshot.get('assertion')");
  const output = ts.transpileModule(migrated, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const outcome: unknown = runInNewContext(output.outputText, { exports: {} });
  expect(await outcome).toEqual(['base-snapshot', 'assertion-snapshot']);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.each([
  'async function build<T extends Daytona>(client: T, name: string) { await client.create(); }',
  'const build: (client: Daytona, name: string) => void = (client, name) => { void client.create(); };',
])('uses existing resolved parameter proof when %s', async declaration => {
  // Given
  const root = temporaryProject({
    'package.json': dependency,
    'index.test.ts': `${sdk}${declaration}\nconst client = { create: vi.fn() }; build(client, 'base');\n`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.test.ts']).toContain('const client = { v0: { create: vi.fn() } };');
});

it('leaves the double unchanged when its consumer has an empty body', async () => {
  // Given
  const source = `${sdk}function build(client: Daytona, name: string) {}
const client = { create: vi.fn() }; build(client, 'base');\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.test.ts']).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk'));
});

it.each([
  'const client: Other = {} as any; build(client, "base");',
  'const client = ({} as Other) as any; build(client, "base");',
  'const client = {} as any; build(client as Other, "base");',
  'const client = {} as any; build(client, "base"); consume(client);',
  'const client = {} as any; build(client, "base"); other(client);',
  'const client = {} as any; consume(client);',
  'const client = {}; build({ ...client }, "base");',
  'const client = {} as any; const holder = [client]; build(client, "base");',
  'const client = {} as any; overloaded(client, "base");',
])('does not infer a double when a different type or opaque flow participates: %s', async use => {
  // Given
  const source = `${sdk}${build}function other(client: { create(): void }) {}
declare function overloaded(client: Daytona, name: string): void;
declare function overloaded(client: Other, name: string): void;
${use}\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const reachesBuild = use.includes('build(');
  expect(result.code).toBe(reachesBuild ? 1 : 0);
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'mock-target')).toHaveLength(reachesBuild ? 1 : 0);
  expect(contents(root)['index.test.ts']?.replace(/\n\/\/ FIXME\(daytona-upgrade\):[^\n]*\n/g, ' ')).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk').replace('client.snapshot.get(name)', 'client.v0.snapshot.get(name)'));
});

it('does not rewrite an imported double even when the consumer parameter is proven', async () => {
  // Given
  const source = `${sdk}${build}import { client } from './shared';\nbuild(client, 'base');\n`;
  const root = temporaryProject({
    'package.json': dependency,
    'index.test.ts': source,
    'shared.ts': 'export const client = { snapshot: { get: vi.fn() } };\n',
  });
  installFakeSdk(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)['shared.ts']).toBe('export const client = { snapshot: { get: vi.fn() } };\n');
});

it('keeps comments and nested SDK edits when wrapping a literal', async () => {
  // Given
  const source = `${sdk}${build}const client = { // test fixture
    create: () => new Daytona().create(),
    snapshot: { get: vi.fn() },
  }; build(client, 'base');\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source.replace('import type', 'import') });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.test.ts']).toContain('{ v0: { // test fixture\n    create: () => new Daytona().v0.create(),');
});
