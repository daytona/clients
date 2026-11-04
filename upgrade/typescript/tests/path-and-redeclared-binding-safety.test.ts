/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { isConstructor } from '../src/bindings.js';
import { loadMapping } from '../src/mapping.js';
import { diff } from '../src/output.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const patchAvailable = spawnSync('patch', ['--version']).status === 0;

it.skipIf(!patchAvailable).each([
  { before: 'old\n', after: '' },
  { before: '', after: 'new\n' },
])('emits an applicable diff when one side is empty: %j', change => {
  // Given
  const root = temporaryProject({ 'index.ts': change.before });
  const patch = diff([{ file: 'index.ts', ...change }]);
  // When
  const result = spawnSync('patch', ['-p0'], { cwd: root, input: patch, encoding: 'utf8' });
  // Then
  expect(result.status, result.stderr + result.stdout).toBe(0);
  expect(contents(root)['index.ts']).toBe(change.after);
});

it.skipIf(process.platform === 'win32')('reports an unsupported POSIX backslash path before changing files', async () => {
  // Given
  const file = 'client\\entry.ts';
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    [file]: "import { Daytona } from '@daytonaio/sdk';\nnew Daytona({ apiUrl: 'https://custom.example' }).create();\n" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: [expect.objectContaining({ kind: 'unsupported-file' })] });
  expect(contents(root)).toEqual(before);
});

it('leaves a constructor unproven when a var redeclaration overwrites its alias', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', "import { Daytona } from '@daytonaio/sdk'; var C = Daytona; var C = Other; new C();");
  const expression = source.getFirstDescendantByKindOrThrow(SyntaxKind.NewExpression);
  // When
  const proven = isConstructor(expression, loadMapping());
  // Then
  expect(proven).toBe(false);
});

it.each([true, false])('preserves calls on a redeclared constructor with SDK types=%s', async types => {
  // Given
  const root = temporaryProject({ 'index.js': "import { Daytona } from '@daytonaio/sdk';\nclass Other { create() { return 42; } }\nvar C = Daytona; var C = Other; const client = new C(); client.create();\n" });
  if (types) installFakeSdk(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.js']?.split('\n').filter(line => !line.trimStart().startsWith('//')).join('\n')).not.toContain('.v0.');
});

it('reports a dynamic CommonJS rest read for manual review', async () => {
  // Given
  const root = temporaryProject({ 'index.cjs': "const { Daytona, ...rest } = require('@daytonaio/sdk');\nrest[key];" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['index.cjs']).toContain('rest[key];');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'private-import', line: 2 }),
  ]) });
});

it.each(['index.ts', 'index.js'].flatMap(file => [true, false].flatMap(types => [
  'Object.assign(holder, { client: other });',
  "Object['assign'](holder, { client: other });",
  'const alias = holder; Object.assign(alias, replacement);',
].map(mutation => ({ file, types, mutation })))))('leaves mutated properties unproven in $file with types=$types: $mutation', async ({ file, types, mutation }) => {
  // Given
  const root = temporaryProject({ [file]: `import { Daytona } from '@daytonaio/sdk';\nconst holder = { client: new Daytona() };\n${mutation}\nholder.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)[file]?.split('\n').filter(line => !line.trimStart().startsWith('//')).join('\n')).not.toContain('.v0.');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it.each([true, false])('reports typed parameter destructuring with SDK types=%s', async types => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\nfunction run({ create: make }: Daytona) { make(); }\n" });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'fixme', line: 2 }),
  ]) });
});
