/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n";
const loaders = [
  "import dotenv from 'dotenv'; dotenv.config({ path: 'config/settings.env' });",
  "import dotenv = require('dotenv'); dotenv.config({ path: 'config/settings.env' });",
  "require('dotenv').config({ path: `config/settings.env` });",
  "const dotenv = require('dotenv'); dotenv.config({ path: 'config/settings.env' });",
  "import { config as load } from 'dotenv'; load({ path: 'config/settings.env' });",
  "const { config: load } = require('dotenv'); load({ path: 'config/settings.env' });",
  "require('dotenv').config({ path: 'config/clean.env', path: 'config/settings.env' });",
  "require('dotenv').config({ 'path': 'config/settings.env' });",
  "import dotenv from 'dotenv'; dotenv.config({ path: ['config/clean.env', 'config/settings.env'] });",
  "import dotenv from 'dotenv'; import path from 'node:path'; dotenv.config({ path: path.join(__dirname, 'config', 'settings.env') });",
  "import dotenv from 'dotenv'; import * as path from 'path'; dotenv.config({ path: path.resolve(import.meta.dirname, 'config', 'settings.env') });",
  "import dotenv from 'dotenv'; import { join } from 'node:path'; dotenv.config({ path: join(process.cwd(), 'config', 'settings.env') });",
  "import dotenvx from '@dotenvx/dotenvx'; dotenvx.config({ path: 'config/settings.env' });",
  "require('dotenvx').config({ path: 'config/settings.env' });",
  "process.loadEnvFile('config/settings.env');",
  "import process from 'node:process'; process.loadEnvFile('config/settings.env');",
  "import { loadEnvFile } from 'node:process'; loadEnvFile('config/settings.env');",
  "import dotenv from 'dotenv'; import expand from 'dotenv-expand'; expand.expand(dotenv.config({ path: 'config/settings.env' }));",
] as const;

it.each(loaders)('refuses custom URLs without writing when loaded by %s', async loader => {
  // Given a nonstandard env filename reached through an environment loader.
  const root = temporaryProject({
    'app.ts': client + loader,
    'config/settings.env': 'DAYTONA_API_URL=https://self.example/api\n',
    'config/clean.env': 'UNRELATED=value\n',
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  installFakeSdk(root);
  const before = contents(root);
  // When the real CLI attempts a strict upgrade.
  const result = await invoke(root, ['--strict', '--json']);
  // Then custom endpoint refusal takes precedence and every file is unchanged.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.refused?.reason).toBe('custom-api-url');
  expect(report.items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'config/settings.env' }),
    expect.objectContaining({ kind: 'custom-api-url' }),
  ]));
  expect(contents(root)).toEqual(before);
});

it('scans a dotenv-flow directory outside the selected project', async () => {
  // Given a configured directory that ordinary project discovery cannot reach.
  const directory = temporaryProject({ '.env.local': 'DAYTONA_API_URL=https://self.example/api\n' });
  const root = temporaryProject({ 'app.ts': client + `require('dotenv-flow').config({ path: ${JSON.stringify(directory)} });` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.refused?.reason).toBe('custom-api-url');
  expect(contents(root)).toEqual(before);
});

it.each([
  { path: "path.join(__dirname, 'config', 'settings.env')", file: 'src/config/settings.env' },
  { path: "path.resolve(import.meta.dirname, 'config', 'settings.env')", file: 'src/config/settings.env' },
  { path: "path.join(process.cwd(), 'config', 'settings.env')", file: 'config/settings.env' },
])('resolves the correct base for $path in a nested source', async ({ path, file }) => {
  // Given different source/cwd bases and a custom URL only at the real target.
  const root = temporaryProject({
    'src/app.ts': client + `import path from 'node:path'; require('dotenv').config({ path: ${path} });`,
    [file]: 'DAYTONA_API_URL=https://self.example/api\n',
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url', file }));
});

it.each(["import 'dotenv/config';", "require('dotenv').config();", 'process.loadEnvFile();'])('retains default .env refusal for %s', async loader => {
  // Given a default loader and ordinary .env file.
  const root = temporaryProject({ 'app.ts': client + loader, '.env': 'DAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.refused?.reason).toBe('custom-api-url');
  expect(contents(root)).toEqual(before);
});

it.each(['config/settings.env', '../config/settings.env'])('checks both cwd and source-relative paths for %s', async path => {
  // Given a nested source file whose literal can resolve against either base.
  const root = temporaryProject({
    'src/app.ts': client + `require('dotenv').config({ path: '${path}' });`,
    [path.startsWith('..') ? 'config/settings.env' : 'src/config/settings.env']: 'DAYTONA_SERVER_URL=https://self.example/api\n',
  });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  "require('dotenv').config({ path: selectedPath });",
  "require('dotenv').config({ path: ['config/clean.env', selectedPath] });",
  "require('dotenv').config(options);",
  "process.loadEnvFile(selectedPath);",
  "require('@dotenvx/dotenvx').config({ path: `config/${stage}.env` });",
])('requires environment review when a loader path is unresolved: %s', async loader => {
  // Given a loader with an unknown path and an otherwise migratable client.
  const root = temporaryProject({ 'app.ts': client + loader, 'config/clean.env': 'UNRELATED=value\n' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then no unknown path is silently certified as safe or refused as a known custom URL.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.refused).toBeUndefined();
  expect(report.items.filter(item => item.kind === 'env-api-url')).toHaveLength(1);
});

it.each([
  "require('dotenv').config({ path: 'config/missing.env' });",
  "import path from 'node:path'; require('dotenv').config({ path: path.join(process.cwd(), 'config/missing.env') });",
  "require('dotenv').config();",
  "import 'dotenv/config';",
])('reports missing cwd-based loads through the CLI: %s', async loader => {
  // Given a project root distinct from the CLI cwd, without the loaded file.
  const root = temporaryProject({ 'app.ts': client + loader });
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the upgrade requires environment review instead of silently passing.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.refused).toBeUndefined();
  expect(report.items.filter(item => item.kind === 'env-api-url')).toHaveLength(1);
});

it.each(['UNRELATED=value\n', '# DAYTONA_API_URL=https://self.example/api\n', 'DAYTONA_API_URL=\n'])('migrates normally when the loaded file has no URL: %j', async value => {
  // Given
  const root = temporaryProject({ 'app.ts': client + loaders[0], 'config/settings.env': value,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items).toEqual([]);
  expect(contents(root)['app.ts']).toContain('new Daytona().v0.create()');
});

it('preserves the environment override behavior for explicitly loaded files', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': client + loaders[0], 'config/settings.env': 'DAYTONA_API_URL=https://self.example/api\n' });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items.filter(item => item.kind === 'env-api-url')).toHaveLength(1);
  expect(report.items.some(item => item.kind === 'custom-api-url')).toBe(false);
});
