/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const sdk = "import { Daytona } from '@daytona/sdk';\n";
const variants = [
  { name: 'global jest', setup: '', spy: 'jest.spyOn' },
  { name: 'global vi', setup: '', spy: 'vi.spyOn' },
  { name: 'imported vi', setup: "import { vi as test } from 'vitest';\n", spy: 'test.spyOn' },
  { name: 'imported jest', setup: "import { jest as test } from '@jest/globals';\n", spy: 'test.spyOn' },
  { name: 'imported spyOn', setup: "import { spyOn as spy } from 'vitest';\n", spy: 'spy' },
  { name: 'Jest imported spyOn', setup: "import { spyOn } from '@jest/globals';\n", spy: 'spyOn' },
  { name: 'Jest namespace', setup: "import * as jest from '@jest/globals';\n", spy: 'jest.jest.spyOn' },
  { name: 'Vitest namespace', setup: "import * as test from 'vitest';\n", spy: 'test.vi.spyOn' },
] as const;

it.each(variants.flatMap(variant => [false, true].map(typed => ({ ...variant, typed }))))('moves proven root spy targets for $name (typed=$typed)', async ({ setup, spy, typed }) => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': `${sdk}${setup}const client = new Daytona();\n${spy}(client, 'delete');\nclient.create();\n`,
  });
  if (typed) {
    installFakeSdk(root);
    cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  }
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(`${sdk}${setup}const client = new Daytona();\n${spy}(client.v0, 'delete');\nclient.v0.create();\n`);
});

it.each(variants)('reports prototype targets for $name', async ({ setup, spy }) => {
  // Given
  const source = `${sdk}${setup}${spy}(Daytona.prototype, 'create');\n`;
  const root = temporaryProject({ 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade):');
  expect(contents(root)['index.ts']).toContain(`${spy}(Daytona.prototype, 'create');`);
  expect(contents(root)['index.ts']).not.toContain('prototype.v0');
});

it.each(variants)('reports linked unproven targets for $name', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${sdk}${setup}declare const daytonaClient: unknown;\n${spy}(daytonaClient, 'get');\n` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade):');
  expect(contents(root)['index.ts']).toContain(`${spy}(daytonaClient, 'get');`);
});

it.each(variants)('leaves unrelated targets clean for $name', async ({ setup, spy }) => {
  // Given
  const source = `${sdk}${setup}${spy}(console, 'log');\n${spy}(api, 'get');\n`;
  const root = temporaryProject({ 'index.ts': source });
  installFakeSdk(root);
  cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
});

it.each(variants)('preserves migrated spy targets on reruns for $name', async ({ setup, spy }) => {
  // Given
  const source = `${sdk}${setup}const client = new Daytona();\n${spy}(client.v0, 'delete');\n${spy}(client.v0.snapshot, 'list');\n`;
  const root = temporaryProject({ 'index.ts': source });
  installFakeSdk(root);
  cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
});

it.each(variants)('reports dynamic names on proven clients for $name', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${sdk}${setup}const client = new Daytona();\n${spy}(client, name);\n` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade):');
  expect(contents(root)['index.ts']).toContain(`${spy}(client, name);`);
});

it.each(variants)('keeps service target migration for $name', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${sdk}${setup}const client = new Daytona();\n${spy}(client.snapshot, 'list');\n` });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain(`${spy}(client.v0.snapshot, 'list');`);
});

it.each([
  "import { Daytona as D } from '@daytona/sdk';\nvi.spyOn(D.prototype, 'create');\n",
  "import { AsyncDaytona as D } from '@daytona/sdk';\nvi.spyOn(D.prototype, 'create');\n",
  "import * as sdk from '@daytona/sdk';\njest.spyOn(sdk.Daytona.prototype, 'delete');\n",
  "const { Daytona } = require('@daytona/sdk');\njest.spyOn(Daytona.prototype, 'create');\n",
])('reports root class prototypes through imported aliases: %s', async source => {
  // Given
  const root = temporaryProject({ 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.ts']).toContain(source.slice(source.indexOf('\n') + 1));
  expect(contents(root)['index.ts']).not.toContain('prototype.v0');
});

it.each([
  "const vi = { spyOn() {} };\nvi.spyOn(client, 'delete');",
  "import { spyOn } from 'other';\nspyOn(client, 'delete');",
  "import { vi } from 'other';\nvi.spyOn(client, 'delete');",
  "function f(jest: unknown) { jest.spyOn(client, 'delete'); }",
  "vi.spyOn(client, 'close');",
])('preserves unrelated spy APIs or unmoved members: %s', async code => {
  // Given
  const source = `${sdk}const client = new Daytona();\n${code}\n`;
  const root = temporaryProject({ 'index.ts': source });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.ts']).toBe(source);
});

it.each(variants)('keeps inserted spy FIXMEs byte-stable on reruns for $name', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${sdk}${setup}${spy}(Daytona.prototype, 'create');\n` });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)).toEqual(before);
});

it.each(variants)('keeps rewritten spies byte-stable on reruns for $name', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${sdk}${setup}const client = new Daytona();\n${spy}(client, 'delete');\n` });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});
