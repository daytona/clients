/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { rmSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\nconst daytona = new Daytona();\n";

it.each([false, true].flatMap(typed => [
  'daytona[name]()', 'daytona?.[name]()', "daytona[flag ? 'create' : 'get']()", 'daytona[`cre${part}ate`]()',
].map(expression => ({ typed, expression }))))('reports computed client access with typed=$typed: $expression', async ({ typed, expression }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + expression });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toHaveLength(1);
  expect(contents(root)['index.ts']).toContain(expression);
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade): review dynamic member lookup on `daytona`; SDK 2.x moved client methods to .v0');
});

it('leaves generic computed receivers and migrated namespaces clean without receiver evidence', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `declare const client: unknown;
client[name]();
const other = { create() {} }; other[name]();
daytona.v0[name](); daytona['v0'][name](); daytona.v2[name]();
` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['index.ts']).toContain('client[name]();');
  expect(contents(root)['index.ts']).not.toContain('FIXME(daytona-upgrade)');
});

it.each([false, true])('keeps literal service access clean across an untyped rerun after typed=%s', async typed => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "daytona['snapshot'].list();\ndaytona[`volume`].get();\n" });
  if (typed) installFakeSdk(root);
  const first = await invoke(root, ['--json']);
  expect(JSON.parse(first.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
  expect(contents(root)['index.ts']).toContain("daytona.v0['snapshot'].list()");
  expect(contents(root)['index.ts']).toContain('daytona.v0[`volume`].get()');
  if (typed) rmSync(join(root, 'node_modules'), { recursive: true });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
  expect(contents(root)).toEqual(before);
});

it.each([false, true])('moves membership checks on proven clients with typed=%s', async typed => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "'create' in daytona; 'snapshot' in daytona; 'close' in daytona;" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['index.ts']).toContain("'create' in daytona.v0; 'snapshot' in daytona.v0; 'close' in daytona;");
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it('leaves a generic unknown membership receiver clean without Daytona evidence', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "declare const client: unknown; 'create' in client; 'create' in { create() {} };" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'types-unresolved' }),
    expect.objectContaining({ kind: 'manual-dependency' }),
  ]));
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['index.ts']).toContain("'create' in client;");
});

it.each([false, true])('handles super client calls with typed=%s', async typed => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\nclass Custom extends Daytona { run() { return super.create(); } }" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const fixmes = JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme');
  expect(fixmes).toHaveLength(typed ? 0 : 1);
  expect(contents(root)['index.ts']).toContain(typed ? 'super.v0.create()' : 'super.create()');
});

it.each([false, true].flatMap(typed => [
  "const { Daytona } = await import('@daytonaio/sdk'); const d = new Daytona(); d.create();",
  "const sdk = await import('@daytonaio/sdk'); const d = new sdk.Daytona(); d.create();",
  "const { Daytona: D } = await import('@daytonaio/sdk'); const d = new D(); d.create();",
  "const d = new (await import('@daytonaio/sdk')).Daytona(); d.create();",
].map(code => ({ typed, code }))))('moves dynamic-import clients with typed=$typed: $code', async ({ typed, code }) => {
  // Given
  const root = temporaryProject({ 'index.ts': code });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['index.ts']).toContain('d.v0.create()');
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it('does not prove a client from an unawaited dynamic import', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "const sdk = import('@daytonaio/sdk'); const d = new sdk.Daytona(); d.create();" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain('d.create()');
});

it('does not duplicate dynamic lookup or membership comments on reruns', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "daytona[name]();\ndeclare const client: unknown;\n'create' in client;\n" });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});
