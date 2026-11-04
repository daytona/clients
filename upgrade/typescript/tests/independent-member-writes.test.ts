/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const writes = [
  "Reflect.set(TARGET, 'create', stub);",
  "Object.defineProperty(TARGET, 'create', {value: stub});",
  "Reflect.defineProperty(TARGET, 'create', {value: stub});",
  'Object.defineProperties(TARGET, {create: {value: stub}});',
  'Object.assign(TARGET, {create: stub});',
  "const {set: patch} = Reflect; patch(TARGET, 'create', stub);",
] as const;

it.each([false, true].flatMap(types => ['client', 'Daytona.prototype'].flatMap(target =>
  writes.map(write => ({ types, target, write: write.replace('TARGET', target) })))))
('reviews replaced members when types=$types target=$target: $write', async ({ types, write }) => {
  // Given a stub installed on the root instance or its class prototype.
  const root = temporaryProject({ 'app.js': prefix +
    `const client = new Daytona(); const stub = () => 'local'; ${write}\nclient.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then both the write and affected call require review, never silently bypassing the stub.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  expect(contents(root)['app.js']).not.toContain('client.v0.create()');
  const items: readonly { readonly kind: string }[] = JSON.parse(result.output).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(contents(root)['app.js'] ?? '', { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  class Daytona { create() { return 'sdk'; } }
  expect(runInNewContext(javascript, { require: () => ({ Daytona }), exports: {} })).toBe('local');
});

it.each([
  "Reflect.set(other, 'create', stub);",
  "Object.defineProperty(other, 'create', {value: stub});",
  "Reflect.set(client, 'unrelated', stub);",
  "Object.defineProperty(client, 'unrelated', {value: stub});",
  "Reflect.set(Daytona.prototype, 'unrelated', stub);",
])('preserves an unaffected client method when $write', async write => {
  // Given
  const root = temporaryProject({ 'app.js': prefix +
    `const client = new Daytona(); const other = new Daytona(); const stub = () => 'local'; ${write} client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then receiver and member identity, not a shared SDK declaration, scope the fence.
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('client.v0.create()');
});
