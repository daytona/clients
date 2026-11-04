/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { ts } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\nconst d = new Daytona();\n";
it.each([
  "let client=d; client={delete(){return 'local'}}; client.delete();",
  'let client=d; if (condition) client=other; client.create();',
  'let client=d; for (client of values) {} client.create();',
  'let client=d; [client]=values; client.create();',
  'let client=d; function replace() { client=other; } client.create();',
  'function f(client: Daytona) { client=other; client.create(); }',
])('leaves reassigned clients unproven when the scope contains %s', async source => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + source });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  const code = (contents(root)['index.ts'] ?? '').split('\n').filter(line => !line.trimStart().startsWith('//')).join('\n');
  expect(code).not.toContain('client.v0.');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});
it('preserves local loader behavior when require is lexically shadowed', async () => {
  // Given
  const source = "function f(require) { const {Daytona} = require('@daytonaio/sdk'); return new Daytona().delete(); }\nf(() => ({Daytona: class {delete(){return 'local'}}}));\n";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.js': source });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.js']).toBe(source);
  expect(runInNewContext(contents(root)['index.js'] ?? '')).toBe('local');
});
it('recognizes the Node loader when require comes from an aliased createRequire import', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { createRequire as makeRequire } from 'node:module';\nconst require = makeRequire(import.meta.url);\nconst {Daytona} = require('@daytonaio/sdk');\nnew Daytona().create();\n" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain("require('@daytona/sdk')");
  expect(contents(root)['index.ts']).toContain('new Daytona().v0.create()');
});
it('initializes the v0 namespace before resource uses in the same CommonJS declaration', async () => {
  // Given
  const root = temporaryProject({ 'index.cjs': "const first = 1, sdk = require('@daytonaio/sdk'), Box = sdk.Sandbox;\nBox;\n" });
  class Sandbox {}
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(runInNewContext(contents(root)['index.cjs'] ?? '', { require: () => ({ Sandbox }) })).toBe(Sandbox);
});
it('migrates import-equals clients and resource namespaces without a manifest', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': 'import SDK = require("@daytonaio/sdk")\nconst Box = SDK.Sandbox;\nnew SDK.Daytona().create();\n' });
  class Sandbox {}
  class Daytona { readonly v0 = { create: () => 42 }; }
  // When
  const result = await invoke(root);
  // Then
  const after = contents(root)['index.ts'] ?? '';
  expect(result.code).toBe(0);
  expect(after).toContain('import SDKV0 = require("@daytona/sdk/v0")\n');
  expect(after).toContain('new SDK.Daytona().v0.create()');
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona, Sandbox }), exports: {} })).toBe(42);
});
it('moves inherited SDK methods but preserves subclass overrides when SDK types resolve', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + 'class Middle extends Daytona {}\nclass Custom extends Middle { delete() { return "local"; } }\nconst client = new Custom();\nclient.create();\nclient.delete();\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('client.v0.create();');
  expect(contents(root)['index.ts']).toContain('client.delete();');
});
it('reports an ambiguous resolved member when SDK and non-SDK providers coexist', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + 'function f(value: Daytona | { create(): void }) { value.create(); }\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('value.v0.create();');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});
it('migrates proven literal element methods without diagnosing generic unknown parameters', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "d['create']();\nd?.['get']('id');\nfunction f(client) { client['delete'](); }\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain("d.v0['create']();");
  expect(contents(root)['index.ts']).toContain("d?.v0['get']('id');");
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['index.ts']).toContain("function f(client) { client['delete'](); }");
});
