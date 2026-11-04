/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const modes = ['js', 'ts'].flatMap(extension => [false, true].map(types => ({ extension, types })));

it.each(modes.flatMap(mode => ['return this;', 'return;', 'return (this);'].map(statement => ({ ...mode, statement }))))
('migrates a self-preserving constructor in $extension with types=$types: $statement', async ({ extension, types, statement }) => {
  // Given
  const file = `app.${extension}`;
  const root = temporaryProject({ [file]: "import { Daytona } from '@daytonaio/sdk';\n" +
    `class Cached extends Daytona { constructor() { super(); ${statement} } } const d = new Cached(); d.create();` });
  if (types) installFakeSdk(root);
  class Daytona { readonly v0 = { create: () => 'sdk-client' }; }
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const after = contents(root)[file] ?? '';
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(after).toContain('d.v0.create()');
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona }), exports: {} })).toBe('sdk-client');
});

it.each(modes.flatMap(mode => [
  'return cache;', 'if (cache) return cache;',
].flatMap(statement => [false, true].map(inherited => ({ ...mode, statement, inherited })))))
('flags a replacement constructor in $extension, types=$types, inherited=$inherited: $statement', async ({ extension, types, statement, inherited }) => {
  // Given: a short receiver name must not hide SDK subclass provenance.
  const file = `app.${extension}`;
  const root = temporaryProject({ [file]: "import { Daytona } from '@daytonaio/sdk';\n" +
    `const cache = {create(){return 'local-cache'}}; class Cached extends Daytona { constructor() { super(); ${statement} } } ` +
    `${inherited ? 'class Child extends Cached {}' : ''} const d = new ${inherited ? 'Child' : 'Cached'}(); d.create();` });
  if (types) installFakeSdk(root);
  class Daytona {}
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const after = contents(root)[file] ?? '';
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(after).toContain('FIXME(daytona-upgrade)');
  expect(after).not.toContain('d.v0.create()');
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona }), exports: {} })).toBe('local-cache');
});
