/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const build = "import { Daytona } from '@daytonaio/sdk';\nexport async function build(daytona: Daytona, name: string) { await daytona.snapshot.get(name); }\n";
const imports = "import { vi, it, expect } from 'vitest';\nimport { mock } from 'jest-mock-extended';\nimport { build } from './build';\n";
const dependency = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each([
  { init: '{} as any', expected: '{ v0: {} } as any' },
  { init: '{ snapshot: { get: vi.fn(), delete: vi.fn() }, create: vi.fn() }', expected: '{ v0: { snapshot: { get: vi.fn(), delete: vi.fn() }, create: vi.fn() } }' },
  { init: 'vi.fn()', expected: '{ v0: vi.fn() }' },
  { init: 'jest.fn()', expected: '{ v0: jest.fn() }' },
])('moves local double setup and assertions when all flows target client parameters: $init', async ({ init, expected }) => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'build.ts': build,
    'build.test.ts': `${imports}it('builds', async () => {
  const client = ${init};
  client.snapshot = { get: vi.fn(), delete: vi.fn() };
  client.snapshot.get = vi.fn();
  client.create = vi.fn();
  client.create.mockResolvedValue({});
  await build(client, 'base');
  expect(client.snapshot.delete).toHaveBeenCalled();
});\n`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['build.test.ts']).toBe(`${imports}it('builds', async () => {
  const client = ${expected};
  client.v0.snapshot = { get: vi.fn(), delete: vi.fn() };
  client.v0.snapshot.get = vi.fn();
  client.v0.create = vi.fn();
  client.v0.create.mockResolvedValue({});
  await build(client, 'base');
  expect(client.v0.snapshot.delete).toHaveBeenCalled();
});\n`);
});

it('reports an unresolved typed factory instead of wrapping its root type', async () => {
  // Given
  const root = temporaryProject({ 'package.json': dependency, 'build.ts': build,
    'build.test.ts': `${imports}const client = mock<AsyncDaytona>(); client.create = vi.fn(); build(client, 'base');\n` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['build.test.ts']).toContain('const client = mock<AsyncDaytona>(); client.create = vi.fn();');
});

it.each(['jest-mock-extended', 'vitest-mock-extended'].flatMap(module =>
  ['mock', 'mockDeep'].flatMap(factory => [
    { imported: `import { ${factory} as makeMock } from '${module}';`, call: 'makeMock()' },
    { imported: `import * as m from '${module}';`, call: `m.${factory}()` },
  ])))('moves setup and consumers when $call uses $imported', async ({ imported, call }) => {
  // Given
  const root = temporaryProject({
    'package.json': dependency,
    'build.ts': build,
    'build.test.ts': `${imported}\nimport { build } from './build';
const client = ${call};
client.create.mockResolvedValue({});
build(client, 'base');
build(${call}, 'direct');\n`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['build.test.ts']).toBe(`${imported}\nimport { build } from './build';
const client = { v0: ${call} };
client.v0.create.mockResolvedValue({});
build(client, 'base');
build({ v0: ${call} }, 'direct');\n`);
  expect(contents(root)['build.ts']).toContain('daytona.v0.snapshot.get(name)');
});

it.each([
  { imported: "import { other as mockDeep } from 'vitest-mock-extended';", call: 'mockDeep()' },
  { imported: "import { mockDeep as makeMock } from 'other-mocks';", call: 'makeMock()' },
  { imported: "import * as m from 'jest-mock-extended';", call: 'm.other()' },
  { imported: "import * as m from 'other-mocks';", call: 'm.mockDeep()' },
])('preserves doubles when $call uses an unrecognized export or module: $imported', async ({ imported, call }) => {
  // Given
  const test = `${imported}\nimport { build } from './build';\nconst client = ${call}; client.create = vi.fn(); build(client, 'base');\n`;
  const root = temporaryProject({ 'package.json': dependency, 'build.ts': build, 'build.test.ts': test });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['build.test.ts']?.replace(/\n\/\/ FIXME\(daytona-upgrade\):[^\n]*\n/g, ' ')).toBe(test);
});

it('moves literal keys when a literal is passed directly to a proven parameter', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'build.ts': build, 'build.test.ts': `${imports}build({ snapshot: { get: vi.fn() }, create: vi.fn() }, 'base');\n`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['build.test.ts']).toBe(`${imports}build({ v0: { snapshot: { get: vi.fn() }, create: vi.fn() } }, 'base');\n`);
});

it.each([
  'consume(client);',
  'const alias = client;',
  'return client;',
  'save({ client });',
  'const other: Other = client;',
  'const key = client[name];',
  'client = {} as any;',
  'client.close();',
])('preserves today’s behavior when a local double escapes client-only flows: %s', async extra => {
  // Given
  const test = `${imports}it('builds', async () => { ${extra.includes('client = {}') ? 'let' : 'const'} client = {} as any; client.create = vi.fn(); await build(client, 'base'); ${extra} });\n`;
  const root = temporaryProject({ 'package.json': dependency, 'build.ts': build, 'build.test.ts': test });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['build.test.ts']?.replace(/\n\/\/ FIXME\(daytona-upgrade\):[^\n]*\n/g, ' ')).toBe(test);
});

it.each([
  'function build(client: Other, name: string) {}',
  'function build(client: any, name: string) {}',
  'function build(client: Sandbox, name: string) {}',
  'declare const build: unknown;',
])('does not infer root clients from an unproven parameter: %s', async declaration => {
  // Given
  const test = `import type { Daytona } from '@daytonaio/sdk';\nimport { vi } from 'vitest';\n${declaration}\nconst client = { snapshot: { get: vi.fn() } }; build(client, 'base');\n`;
  const root = temporaryProject({ 'package.json': dependency, 'build.test.ts': test });
  installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).not.toBe(2);
  expect(contents(root)['build.test.ts']).toBe(test.replace('@daytonaio/sdk', '@daytona/sdk'));
});

it.each(['getShared()', 'shared', '{ ...shared }', '{ [name]: vi.fn() }', '{ v0: {} }'])('does not move nonlocal or ambiguous initializers: %s', async init => {
  // Given
  const test = `${imports}const client = ${init}; build(client, 'base');\n`;
  const root = temporaryProject({ 'package.json': dependency, 'build.ts': build, 'build.test.ts': test });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['build.test.ts']?.replace(/\n\/\/ FIXME\(daytona-upgrade\):[^\n]*\n/g, ' ')).toBe(test);
});

it('keeps migrated doubles byte-stable on reruns', async () => {
  // Given
  const root = temporaryProject({ 'package.json': dependency, 'build.ts': build, 'build.test.ts': `${imports}const client = { snapshot: { get: vi.fn() } }; build(client, 'base');\n` });
  installFakeSdk(root);
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});
