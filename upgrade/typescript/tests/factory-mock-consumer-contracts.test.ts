/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { executeFactory } from './factory-mock-runtime.js';

const replacements = [
  { name: 'auto-mock', setup: "TEST.mock('./client'); (getDaytona as jest.Mock).mockReturnValue({ create: TEST.fn(() => 42) });", line: 3 },
  { name: 'module factory', setup: "TEST.mock('./client', () => ({ getDaytona: () => ({ create: TEST.fn(() => 42) }) }));", line: 3 },
  { name: 'cast return value', setup: "TEST.mock('./client');\n(getDaytona as jest.Mock).mockReturnValue({ create: TEST.fn(() => 42) });", line: 4 },
  { name: 'mocked return value', setup: "TEST.mock('./client');\nTEST.mocked(getDaytona).mockReturnValue({ create: TEST.fn(() => 42) });", line: 4 },
  { name: 'mock implementation', setup: "TEST.mock('./client');\nTEST.mocked(getDaytona).mockImplementation(() => ({ create: TEST.fn(() => 42) }));", line: 4 },
  { name: 'spy return value', setup: "TEST.spyOn(clientModule, 'getDaytona').mockReturnValue({ create: TEST.fn(() => 42) });", line: 3 },
  { name: 'spy implementation', setup: "TEST.spyOn(clientModule, 'getDaytona').mockImplementation(() => ({ create: TEST.fn(() => 42) }));", line: 3 },
] as const;
const cases = ['jest', 'vi'].flatMap(tester => replacements.map(replacement => ({
  ...replacement, tester, setup: replacement.setup.replaceAll('TEST', tester),
})));

const consumers = [
  { flow: 'direct result', source: "import { getDaytona } from './client'; export function run() { return getDaytona().create(); }" },
  { flow: 'assigned result', source: "import { getDaytona } from './client'; export function run() { const sdk = getDaytona(); return sdk.create(); }" },
  { flow: 'namespace result', source: "import * as client from './client'; export function run() { return client.getDaytona().create(); }" },
  { flow: 'forwarded argument', source: "import { getDaytona } from './client'; import type { Daytona } from '@daytonaio/sdk'; function consume(sdk: Daytona) { return sdk.create(); } export function run() { return consume(getDaytona()); }" },
] as const;

it.each(cases.flatMap(replacement => consumers.map(consumer => ({ ...replacement, ...consumer }))))('reports $tester $name when a $flow consumer moves', async ({ setup, line, source }) => {
  // Given: replacement factory results work on 0.x, but lack v0.
  const files = {
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': source,
    'index.ts': `import { getDaytona } from './client';\nimport * as clientModule from './client';\n${setup}\nimport { run } from './run'; export const result = run();`,
  };
  const root = temporaryProject(files);
  installFakeSdk(root);
  expect(executeFactory(files)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: the unresolved runtime break cannot be silent or exit successfully.
  expect(() => executeFactory(contents(root))).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts', line }));
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts', line: 3 }));
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
});

it.each(['undefined', '{}', '{ create: {} }'])('reports an incomplete v0 shape when its value is %s', async shape => {
  // Given: a v0 key alone is not an adapted replacement for a moving create call.
  const files = {
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; export function run() { return getDaytona().create(); }",
    'index.ts': `jest.mock('./client', () => ({ getDaytona: () => ({ create: () => 42, v0: ${shape} }) })); import { run } from './run'; export const result = run();`,
  };
  const root = temporaryProject(files);
  installFakeSdk(root);
  expect(executeFactory(files)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(() => executeFactory(contents(root))).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts' }));
});

it.each(['mockReturnValue({ v0: { create: () => 42 } })', 'mockImplementation(() => ({ v0: { create: () => 42 } }))'])('accepts an adapted spy replacement when using %s', async setter => {
  // Given: the replacement already implements the consumer's migrated shape.
  const files = {
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; export function run() { return getDaytona().create(); }",
    'index.ts': `import * as client from './client'; jest.spyOn(client, 'getDaytona').${setter}; import { run } from './run'; export const result = run();`,
  };
  const root = temporaryProject(files);
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(executeFactory(contents(root))).toBe(42);
});

it('keeps SDK factory mocks quiet when no result member is rewritten', async () => {
  // Given: an SDK factory is called, but its result is returned untouched.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'index.ts': "import { getDaytona } from './client'; jest.mock('./client'); (getDaytona as jest.Mock).mockReturnValue({ create: jest.fn(() => 42) }); export const result = getDaytona();",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(executeFactory(contents(root))).toMatchObject({ create: expect.any(Function) });
});

it.each(['jest', 'vi'])('accepts a literal v0-shaped module factory when using %s', async tester => {
  // Given: the replacement explicitly implements the migrated shape.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export const getDaytona = () => new Daytona();",
    'run.ts': "import { getDaytona as factory } from './client'; export function run() { return factory().create(); }",
    'index.ts': `${tester}.mock('./client', () => ({ getDaytona: ${tester}.fn(() => ({ v0: { create: () => 42 } })) })); import { run } from './run'; export const result = run();`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(executeFactory(contents(root))).toBe(42);
});

it.each(cases)('keeps $tester $name quiet when factory consumers do not move', async ({ setup }) => {
  // Given: a similarly named, unrelated project factory.
  const files = {
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': 'export function getDaytona() { return { create: () => 7 }; }',
    'run.ts': "import { getDaytona } from './client'; export function run() { return getDaytona().create(); }",
    'index.ts': `import { getDaytona } from './client';\nimport * as clientModule from './client';\n${setup}\nimport { run } from './run'; export const result = run();`,
  };
  const root = temporaryProject(files);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(executeFactory(contents(root))).toBe(42);
});
