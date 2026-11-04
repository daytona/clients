/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { bindingKey } from '../src/binding-key.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  String.raw`const { Dayt\u006fna: D } = require('@daytonaio/sdk');`,
  String.raw`import * as sdk from '@daytonaio/sdk'; const { Dayt\u{6f}na: D } = sdk;`,
  String.raw`const { Dayt\u006fna } = require('@daytonaio/sdk'); const D = Daytona;`,
])('routes clients imported through Unicode-escaped binding keys: %s', async code => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': `${code}\nconst daytona = new D(); daytona.create();`,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => ['fixme', 'private-import'].includes(entry.kind))).toEqual([]);
  expect(contents(root)['index.ts']).toContain('daytona.v0.create()');
});

it.each([String.raw`cr\u0065ate: make`, String.raw`cr\u{65}ate: make`, String.raw`cr\u0065ate`])('decodes identifier binding keys: %s', binding => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `const { ${binding} } = daytona;`);
  // When
  const key = bindingKey(source.getFirstDescendantByKindOrThrow(SyntaxKind.BindingElement));
  // Then
  expect(key).toBe('create');
});

it.each([
  "const { ['Daytona']: D, ['Sandbox']: S } = require('@daytonaio/sdk');",
  "import * as sdk from '@daytonaio/sdk'; const { ['Daytona']: D, ['Sandbox']: S } = sdk;",
  "const sdk = require('@daytonaio/sdk'); const { ['Daytona']: D, ['Sandbox']: S } = sdk;",
])('keeps literal computed root exports at the root: %s', async code => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': `${code}\nconst daytona = new D(); daytona.create();`,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => ['fixme', 'private-import'].includes(entry.kind))).toEqual([]);
  expect(contents(root)['index.ts']).toContain("{ ['Daytona']: D }");
  expect(contents(root)['index.ts']).toContain("{ ['Sandbox']: S }");
  expect(contents(root)['index.ts']).toContain('daytona.v0.create()');
});

it.each([
  "const { [key]: D } = require('@daytonaio/sdk');",
  "import * as sdk from '@daytonaio/sdk'; const { [key]: D } = sdk;",
  "const sdk = require('@daytonaio/sdk'); const { [key]: D } = sdk;",
])('reports unknown computed keys without routing them to v0: %s', async code => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': code,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
  expect(contents(root)['index.ts']).toContain('{ [key]: D }');
  expect(contents(root)['index.ts']).not.toContain('@daytona/sdk/v0');
});
