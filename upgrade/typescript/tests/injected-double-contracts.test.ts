/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

const cases = [
  { name: 'private parameter property', body: 'class Service { constructor(private readonly client: Daytona) {} run() { return this.client.create(); } }', call: 'new Service(fake).run()' },
  { name: 'public parameter property', body: 'class Service { constructor(public readonly client: Daytona) {} run() { return this.client.create(); } }', call: 'new Service(fake).run()' },
  { name: 'constructor assignment', body: 'class Service { private readonly client: Daytona; constructor(client: Daytona) { this.client = client; } run() { return this.client.create(); } }', call: 'new Service(fake).run()' },
  { name: 'direct constructor', body: 'class Service { result: unknown; constructor(client: Daytona) { this.result = client.create(); } run() { return this.result; } }', call: 'new Service(fake).run()' },
  { name: 'constructor options', body: 'class Service { constructor(private readonly options: { client: Daytona }) {} run() { return this.options.client.create(); } }', call: 'new Service({ client: fake }).run()' },
  { name: 'destructured constructor', body: 'class Service { private readonly client: Daytona; constructor({ client }: { client: Daytona }) { this.client = client; } run() { return this.client.create(); } }', call: 'new Service({ client: fake }).run()' },
  { name: 'rest element', body: 'function run(...clients: Daytona[]) { return clients[0]!.create(); }', call: 'run(fake)' },
  { name: 'later rest element', body: 'function run(label: string, ...clients: Daytona[]) { return clients[1]!.create(); }', call: "run('label', new Daytona(), fake)" },
  { name: 'destructured parameter', body: 'function run({ client }: { client: Daytona }) { return client.create(); }', call: 'run({ client: fake })' },
  { name: 'renamed parameter', body: 'function run({ client: chosen }: { client: Daytona }) { return chosen.create(); }', call: 'run({ client: fake })' },
  { name: 'nested destructuring', body: 'function run({ options: { client } }: { options: { client: Daytona } }) { return client.create(); }', call: 'run({ options: { client: fake } })' },
  { name: 'option property', body: 'function run(options: { client: Daytona }) { return options.client.create(); }', call: 'run({ client: fake })' },
  { name: 'option alias', body: 'function run(options: { client: Daytona }) { const client = options.client; return client.create(); }', call: 'run({ client: fake })' },
  { name: 'array destructuring', body: 'function run([client]: Daytona[]) { return client!.create(); }', call: 'run([fake])' },
] as const;

it.each(cases.flatMap(shape => [' as unknown as Daytona', ''].map(cast => ({ ...shape, cast }))))('reports a changed contract when a fake$cast reaches a $name', async ({ body, call, cast }) => {
  // Given: injected doubles worked before migration; casts are intentional fixture syntax.
  const fake = '{ create: () => 42 }';
  const source = `import { Daytona } from '@daytonaio/sdk';\n${body}\nconst fake = ${fake}${cast};\n${call};\n`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: no unsupported adaptation, and no silent runtime break.
  const migrated = contents(root)['index.ts'] ?? '';
  expect(() => execute(migrated)).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', line: 4 }));
  expect(execute(migrated.replace(fake, `{ v0: ${fake} }`))).toBe(42);
});

it.each([
  { body: 'class Service { constructor(private readonly client: { create(): number }) {} run() { return this.client.create(); } }', call: 'new Service(fake).run()' },
  { body: 'function run(...clients: { create(): number }[]) { return clients[0]!.create(); }', call: 'run(fake)' },
  { body: 'function run({ client }: { client: { create(): number } }) { return client.create(); }', call: 'run({ client: fake })' },
  { body: 'function run(options: { client: { create(): number } }) { return options.client.create(); }', call: 'run({ client: fake })' },
])('preserves execution when the consumer does not move: $body', async ({ body, call }) => {
  // Given
  const source = `import type { Daytona } from '@daytonaio/sdk';\n${body}\nconst fake = { create: () => 42 } as unknown as Daytona;\n${call};\n`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(42);
  expect(result.code, result.output).toBe(0);
});

it.each(cases)('keeps production clients executable when supplied through a $name', async ({ body, call }) => {
  // Given: selected properties/elements, not their containers, carry the SDK identity.
  const source = `import { Daytona } from '@daytonaio/sdk';\n${body}\nconst fake = new Daytona();\n${call};\n`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(7);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(99);
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it('keeps the manual item on reruns when a constructor consumer already moved', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': "import type { Daytona } from '@daytonaio/sdk'; class Service { constructor(private readonly client: Daytona) {} run() { return this.client.create(); } } const fake = { create: () => 42 } as unknown as Daytona; new Service(fake).run();" });
  installFakeSdk(root);
  await invoke(root, ['--strict', '--json']);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)).toEqual(before);
});
