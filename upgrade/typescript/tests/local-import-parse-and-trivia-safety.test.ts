/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import { walk } from '../src/discovery.js';
import { sourceSupport } from '../src/source-support.js';
import { workspacePackageNames } from '../src/workspace-names.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk';\nexport const client = new Daytona();\nclient.create();\n";
const dependency = { dependencies: { '@daytonaio/sdk': '0.224.0' } };

it.each([
  "import { client } from '@app/client';\nconst broken = ;",
  "export { client } from '@app/client';\nconst broken = ;",
  "const { client } = require('@app/client');\nconst broken = ;",
  "import('@app/client');\nconst broken = ;",
  "// @flow\nimport { client } from '@app/client';\nconst value: number = 1; client.create();",
])('blocks migration when unparsable source imports a tsconfig alias: %s', async input => {
  // Given
  const root = temporaryProject({ 'package.json': JSON.stringify(dependency),
    'tsconfig.json': JSON.stringify({ compilerOptions: { allowJs: true, paths: { '@app/*': ['./*'] } }, include: ['**/*'] }),
    'client.ts': client, 'worker.js': input });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
});

it.each([
  { 'package.json': JSON.stringify({ ...dependency, workspaces: ['packages/*'] }) },
  { 'package.json': JSON.stringify({ ...dependency, workspaces: { packages: ['packages/*'] } }) },
  { 'package.json': JSON.stringify(dependency), 'pnpm-workspace.yaml': "packages:\n  - 'packages/*'\n" },
])('blocks migration when a workspace glob selects a differently named package: %j', async workspace => {
  // Given
  const root = temporaryProject({ ...workspace,
    'packages/core/package.json': '{"name":"@local/shared-client"}',
    'packages/core/client.ts': client,
    'worker.js': "// @flow\nconst { client } = require('@local/shared-client/client');\nconst value: number = 1; client.create();" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
});

it.each(['import', 'export'].flatMap(keyword => [
  '// eslint-disable-line',
  '// eslint-disable-line custom/rule, another/rule -- retain this explanation',
  '// eslint-disable-line no-unused-vars -- arbitrary text: @#$%',
].map(suppression => ({ keyword, suppression }))))('preserves $suppression on both split $keyword declarations', async ({ keyword, suppression }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `${keyword} { Daytona, Sandbox } from '@daytonaio/sdk'; ${suppression}\n` });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  const lines = readFileSync(join(root, 'app.ts'), 'utf8').trimEnd().split('\n');
  expect(lines).toHaveLength(2);
  expect(lines.every(line => line.endsWith(suppression))).toBe(true);
});

it('preserves an ordinary trailing comment on both lines when splitting imports', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import { Daytona, Sandbox } from '@daytonaio/sdk'; // explanation\n" });
  // When
  await invoke(root);
  // Then
  const lines = readFileSync(join(root, 'app.ts'), 'utf8').trimEnd().split('\n');
  expect(lines).toHaveLength(2);
  expect(lines.every(line => line.endsWith('// explanation'))).toBe(true);
});

it.each([
  { manifest: { workspaces: ['packages/{core,ignored}', '!packages/ignored'] }, pnpm: '' },
  { manifest: {}, pnpm: 'packages:\n  - "packages/*"\n  - "!packages/ignored"\n' },
  { manifest: {}, pnpm: "packages: ['packages/*', '!packages/ignored']\n" },
])('resolves actual package names when workspace globs include exclusions: %j', ({ manifest, pnpm }) => {
  // Given
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest), 'pnpm-workspace.yaml': pnpm,
    'packages/core/package.json': '{"name":"@local/shared-client"}',
    'packages/ignored/package.json': '{"name":"@local/ignored"}',
    'external/package.json': '{"name":"@local/external"}' });
  // When
  const names = workspacePackageNames(walk(root), { root });
  // Then
  expect(names).toEqual(['@local/shared-client']);
});

it.each(['@local/client', '@local/client/resource'])('blocks parse failures when %s names a workspace package', name => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/worker.ts', `import { client } from '${name}'; const broken = ;`);
  // When
  const support = sourceSupport(source, '/', ['@local/client']);
  // Then
  expect(support.blocking).toBe(true);
});

it.each([
  { name: '@local/client-extra', paths: {}, workspace: ['@local/client'] },
  { name: '@app/other', paths: { '@app/client': ['./client'] }, workspace: [] },
  { name: '@app/client-extra', paths: { '@app/*/client': ['./client'] }, workspace: [] },
])('skips an unrelated parse failure when $name does not match local module keys', ({ name, paths, workspace }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { paths } });
  const source = project.createSourceFile('/worker.ts', `import { client } from '${name}'; const broken = ;`);
  // When
  const support = sourceSupport(source, '/', workspace);
  // Then
  expect(support).toMatchObject({ supported: false, blocking: false });
});

it('blocks an exact alias when its target does not exist yet', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { paths: { '@client': ['./missing'] } } });
  const source = project.createSourceFile('/worker.ts', "import { client } from '@client'; const broken = ;");
  // When
  const support = sourceSupport(source, '/');
  // Then
  expect(support.blocking).toBe(true);
});
