/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const cases = [
  { name: 'explicit receiver', setup: '', write: "Reflect.set({}, 'create', stub, client);" },
  { name: 'aliased explicit receiver', setup: 'const receiver = client;', write: "Reflect.set({}, 'create', stub, receiver);" },
  { name: 'destructured setter receiver', setup: 'const {set: patch} = Reflect;', write: "patch({}, 'create', stub, client);" },
  { name: 'prototype alias', setup: 'const proto = Daytona.prototype;', write: "Reflect.set(proto, 'create', stub);" },
  { name: 'prototype alias descriptor', setup: 'const proto = Daytona.prototype;', write: "Object.defineProperty(proto, 'create', {value: stub});" },
  { name: 'nested prototype alias', setup: 'const proto = Daytona.prototype; const alias = proto;', write: "Object.assign(alias, {create: stub});" },
] as const;

it.each([false, true].flatMap(types => cases.map(test => ({ types, ...test }))))
('retains a replacement through $name when types=$types', async ({ types, setup, write }) => {
  // Given a reflective replacement of the root method, including an explicit set receiver.
  const root = temporaryProject({ 'app.js': prefix +
    `const client = new Daytona(); const stub = () => 'local'; ${setup} ${write}\nclient.create();` });
  if (types) installFakeSdk(root);
  // When the user upgrades in strict mode.
  const result = await invoke(root, ['--strict', '--json']);
  // Then the write and call require review and the replacement still executes.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  const text = contents(root)['app.js'] ?? '';
  expect(text).not.toContain('client.v0.create()');
  const items: readonly { readonly kind: string }[] = JSON.parse(result.output).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  class Daytona { create() { return 'sdk'; } }
  expect(runInNewContext(javascript, { require: () => ({ Daytona }), exports: {} })).toBe('local');
});

it.each([false, true].flatMap(types => [
  "Reflect.set(Parent.prototype, 'create', stub);",
  "Object.defineProperty(Parent.prototype, 'create', {value: stub});",
  "const proto = Parent.prototype; Object.assign(proto, {create: stub});",
  "Reflect.set({}, 'create', stub, Parent.prototype);",
].map(write => ({ types, write }))))
('retains inherited replacements across all base layers when types=$types: $write', async ({ types, write }) => {
  // Given a grandchild inheriting a reflectively replaced method from Parent.
  const root = temporaryProject({ 'app.js': prefix +
    'class Parent extends Daytona {} class Middle extends Parent {} class Child extends Middle {}\n' +
    `const client = new Child(); const stub = () => 'inherited'; ${write}\nclient.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then migration does not bypass the inherited replacement.
  expect(result.code).toBe(1);
  const text = contents(root)['app.js'] ?? '';
  expect(text).not.toContain('client.v0.create()');
  const items: readonly { readonly kind: string }[] = JSON.parse(result.output).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  class Daytona { create() { return 'sdk'; } }
  expect(runInNewContext(javascript, { require: () => ({ Daytona }), exports: {} })).toBe('inherited');
});

it.each([false, true].flatMap(types => [
  "Reflect.set(client, 'create', stub, other);",
  "Reflect.set(Daytona.prototype, 'create', stub, other);",
  "Reflect.set({}, 'unrelated', stub, client);",
  "const proto = Daytona.prototype; Reflect.set(proto, 'unrelated', stub);",
  "Reflect.set(Other.prototype, 'create', stub);",
  "const proto = Other.prototype; Object.assign(proto, {create: stub});",
  "Reflect.set(Child.prototype, 'create', stub);",
].map(write => ({ types, write }))))
('rewrites unaffected receiver members when types=$types: $write', async ({ types, write }) => {
  // Given a write to another receiver, sibling/descendant prototype, or member.
  const root = temporaryProject({ 'app.js': prefix +
    'class Parent extends Daytona {} class Child extends Parent {} class Other extends Daytona {}\n' +
    `const client = new Parent(); const other = new Daytona(); const stub = () => 'local'; ${write}\nclient.create();` });
  if (types) installFakeSdk(root);
  // When
  await invoke(root, ['--json']);
  // Then the unaffected method retains its automatic namespace upgrade.
  expect(contents(root)['app.js']).toContain('client.v0.create()');
});
