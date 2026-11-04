/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createRequire } from 'node:module';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const sdk = "import type { Daytona } from '@daytonaio/sdk';\n";
const consumer = 'function build(client: Daytona) { return client.create(); }\n';
const message = 'Mock replacement reaches a Daytona client consumer but its v0 shape is unresolved; give the replacement a v0 attribute before using SDK 2.x.';
const cases = [
  { name: 'cast', setup: "const fake = { create: async () => ({ id: 's1' }) } as unknown as Daytona;" },
  { name: 'class', setup: "class Fake { async create() { return { id: 's1' }; } } const fake = new Fake() as unknown as Daytona;" },
  { name: 'shared', setup: "import { fake } from './shared';" },
  { name: 'mixed', setup: "function other(client: { create(): Promise<unknown> }) { return client.create(); } const fake = { create: async () => ({ id: 's1' }) } as any; void other(fake);" },
  { name: 'identity', setup: "const fake = { create: async () => ({ id: 's1' }) } as any; const seen = new WeakSet<object>(); seen.add(fake);" },
  { name: 'existing-v0', setup: "const fake = { v0: { create: async () => ({ id: 'old' }) }, create: async () => ({ id: 's1' }) } as any;" },
] as const;

function execute(root: string, source: string): unknown {
  const require = createRequire(join(root, 'index.ts'));
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  return runInNewContext(compiled.outputText, { exports: {}, require: (name: string) => {
    if (name === './shared') {
      const shared = ts.transpileModule(contents(root)['shared.ts'] ?? '', { compilerOptions: { module: ts.ModuleKind.CommonJS } });
      const exports = {};
      runInNewContext(shared.outputText, { exports });
      return exports;
    }
    return require(name);
  } });
}

it.each(cases)('reports an unresolved replacement when the $name double reaches a rewritten consumer', async ({ name, setup }) => {
    // Given: unresolved replacement shapes supplied to a client consumer.
  const source = `${sdk}${consumer}${setup}\n(async () => await build(fake))();\n`;
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': source,
    ...(name === 'shared' ? { 'shared.ts': "export const fake = { create: async () => ({ id: 's1' }) } as any;\n" } : {}),
  });
  installFakeSdk(root);
  expect(await execute(root, source)).toEqual({ id: 's1' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: an unsafe migration cannot claim success; the replacement is untouched.
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', message }));
  const migrated = contents(root)['index.ts'] ?? '';
  expect(migrated).toContain(setup);
  expect(migrated).toContain('client.v0.create()');
  if (name === 'existing-v0') expect(await execute(root, migrated)).toEqual({ id: 'old' });
  else await expect(execute(root, migrated)).rejects.toThrow("Cannot read properties of undefined (reading 'create')");
});

it('keeps unresolved replacements strict on reruns when the consumer already moved', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': `${sdk}${consumer}const fake = { v0: {}, create: () => 42 } as any; build(fake);` });
  installFakeSdk(root);
  await invoke(root, ['--strict', '--json']);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', message }));
  expect(contents(root)).toEqual(before);
});

it.each([
  'function build(client: Daytona) { void client; return client.create(); } build(fake);',
  'class Consumer { constructor(client: Daytona) { client.create(); } } new Consumer(fake);',
  'const build: (client: Daytona) => unknown = client => client.create(); build(fake);',
  'function build(client: Daytona): unknown; function build(client: Daytona) { return client.create(); } build(fake);',
])('reports unsafe replacements when any consumer access moves: %s', async body => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': `${sdk}const fake = { v0: {}, create: () => 42 } as any; ${body}` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', message }));
});

it.each([
  'build(getFake());',
  'build(({ create: () => 42 } as unknown as Daytona));',
  'build(new Fake() as unknown as Daytona);',
  'build(<Daytona><unknown>{ create: () => 42 });',
  'build(holder.fake);',
])('reports opaque arguments when a consumer moves: %s', async supply => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': `${sdk}${consumer}${supply}` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', message }));
});

it.each([
  'build(new Daytona());',
  'const real: Daytona = new Daytona(); build(real);',
  'const real = new Daytona(); const alias = real; build(alias);',
  'function makeClient(): Daytona { return new Daytona(); } build(makeClient());',
  'function forward(client: Daytona) { return build(client); } forward(new Daytona());',
  "import { real } from './shared'; build(real);",
])('does not report production clients when their SDK identity is proven: %s', async supply => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': `${sdk.replace('import type', 'import')}${consumer}${supply}`,
    'shared.ts': "import { Daytona } from '@daytonaio/sdk'; export const real = new Daytona();",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
