/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  'function replace() { client = other; }\nlet client = new Daytona();\nreplace();\nclient.create();',
  'function replace() { client ??= other; }\nlet client = new Daytona();\nreplace();\nclient.create();',
])('leaves clients unproven when an earlier function writes the binding: %s', async source => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" + source });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('client.v0.create()');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it.each(['index.ts', 'replace.ts'])('preserves custom loader package strings when ambient require is reassigned in %s', async file => {
  // Given
  const source = "const sdk = require('@daytonaio/sdk');\nsdk;\n";
  const assignment = 'require = customLoader;\n';
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"types":["node"]}}',
    'node_modules/@types/node/package.json': '{"name":"@types/node","types":"index.d.ts"}',
    'node_modules/@types/node/index.d.ts': 'declare var require: (name: string) => unknown;\ndeclare const customLoader: typeof require;\n',
    'index.ts': source,
    [file]: file === 'index.ts' ? assignment + source : assignment,
  });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(file === 'index.ts' ? assignment + source : source);
});

it('migrates inherited missing root members when the installed SDK is 2.x while preserving overrides', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'node_modules/@daytona/sdk/package.json': '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}',
    'node_modules/@daytona/sdk/index.d.ts': 'export declare class Daytona { readonly v0: { create(): void; delete(): void }; readonly v2: object; }\n',
    'index.ts': "import { Daytona } from '@daytona/sdk';\nclass Middle extends Daytona {}\nclass Custom extends Middle { delete() { return 'local'; } }\nconst client = new Custom();\nclient.create();\nclient.delete();\n",
  });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('client.v0.create();');
  expect(contents(root)['index.ts']).toContain('client.delete();');
  expect(contents(root)['index.ts']).not.toContain('client.v0.delete();');
});
