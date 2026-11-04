/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { expect, it } from 'vitest';
import { installCommand } from '../src/discovery.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

it('leaves imported config sources untouched with an explicit override', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytonaio/sdk":"0.224.0"}}\n',
    'config.ts': "export const config = { apiUrl: 'https://app.daytona.io/api' };\n",
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\nimport { config } from './config.js';\nnew Daytona(config);\n",
  });
  // When
  const result = await invoke(root, ['--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['config.ts']).toBe("export const config = { apiUrl: 'https://app.daytona.io/api' };\n");
  expect(contents(root)['index.ts']).toBe("import { Daytona } from '@daytona/sdk';\nimport { config } from './config.js';\nnew Daytona(config);\n");
});
it('refuses before edits when a custom URL comes from another module', async () => {
  // Given
  const root = temporaryProject({
    'config.ts': "export const config = { apiUrl: 'https://custom.example' };\n",
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\nimport { config } from './config.js';\nnew Daytona(config);\n",
  });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ file: 'index.ts', kind: 'custom-api-url' })]) });
});
it('adds v0 for the installed 2.x root when the resource member is absent', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk';\nfunction make() { return new Daytona(); }\nconst arbitrary = make();\narbitrary.create();\n",
    'node_modules/@daytona/sdk/package.json': '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}',
    'node_modules/@daytona/sdk/index.d.ts': 'export declare class Daytona { v0: { create(): Promise<string> }; close(): void; }',
  });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('arbitrary.v0.create();');
});
it('tracks unannotated this properties when their assignment constructs Daytona', async () => {
  // Given
  const root = temporaryProject({ 'index.js': "import { Daytona } from '@daytona/sdk';\nclass S { constructor() { this.client = new Daytona(); } work() { return this.client.create(); } }\n" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.js']).toContain('this.client.v0.create()');
});
it('finds sandbox calls when list items arrive through for-await', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\nfor await (const box of d.v0.list()) {\n  await box.delete();\n}\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], suggestionMapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'suggestion', suggestion: expect.objectContaining({ id: 'sandbox.delete', v0: 'box.delete' }) })]) });
});
it('does not suggest changes when a sandbox is explicitly v2', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\nimport type { Sandbox } from '@daytona/sdk/v2';\nconst d = new Daytona();\nconst sandbox = await d.v2.create();\nawait sandbox.delete();\nasync function f(box: Sandbox) { await box.delete(); }\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], suggestionMapping);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.not.arrayContaining([expect.objectContaining({ kind: 'suggestion' })]) });
});
it('groups an aliased child receiver with its producing workflow', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\nconst box = await d.v0.create();\nconst fs = box.fs;\nawait fs.downloadFile('/file');\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], suggestionMapping);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'workflow', message: expect.stringContaining('downloadFile') })]) });
});
it.each([{ file: 'yarn.lock', manager: 'yarn' }, { file: 'pnpm-lock.yaml', manager: 'pnpm' }, { file: 'bun.lock', manager: 'bun' }, { file: 'package-lock.json', manager: 'npm' }])('chooses $manager when its lockfile is nearest', ({ file, manager }) => {
  // Given
  const root = temporaryProject({ [file]: '', 'sub/package.json': '{}' });
  // When
  const command = installCommand(join(root, 'sub'), root);
  // Then
  expect(command).toEqual({ cwd: root, manager, args: ['install'] });
});
it('discovers multiple configs plus uncovered files when processing a workspace', async () => {
  // Given
  const code = "import { Daytona } from '@daytona/sdk';\nnew Daytona().create();\n";
  const root = temporaryProject({
    'a/tsconfig.json': '{"files":["index.ts"]}', 'a/index.ts': code,
    'b/jsconfig.json': '{"files":["index.js"]}', 'b/index.js': code,
    'outside.mts': code,
    'a/package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"}}',
    'b/package.json': '{"devDependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  for (const file of ['a/index.ts', 'b/index.js', 'outside.mts']) expect(contents(root)[file]).toContain('new Daytona().v0.create()');
  for (const file of ['a/package.json', 'b/package.json']) expect(contents(root)[file]).toContain('"@daytona/sdk":"^2.0.0"');
});
it('preserves BOM, CRLF and unrelated whitespace when editing source offsets', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "\uFEFFimport { Daytona } from '@daytona/sdk';\r\n\tconst d=new Daytona();  \r\n\tawait d.create();\r\n" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toBe("\uFEFFimport { Daytona } from '@daytona/sdk';\r\n\tconst d=new Daytona();  \r\n\tawait d.v0.create();\r\n");
});
it('does not treat a mixed union or generic container as a proven client', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona, Sandbox } from '@daytona/sdk';\nfunction f(client: Daytona | Sandbox, clients: Array<Daytona>) {\n  client.delete();\n  clients.delete();\n}\n" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('.v0.delete();');
});
