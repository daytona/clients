/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { ts } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const modes = ['js', 'ts'].flatMap(extension => [false, true].map(types => ({ extension, types })));
const writes = [
  "const R = Reflect; R.set(target, 'client', local);",
  "const { set } = Reflect; set(target, 'client', local);",
  "const { set: write } = Reflect; write(target, 'client', local);",
  "const write = Reflect.set; write(target, 'client', local);",
  "const R = Reflect; const S = R; S['set'](target, 'client', local);",
  "const { defineProperty: dp } = Object; dp(target, 'client', {value: local});",
  "const O = Object; O.defineProperty(target, 'client', {value: local});",
  "const { defineProperty: dp } = Reflect; dp(target, 'client', {value: local});",
  'const { defineProperties: dp } = Object; dp(target, {client: {value: local}});',
  'const { assign: write } = Object; write(target, {client: local});',
  'const O = Object; O.assign(target, {client: local});',
  "globalThis.Reflect.set(target, 'client', local);",
  "globalThis.Object.defineProperty(target, 'client', {value: local});",
] as const;

it.each(modes.flatMap(mode => writes.flatMap(write => [false, true].map(opaque => ({ ...mode, write, opaque })))))
('requires review for aliased mutation in $extension, types=$types, opaque=$opaque: $write', async ({ extension, types, write, opaque }) => {
  // Given: both a known target and a widened parameter can replace the proven field.
  const file = `app.${extension}`;
  const program = opaque ? `function replace(target${extension === 'ts' ? ': object' : ''}) { ${write} } replace(service);` : `const target = service; ${write}`;
  const root = temporaryProject({ [file]: "import { Daytona } from '@daytonaio/sdk';\n" +
    "const local = {create(){return 'local-cache'}}; const service = {client: new Daytona()};\n" + program + '\nservice.client.create();' });
  if (types) installFakeSdk(root);
  class Daytona {}
  // When
  const result = await invoke(root, ['--json']);
  // Then: the local replacement stays executable and is not silently migrated.
  const after = contents(root)[file] ?? '';
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(after).toContain('FIXME(daytona-upgrade)');
  expect(after).not.toContain('service.client.v0.create()');
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona }), exports: {} })).toBe('local-cache');
});

it.each(modes.flatMap(mode => [
  "const other = {client: new Daytona()}; const R = Reflect; R.set(other, 'client', local);",
  "const { defineProperty: dp } = Object; dp({}, 'client', {value: local});",
].map(write => ({ ...mode, write }))))
('keeps a distinct field proven in $extension with types=$types: $write', async ({ extension, types, write }) => {
  // Given
  const file = `app.${extension}`;
  const root = temporaryProject({ [file]: "import { Daytona } from '@daytonaio/sdk';\n" +
    'const local = {}; const service = {client: new Daytona()}; ' + write + '\nservice.client.create();' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then: unrelated writes must not erase a valid proof.
  expect(result.code).toBe(0);
  expect(contents(root)[file]).toContain('service.client.v0.create()');
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each(modes.flatMap(mode => [
  "const { set } = Reflect; function replace(target) { set(target, 'other', local); } replace(service);",
  'const { assign } = Object; function replace(target) { assign(target, {other: local}); } replace(service);',
].map(write => ({ ...mode, write }))))
('reviews holder fields after a call in $extension with types=$types: $write', async ({ extension, types, write }) => {
  // Given: passing a holder to any call drops all property proofs, even for an unrelated write.
  const file = `app.${extension}`;
  const root = temporaryProject({ [file]: "import { Daytona } from '@daytonaio/sdk';\n" +
    'const local = {}; const service = {client: new Daytona()}; ' + write + '\nservice.client.create();' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)[file]).not.toContain('service.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});
