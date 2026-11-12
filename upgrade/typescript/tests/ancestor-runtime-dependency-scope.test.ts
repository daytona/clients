/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { dependencyScopeItem } from '../src/dependency-scope.js';

const code = "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n";
const currentCode = code.replace('@daytonaio/sdk', '@daytona/sdk');

it('normalizes Windows-style paths before checking dependency scope', () => {
  // Given
  const root = temporaryProject({});
  const dependencyDirectory = join(root, 'packages/app');
  const rewritten = join(dependencyDirectory, 'index.ts').replaceAll('/', '\\');
  const dependencyDirectories = new Set([dependencyDirectory.replaceAll('/', '\\')]);
  // When
  const item = dependencyScopeItem(root, rewritten, dependencyDirectories, '^2.0.0');
  // Then
  expect(item).toBeUndefined();
});

it('reports the unchanged owning manifest when upgrading an npm workspace package', async () => {
  // Given: only the workspace root owns the SDK.
  const manifest = JSON.stringify({ workspaces: ['packages/*'], dependencies: { '@daytonaio/sdk': '0.120.0' } });
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': manifest, 'packages/app/index.ts': code });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'packages/app'), ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: '../../package.json' }));
  expect(readFileSync(join(root, 'package.json'), 'utf8')).toBe(manifest);
  expect(contents(root)['packages/app/index.ts']).toContain('d.v0.create()');
});

it.each(['dependencies', 'devDependencies', 'optionalDependencies'])(
  'accepts a parent 2.x runtime declaration in %s', async section => {
    // Given
    const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({ [section]: { '@daytona/sdk': '>=2' } }), 'app/index.ts': currentCode });
    // When
    const result = await invoke(join(root, 'app'), ['--json']);
    // Then
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency')).toEqual([]);
  },
);

it.each(['^2.0.0', '~2.1.0', '2.x', '>1', '2.0.0 - 3.0.0', '>=2 <3 || >=4'])(
  'accepts an ancestor range admitting only >=2: %s', async range => {
    // Given
    const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': range } }), 'app/index.ts': currentCode });
    // When
    const result = await invoke(join(root, 'app'), ['--json']);
    // Then
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency')).toEqual([]);
  },
);

it.each(['0.224.0', '^0.120.0', '*', '>=0', '^0.224.0 || ^2', '>=2.0.0-alpha.1', 'workspace:*', 'latest', '<0.0.0'])(
  'reports an ancestor that cannot certify SDK 2.x: %s', async range => {
    // Given
    const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': range } }), 'app/index.ts': currentCode });
    // When
    const result = await invoke(join(root, 'app'), ['--json']);
    // Then
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: '../package.json' }));
  },
);

it.each([{}, { peerDependencies: { '@daytona/sdk': '^2' } }, { overrides: { '@daytona/sdk': '^2' } }])(
  'reports a missing consuming dependency for parent %j', async parent => {
    // Given
    const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify(parent), 'app/package.json': '{}', 'app/index.ts': code });
    // When
    const result = await invoke(join(root, 'app'), ['--json', '--strict']);
    // Then
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: 'index.ts' }));
  },
);

it('honors a nearer old declaration over a farther SDK 2.x declaration', async () => {
  // Given
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': '{"dependencies":{"@daytona/sdk":"^2"}}',
    'packages/package.json': '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}', 'packages/app/index.ts': code });
  // When
  const result = await invoke(join(root, 'packages/app'), ['--json']);
  // Then
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: '../package.json' }));
});

it('reports an unreadable ancestor instead of certifying a farther manifest', async () => {
  // Given
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': '{"dependencies":{"@daytona/sdk":"^2"}}',
    'packages/package.json': '{', 'packages/app/index.ts': code });
  // When
  const result = await invoke(join(root, 'packages/app'), ['--json']);
  // Then
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: '../package.json' }));
});

it('does not certify a malformed runtime declaration alongside a valid one', async () => {
  // Given
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({
    dependencies: { '@daytona/sdk': '^2' }, optionalDependencies: { '@daytonaio/sdk': null },
  }), 'app/index.ts': code });
  // When
  const result = await invoke(join(root, 'app'), ['--json']);
  // Then
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency', file: '../package.json' }));
});

it.each(['no-git', 'nested-git', 'symlink'])(
  'does not certify dependencies outside the read boundary: %s', async boundary => {
    // Given
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"^2"}}', 'app/index.ts': currentCode,
      ...(boundary === 'nested-git' ? { 'app/.git/HEAD': '' } : {}),
      ...(boundary === 'symlink' ? { 'app/.git/HEAD': '', 'outside.json': '{"dependencies":{"@daytona/sdk":"^2"}}' } : {}) });
    if (boundary === 'symlink') symlinkSync(join(root, 'outside.json'), join(root, 'app/package.json'));
    // When
    const result = await invoke(join(root, 'app'), ['--json']);
    // Then
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency' }));
  },
);

it('does not duplicate an in-scope dependency report', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"peerDependencies":{"@daytona/sdk":"0.224.0"}}', 'index.ts': currentCode });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency')).toEqual([]);
});

it('does not treat an unchanged constraint as a consuming dependency', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"overrides":{"@daytona/sdk":"^2.0.0"}}', 'index.ts': currentCode });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency' }));
});

it.each(['--suggest-v2', '--remove-suggestions'])(
  'does not request a dependency migration in %s mode', async mode => {
    // Given
    const root = temporaryProject({ 'index.ts': code });
    // When
    const result = await invoke(root, ['--json', mode]);
    // Then
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency')).toEqual([]);
  },
);
