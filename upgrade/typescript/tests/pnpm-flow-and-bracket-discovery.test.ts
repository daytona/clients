/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { expect, it } from 'vitest';
import { projects, walk } from '../src/discovery.js';
import { workspacePackageNames } from '../src/workspace-names.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  'packages: [\n  "packages/*", # include packages\n  "!packages/ignored",\n]\n',
  'packages:\n  - "packages/*" # include packages\n  - "!packages/ignored"\n',
])('resolves package names when pnpm uses a multiline sequence: %s', pnpm => {
  // Given
  const root = temporaryProject({ 'pnpm-workspace.yaml': pnpm,
    'packages/core/package.json': '{"name":"@local/core"}',
    'packages/ignored/package.json': '{"name":"@local/ignored"}',
    'include/package.json': '{"name":"@local/comment"}' });
  // When
  const names = workspacePackageNames(walk(root), { root });
  // Then
  expect(names).toEqual(['@local/core']);
});

it('blocks malformed local source when a multiline pnpm flow selects its package', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'pnpm-workspace.yaml': 'packages: [\n  "packages/*"\n]\n',
    'packages/core/package.json': '{"name":"@local/core"}',
    'app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();",
    'worker.ts': "import { client } from '@local/core'; const broken = ;",
  });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
});

it('keeps tsconfig alias resolution when a source path contains brackets', () => {
  // Given
  const root = temporaryProject({
    'tsconfig.json': JSON.stringify({ compilerOptions: { paths: { '@local/*': ['./lib/*'] } }, include: ['app/**/*.tsx'] }),
    'lib/value.ts': 'export const value = 42;',
    'app/[slug]/page.tsx': "import { value } from '@local/value'; export const answer = value;",
  });
  // When
  const groups = projects(root, walk(root));
  const source = groups.flatMap(group => group.sources).find(file => file.getFilePath() === join(root, 'app/[slug]/page.tsx'));
  // Then resolution must use the configured project, not fallback defaults.
  expect(source?.getImportDeclarations()[0]?.getModuleSpecifierSourceFile()?.getFilePath()).toBe(join(root, 'lib/value.ts'));
  expect(source?.getVariableDeclarationOrThrow('answer').getType().getText()).toBe('42');
});
