/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { isProjectLocalModule, isProjectSourcePath } from '../src/project-modules.js';
import { sourceSupport } from '../src/source-support.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['*', 'external', '@workspace/*'])('rejects npm resolution when paths contains %s', alias => {
  // Given an alias that fails to resolve, then falls back to an installed package.
  const name = alias === '@workspace/*' ? '@workspace/external' : 'external';
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: {
    allowJs: true, moduleResolution: ts.ModuleResolutionKind.Node10, baseUrl: '/project',
    paths: { [alias]: ['missing/*'] },
  } });
  project.createSourceFile(`/project/node_modules/${name}/index.ts`, 'export const api = unknownValue;');
  const source = project.createSourceFile('/project/index.ts', `import { api } from '${name}';`);
  // When
  const local = isProjectLocalModule(source, name, [name]);
  // Then package resolution overrides both aliases and workspace names.
  expect(local).toBe(false);
});

it.each([
  { name: 'external', expected: false },
  { name: '@/missing', expected: true },
  { name: '@workspace/missing', expected: true },
  { name: './missing', expected: true },
  { name: 'node:fs', expected: false },
])('classifies unresolved $name without relying on the catch-all alias', ({ name, expected }) => {
  // Given catch-all and specific aliases plus a declared workspace.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: {
    baseUrl: '/project', paths: { '*': ['missing/*'], '@/*': ['src/*'] },
  } });
  const source = project.createSourceFile('/project/index.ts', '');
  // When
  const local = isProjectLocalModule(source, name, ['@workspace/missing']);
  // Then
  expect(local).toBe(expected);
});

it('accepts a local file when resolution uses a catch-all alias', () => {
  // Given a catch-all that actually resolves to project source.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: {
    baseUrl: '/project', paths: { '*': ['src/*'] },
  } });
  project.createSourceFile('/project/src/local.ts', 'export const api = unknownValue;');
  const source = project.createSourceFile('/project/index.ts', '');
  // When
  const local = isProjectLocalModule(source, 'local', []);
  // Then
  expect(local).toBe(true);
});

it('rejects node_modules when the resolved file path contains backslashes', () => {
  // Given the Windows spelling, without ts-morph's path canonicalization.
  const path = 'C:\\project\\node_modules\\external\\index.ts';
  // When
  const local = isProjectSourcePath(path);
  // Then
  expect(local).toBe(false);
});

it.each([true, false])('does not block unrelated parse errors with catch-all paths (installed=%s)', installed => {
  // Given invalid non-Daytona source whose sole import is an external package.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: {
    baseUrl: '/project', paths: { '*': ['src/*'] },
  } });
  if (installed) project.createSourceFile('/project/node_modules/external/index.ts', '');
  const source = project.createSourceFile('/project/broken.ts', "import api from 'external'; const broken = ;");
  // When
  const support = sourceSupport(source, '/project');
  // Then
  expect(support.supported).toBe(false);
  expect(support.blocking).toBe(false);
});

it('does not traverse npm exports as project clients when catch-all paths is configured', async () => {
  // Given a package export with apparent SDK provenance, outside project source.
  const source = "import { api } from 'external'; api.create();";
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"*":["src/*"]}}}',
    'node_modules/external/package.json': '{"main":"index.js"}',
    'node_modules/external/index.js': "import { Daytona } from '@daytonaio/sdk'; export const api = new Daytona();",
    'index.js': source,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.js']).toBe(source);
  expect(result.code).toBe(0);
});
