/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

const joins = [
  { name: 'true ternary branch', expression: "client ? client : new Daytona({ apiKey: 'x' })" },
  { name: 'false ternary branch', expression: "client === undefined ? new Daytona({ apiKey: 'x' }) : client" },
  { name: 'logical conjunction', expression: 'client && client' },
] as const;

const consumers = [
  { name: 'direct parameter', body: 'function fetchSandbox(client?: Daytona) { const d = JOIN; return d?.get("id"); }', call: 'fetchSandbox(fake)' },
  { name: 'constructor parameter', body: 'class Service { readonly client; constructor(client?: Daytona) { this.client = JOIN; } run() { return this.client?.get("id"); } }', call: 'new Service(fake).run()' },
  { name: 'parameter property', body: 'class Service { constructor(private readonly client?: Daytona) {} run() { const d = JOIN; return d?.get("id"); } }', call: 'new Service(fake).run()', member: true },
] as const;

const cases = consumers.flatMap(consumer => joins.flatMap(join => [false, true].map(wrapped => ({
  name: `${consumer.name}, ${join.name}, ${wrapped ? 'wrapped' : 'plain'}`,
  body: consumer.body.replace('JOIN', wrapped
    ? `((${join.expression.replaceAll('client', 'member' in consumer ? '((this.client) as Daytona | undefined)' : '((client) as Daytona | undefined)')}) as Daytona | undefined)`
    : join.expression.replaceAll('client', 'member' in consumer ? 'this.client' : 'client')),
  call: consumer.call,
}))));

it.each(cases)('reports a moving fake consumer when flowing through $name', async ({ body, call }) => {
  // Given: cast syntax is intentional migration input, not a test-side type escape.
  const fake = '{ get: async (id: string) => ({ id }) }';
  const source = `import { Daytona } from '@daytonaio/sdk';
${body}
const fake = ${fake} as unknown as Daytona;
${call};`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  await expect(execute(source)).resolves.toEqual({ id: 'id' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: the tool must report the runtime-breaking contract, not silently succeed.
  const migrated = contents(root)['index.ts'] ?? '';
  expect(() => execute(migrated)).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', line: 4 }));
  await expect(execute(migrated.replace(fake, `{ v0: ${fake} }`))).resolves.toEqual({ id: 'id' });
});

it('keeps condition-only uses out of the value flow when both branches construct SDK clients', async () => {
  // Given
  const source = `import { Daytona } from '@daytonaio/sdk';
function fetchSandbox(client?: Daytona) { const d = client ? new Daytona() : new Daytona(); return d.get('id'); }
fetchSandbox({ get: () => 42 } as unknown as Daytona);`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(11);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(23);
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
