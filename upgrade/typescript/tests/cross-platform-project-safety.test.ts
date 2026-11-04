/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
import { projects, walk } from '../src/discovery.js';
import { manifests } from '../src/manifests.js';
import { loadMapping } from '../src/mapping.js';
import { diff } from '../src/output.js';
import { main } from '../src/cli.js';
import { scan } from '../src/scans.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it('upgrades the dependency when a manifest name follows a backslash', () => {
  // Given: a literal backslash exercises filename matching on POSIX too.
  const root = temporaryProject({ 'workspace\\package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  // When
  const result = manifests(walk(root), loadMapping(), root);
  // Then
  expect(result.changes.map(change => JSON.parse(change.after))).toEqual([{ dependencies: { '@daytona/sdk': '^2.0.0' } }]);
});

it.each(['tsconfig.json', 'jsconfig.json'])('loads project settings when %s has Windows separators', name => {
  // Given
  const root = temporaryProject({ [name]: '{"compilerOptions":{"strictNullChecks":true},"files":["index.ts"]}', 'index.ts': 'export const value = 1;' });
  const files = walk(root).map(file => file.replaceAll('/', '\\'));
  // When
  const groups = projects(root, files);
  // Then
  expect(groups).toHaveLength(1);
  expect(groups[0]?.project.getCompilerOptions().strictNullChecks).toBe(true);
  expect(groups[0]?.sources.map(source => source.getBaseName())).toEqual(['index.ts']);
});

it.each(['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'])('reports aliases without duplicating a root dependency in %s', async section => {
  // Given
  const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '0.224.0' }, [section]: { ...(section === 'dependencies' ? { '@daytonaio/sdk': '0.224.0' } : {}), '@daytona/sdk': '^2.0.0' } }) });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(contents(root)['package.json'] ?? '{}')).toHaveProperty('dependencies.@daytonaio/sdk', '0.224.0');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.each(['setup.cfg', 'requirements.in', 'constraints.in', 'nested/setup.cfg'])('reports Python guidance when the manifest is %s', async file => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}', [file]: '' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ file, kind: 'other-ecosystem' })]) });
});

it.each(['workspace\\setup.cfg', 'workspace\\.env.local', 'workspace\\Dockerfile'])('recognizes ecosystem files when the filename follows a backslash: %s', file => {
  // Given
  const root = temporaryProject({ [file]: 'DAYTONA_API_URL=https://custom.example\n' });
  // When
  const items = scan(walk(root), root, { mapping: loadMapping(), dropApiUrl: true });
  // Then
  expect(items).toEqual([expect.objectContaining({ kind: file.endsWith('setup.cfg') ? 'other-ecosystem' : 'env-api-url' })]);
});

it.each(['DAYTONA_API_URL_BACKUP', 'MY_DAYTONA_API_URL', 'DAYTONA_SERVER_URL2', '$DAYTONA_API_URL_EXTRA'])('ignores an unrelated environment identifier when it is %s', async name => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}', '.env': `${name}=https://example.com\n` });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: [] });
});

it('shows zero replacement lines when a dry diff empties a file', () => {
  // Given
  const change = { file: 'index.ts', before: '// daytona-v2: obsolete\n', after: '' };
  // When
  const output = diff([change]);
  // Then
  expect(output).toBe('--- index.ts\n+++ index.ts\n@@ -1,1 +0,0 @@\n-// daytona-v2: obsolete\n');
});

it('refuses untracked files when Git config hides untracked changes', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  const git = (args: readonly string[]) => spawnSync('git', [...args], { cwd: root, env: { ...process.env, LC_ALL: 'C', LANG: 'C' }, encoding: 'utf8' });
  expect(git(['init', '-q']).status).toBe(0);
  expect(git(['config', 'status.showUntrackedFiles', 'no']).status).toBe(0);
  const before = contents(root);
  const output: string[] = [];
  // When
  const code = await main([root, '--yes', '--json'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(2);
  expect(JSON.parse(output.join(''))).toMatchObject({ refused: { reason: 'dirty-tree' } });
  expect(contents(root)).toEqual(before);
});
