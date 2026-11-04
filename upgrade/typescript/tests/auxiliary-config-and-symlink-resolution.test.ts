/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { mkdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([false, true])('ignores an unresolved auxiliary config (auxiliary preferred=%s)', async preferred => {
  // Given an app alias and a lint config that cannot resolve it, regardless of ownership rank.
  const source = "import { client } from '@/provider'; client.create();";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.app.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@/*': ['src/*'] } }, include: ['src'] }),
    'tsconfig.eslint.json': JSON.stringify(preferred ? { files: ['src/main.ts'] } : { include: ['src'] }),
    'src/provider.ts': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    'src/main.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the resolving app context remains usable, rather than only hiding the conflict.
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['src/main.ts']).toContain('client.v0.create();');
  expect(result.code).toBe(0);
});

it.each([false, true])('compares workspace symlink targets by real path (different=%s)', async different => {
  // Given two configs resolving the same import via real and symlinked package paths.
  const source = "import { client } from '@acme/core'; client.create();";
  const root = temporaryProject({ 'package.json': '{"workspaces":["packages/*"],"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'packages/core/package.json': '{"name":"@acme/core","main":"index.ts"}',
    'packages/core/index.ts': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    'other/index.ts': 'export const client = { create() {} };',
    'tsconfig.app.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@acme/core': ['packages/core/index.ts'] } }, include: ['src'] }),
    'tsconfig.eslint.json': JSON.stringify({ compilerOptions: { baseUrl: '.', preserveSymlinks: true,
      paths: { '@acme/core': ['node_modules/@acme/core/index.ts'] } }, include: ['src'] }), 'src/main.ts': source });
  installFakeSdk(root);
  mkdirSync(join(root, 'node_modules/@acme'), { recursive: true });
  symlinkSync(join(root, different ? 'other' : 'packages/core'), join(root, 'node_modules/@acme/core'), 'dir');
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then equivalent files are safe, but distinct existing files still conflict.
  expect(JSON.parse(result.output).items.filter((item: { readonly file: string; readonly kind: string }) =>
    item.file === 'src/main.ts' && item.kind === 'fixme')).toHaveLength(different ? 1 : 0);
  expect(contents(root)['src/main.ts']).toBe(different ? source : source.replace('client.create()', 'client.v0.create()'));
});
