/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

it('reports a truncated consumer flow when a moved access lies beyond the analysis budget', async () => {
  // Given: the replacement works until a distant typed alias moves to v0.
  const aliases = Array.from({ length: 70 }, (_, index) => `const client${index}: Daytona = ${index === 0 ? 'client' : `client${index - 1}`};`).join('\n');
  const source = `import { Daytona } from '@daytonaio/sdk';
function consume(client: Daytona) { ${aliases} return client69.create(); }
consume({ create: () => 42 } as unknown as Daytona);`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const migrated = contents(root)['index.ts'] ?? '';
  expect(() => execute(migrated)).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
});

it('reports the caller argument when a cross-module consumer flow is truncated', async () => {
  // Given
  const aliases = Array.from({ length: 70 }, (_, index) => `const client${index}: Daytona = ${index === 0 ? 'client' : `client${index - 1}`};`).join('\n');
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'consumer.ts': `import type { Daytona } from '@daytonaio/sdk'; export function consume(client: Daytona) { ${aliases} return client69.create(); }`,
    'index.ts': "import { consume } from './consumer'; consume({ create: () => 42 });",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: the manual item belongs to the argument, not an offset in another file.
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts', line: 1 }));
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
});

it.each(['01', '1.0', '+1', '1e0', ' 1', '-0', '', '-1', '0.5'])('reviews a non-index array property when the key is %j', async key => {
  // Given: this named property is unrelated to the array's SDK elements.
  const source = `import { Daytona } from '@daytonaio/sdk';
function consume(clients: Daytona[] & Record<string, Daytona>) { return clients[${JSON.stringify(key)}].create(); }
Array.prototype[${JSON.stringify(key)}] = { create: () => 42 };
consume([new Daytona(), new Daytona()]);`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(() => execute(contents(root)['index.ts'] ?? '')).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', line: 4 }));
});

it.each([
  { key: '0', source: `import { Daytona } from '@daytonaio/sdk'; function consume(clients: Daytona[]) { return clients['0'].create(); } consume([new Daytona(), new Daytona()]);` },
  { key: '1', source: `import { Daytona } from '@daytonaio/sdk'; function consume(clients: Daytona[]) { return clients['1'].create(); } consume([new Daytona(), new Daytona()]);` },
])('accepts real SDK elements when the array index is canonical: $key', async ({ source }) => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(99);
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.each(['create', 'get'])('distinguishes root dispatch from v0 dispatch when calling %s', member => {
  // Given
  const rootCall = `new (require('@daytona/sdk').Daytona)().${member}()`;
  const v0Call = `new (require('@daytona/sdk').Daytona)().v0.${member}()`;
  // When
  const result = execute(rootCall);
  // Then
  expect(result).not.toBe(execute(v0Call));
});
