/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync, mkdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, here, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const dependency = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const sdk = "import type { Daytona } from '@daytonaio/sdk';\n";

function execute(source: string): unknown {
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  return runInNewContext(output.outputText, { exports: {} });
}

it.each([
  'const fake = { create: () => 42 }; build(fake);',
  'build({ create: () => 42 });',
])('preserves execution when a local overload has a migrated implementation: %s', async supply => {
  // Given
  const source = `${sdk}function build(client: Daytona): unknown;
function build(client: Daytona) { return client.create(); }
${supply}\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.test.ts'] ?? '')).toBe(42);
  expect(result.code, result.output).toBe(0);
});

it.each([
  'return arguments[0].create();',
  'const args = arguments; return args[0].create();',
  'return eval("arguments[0].create()");',
  'return new Function("args", "return args[0].create()") (arguments);',
  'void client; return arguments[0].create();',
  'void client; return eval("client.create()");',
  'void client; return Function("args", "return args[0].create()") (arguments);',
  'if (false) client.create(); return arguments[0].create();',
  'if (false) client.create(); return eval("client.create()");',
  'if (false) client.create(); return Function("args", "return args[0].create()") (arguments);',
].flatMap(body => [
  'const fake = { create: () => 42 }; build(fake);',
  'build({ create: () => 42 });',
].map(supply => ({ body, supply }))))('preserves execution when a consumer reads its argument indirectly: $body / $supply', async ({ body, supply }) => {
  // Given
  const source = `${sdk}function build(client: Daytona) { ${body} }\n${supply}\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  await invoke(root, ['--json']);
  // Then
  expect(execute(contents(root)['index.test.ts'] ?? '')).toBe(42);
});

it('reports a double when an overloaded SDK signature has no matching migrated implementation', async () => {
  // Given
  const source = `${sdk}function build(client: Daytona): unknown;
function build(client: { create(): number }) { return client.create(); }
const fake = { create: () => 42 }; build(fake);\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.test.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.test.ts'] ?? '')).toBe(42);
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'mock-target' }),
  ]));
});

it.skipIf(process.platform === 'win32')('migrates a type-erased SDK client when its declarations resolve outside node_modules', async () => {
  // Given
  const linked = temporaryProject();
  cpSync(join(here, 'fake-sdk'), linked, { recursive: true });
  const source = `import { Daytona } from '@daytonaio/sdk';
const client: any = new Daytona(); client.create();\n`;
  const root = temporaryProject({ 'package.json': dependency, 'index.ts': source });
  mkdirSync(join(root, 'node_modules/@daytonaio'), { recursive: true });
  symlinkSync(linked, join(root, 'node_modules/@daytonaio/sdk'), 'dir');
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const migrated = contents(root)['index.ts'] ?? '';
  const output = ts.transpileModule(migrated, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const outcome: unknown = runInNewContext(output.outputText, { exports: {}, require: () => ({
    Daytona: class { readonly v0 = { create: () => 42 }; },
  }) });
  expect(outcome).toBe(42);
  expect(result.code, result.output).toBe(0);
});
