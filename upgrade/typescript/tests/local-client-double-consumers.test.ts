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

const dependency = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const sdk = "import { Daytona } from '@daytonaio/sdk';\n";

function execute(source: string, root: string): unknown {
  const require = createRequire(join(root, 'index.test.ts'));
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  return runInNewContext(output.outputText, { exports: {}, require: (name: string) =>
    ['@daytonaio/sdk', '@daytona/sdk'].includes(name)
      ? { Daytona: class { readonly v0 = { create: () => 99 }; } } : require(name) });
}

it.each([
  'function build(client: Local) { return client.create(); }',
  'function build<T extends Local>(client: T) { return client.create(); }',
].flatMap(consumer => [
  'const fake = { create: () => Promise.resolve(42) }; build(fake);',
  'build({ create: () => Promise.resolve(42) });',
].map(supply => ({ consumer, supply }))))('preserves execution when an overriding subclass consumes the double: $consumer / $supply', async ({ consumer, supply }) => {
  // Given
  const source = `${sdk}class Local extends Daytona { override create() { return Promise.resolve(7); } }
${consumer}\n${supply}\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(await execute(source, root)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(await execute(contents(root)['index.test.ts'] ?? '', root)).toBe(42);
  expect(result.code).toBe(0);
});

it.each(['index.d.ts', 'index.ts'].flatMap(types => [
  'const fake = { create: () => Promise.resolve(42) }; build(fake);',
  'build({ create: () => Promise.resolve(42) });',
].map(supply => ({ types, supply }))))('preserves execution when a node_modules consumer is resolved from $types: $supply', async ({ types, supply }) => {
  // Given
  const source = `import { build } from 'consumer';\n${supply}\n`;
  const consumer = types.endsWith('.d.ts')
    ? 'export declare function build(client: Daytona): Promise<unknown>;'
    : 'export function build(client: Daytona) { return client.create(); }';
  const root = temporaryProject({
    'package.json': dependency,
    'index.test.ts': source,
    'node_modules/consumer/package.json': JSON.stringify({ name: 'consumer', main: 'index.js', types }),
    [`node_modules/consumer/${types}`]: `${sdk}${consumer}\n`,
    'node_modules/consumer/index.js': 'exports.build = client => client.create();\n',
  });
  installFakeSdk(root);
  expect(await execute(source, root)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(await execute(contents(root)['index.test.ts'] ?? '', root)).toBe(42);
  expect(result.code).toBe(0);
});

it('preserves execution when a project declaration describes an unrewritten implementation', async () => {
  // Given
  const source = `import { build } from './consumer';
const fake = { create: () => Promise.resolve(42) }; build(fake);\n`;
  const root = temporaryProject({
    'package.json': dependency,
    'index.test.ts': source,
    'consumer.d.ts': `${sdk}export declare function build(client: Daytona): Promise<unknown>;\n`,
    'consumer.js': 'exports.build = client => client.create();\n',
  });
  installFakeSdk(root);
  expect(await execute(source, root)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(await execute(contents(root)['index.test.ts'] ?? '', root)).toBe(42);
  expect(result.code).toBe(0);
});

it.each([
  'const fake = { create: () => Promise.resolve(42) }; build(fake);',
  'build({ create: () => Promise.resolve(42) });',
])('keeps execution when the project root consumer is rewritten: %s', async supply => {
  // Given
  const source = `${sdk}function build(client: Daytona) { return client.create(); }\n${supply}\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(await execute(source, root)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const migrated = contents(root)['index.test.ts'] ?? '';
  expect(migrated).toContain('client.v0.create()');
  expect(await execute(migrated, root)).toBe(42);
  expect(result.code).toBe(0);
});

it('retains a visible subclass override when its variable is widened to the SDK root', async () => {
  // Given
  const source = `${sdk}class Offline extends Daytona { override create() { return Promise.resolve(42); } }
const d: Daytona = new Offline(); d.create();\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(await execute(source, root)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const migrated = contents(root)['index.test.ts'] ?? '';
  expect(await execute(migrated, root)).toBe(42);
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]));
});
