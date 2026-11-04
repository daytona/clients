/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  { parameters: '...clients: Daytona[]', arguments: 'getDaytona()', index: '0', reviews: 1 },
  { parameters: 'label: string, ...clients: Daytona[]', arguments: "'label', getDaytona()", index: '0', reviews: 1 },
  { parameters: '...clients: Daytona[]', arguments: 'new Daytona(), getDaytona()', index: '1', reviews: 1 },
  { parameters: '...clients: Daytona[]', arguments: 'getDaytona(), new Daytona()', index: '1', reviews: 0 },
  { parameters: '...clients: Daytona[]', arguments: 'getDaytona()', index: "'0'", reviews: 1 },
  { parameters: '...clients: Daytona[]', arguments: 'getDaytona()', index: 'selected', reviews: 1 },
])('selects rest-array consumer uses when $parameters receives $arguments at $index', async ({ parameters, arguments: args, index, reviews }) => {
  // Given: only the selected factory argument needs a replacement namespace.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': `import { Daytona } from '@daytonaio/sdk'; import { getDaytona } from './client';
      declare const selected: number;
      function consume(${parameters}) { return clients[${index}].create(); }
      consume(${args});`,
    'index.ts': "import { getDaytona } from './client'; jest.mocked(getDaytona).mockReturnValue({ create: () => 42 });",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['run.ts']).toContain(`clients[${index}].v0.create()`);
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string; readonly file: string }) =>
    item.kind === 'mock-target' && item.file === 'index.ts')).toHaveLength(reviews);
  expect(result.code, result.output).toBe(reviews ? 1 : 0);
});

it.each(['mockResolvedValue', 'mockResolvedValueOnce'])('reviews an unadapted async replacement when using %s', async setter => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export async function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; export async function run() { return (await getDaytona()).create(); }",
    'index.ts': `import { getDaytona } from './client'; jest.mocked(getDaytona).${setter}({ create: () => 42 });`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['run.ts']).toContain('(await getDaytona()).v0.create()');
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts' }));
});

it.each(['mockResolvedValue', 'mockResolvedValueOnce', 'mockRejectedValue', 'mockRejectedValueOnce'])('keeps a safe async setter quiet when using %s', async setter => {
  // Given: rejection reasons are not client replacements and must not be shape-checked.
  const replacement = setter.startsWith('mockRejected') ? "new Error('expected rejection')" : '{ v0: { create: () => 42 } }';
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export async function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; export async function run() { return (await getDaytona()).create(); }",
    'index.ts': `import { getDaytona } from './client'; jest.mocked(getDaytona).${setter}(${replacement});`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.each(['mockRejectedValue', 'mockRejectedValueOnce'])('follows a rejection setter when a later %s chain supplies an unsafe resolved value', async setter => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export async function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; export async function run() { return (await getDaytona()).create(); }",
    'index.ts': `import * as client from './client'; jest.spyOn(client, 'getDaytona').${setter}(new Error()).mockResolvedValue({ create: () => 42 });`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts' }));
});

it('reviews a factory mock when its alias declaration chain exceeds the analysis budget', async () => {
  // Given: the actual factory consumer moves, while the mock refers to a distant alias.
  const aliases = Array.from({ length: 70 }, (_, index) => `const factory${index} = ${index ? `factory${index - 1}` : 'getDaytona'};`).join('\n');
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; getDaytona().create();",
    'index.ts': `import { getDaytona } from './client';\n${aliases}\njest.mocked(factory69).mockReturnValue({ create: () => 42 });`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['run.ts']).toContain('getDaytona().v0.create()');
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts', line: 72 }));
});
