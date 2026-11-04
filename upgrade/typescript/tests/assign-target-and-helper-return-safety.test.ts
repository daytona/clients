/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { isObjectAssign } from '../src/intrinsics.js';
import { bindingIsStable } from '../src/stable-bindings.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['index.ts', 'index.js'].flatMap(file => [true, false].flatMap(types => [
  'Object.assign({ client }, options);',
  'Object.assign([client], options);',
  "Object['assign'](({ client }), options);",
].map(copy => ({ file, types, copy })))))('keeps a contained client proven after a fresh copy in $file with types=$types: $copy', async ({ file, types, copy }) => {
  // Given
  const root = temporaryProject({ [file]: `import { Daytona } from '@daytonaio/sdk';\nconst client = new Daytona();\nconst options = {};\n${copy}\nclient.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)[file]).toContain('client.v0.create();');
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it.each(['{ client: original }', '({ client: original })'])('invalidates properties on a fresh assign target: %s', target => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `const holder = Object.assign(${target}, replacement);`);
  const property = source.getFirstDescendantByKindOrThrow(SyntaxKind.PropertyAssignment);
  // When
  const stable = bindingIsStable(property);
  // Then
  expect(stable).toBe(false);
});

it.each([
  { code: 'Object.assign({}, {});', intrinsic: true },
  { code: "Object['assign']({}, {});", intrinsic: true },
  { code: 'const Object = { assign: () => ({}) }; Object.assign({}, {});', intrinsic: false },
  { code: 'function run(Object) { Object.assign({}, {}); }', intrinsic: false },
  { code: "import Object from './custom.js'; Object.assign({}, {});", intrinsic: false },
  { code: 'declare const Object: { assign: Function }; Object.assign({}, {});', intrinsic: false },
])('recognizes only the global intrinsic: $code', ({ code, intrinsic }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `export {}; ${code}`);
  const call = source.getFirstDescendantByKindOrThrow(SyntaxKind.CallExpression);
  // When
  const recognized = isObjectAssign(call);
  // Then
  expect(recognized).toBe(intrinsic);
});

it.each(['Object.assign', "Object['assign']"].flatMap(assign => [true, false].map(drop => ({ assign, drop }))))('preserves custom assign arguments for $assign with drop=$drop', async ({ assign, drop }) => {
  // Given
  const code = `const Object = { assign: (target, options) => options };\nnew Daytona(${assign}({}, { apiUrl: readUrl() }));`;
  const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
  const root = temporaryProject({ 'index.ts': prefix + code });
  // When
  const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(drop ? 0 : 2);
  expect(contents(root)['index.ts']).toBe((drop ? prefix.replace('@daytonaio/sdk', '@daytona/sdk') : prefix) + code);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: drop ? 'config-field' : 'custom-api-url' }),
  ]) });
});

it.each([
  "const unused = { method() { return { apiUrl: 'https://custom.example' }; } };",
  "const unused = { get config() { return { apiUrl: 'https://custom.example' }; } };",
  "const unused = { set config(value) { return { apiUrl: 'https://custom.example' }; } };",
  "class Unused { constructor() { return { apiUrl: 'https://custom.example' }; } }",
  "class Unused { method() { return { apiUrl: 'https://custom.example' }; } }",
  "function unused() { return { apiUrl: 'https://custom.example' }; }",
  "const unused = () => { return { apiUrl: 'https://custom.example' }; };",
  "const unused = function () { return { apiUrl: 'https://custom.example' }; };",
])('ignores returns inside unused nested callables: %s', async nested => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytonaio/sdk';\nfunction makeConfig() { ${nested} return { apiKey: 'key' }; }\nnew Daytona(makeConfig()).create();` });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain(nested);
  expect(contents(root)['index.ts']).toContain('new Daytona(makeConfig()).v0.create();');
});

it('still refuses custom URLs returned by the helper itself with override', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\nfunction makeConfig() { const key = 'key'; return { apiKey: key, apiUrl: 'https://custom.example' }; }\nnew Daytona(makeConfig()).create();" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
