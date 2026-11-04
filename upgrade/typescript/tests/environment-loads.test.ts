/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { environmentLoads } from '../src/environment-loads.js';
import { ReadFailures } from '../src/read-failures.js';
import { temporaryProject } from './helpers.js';

it.each([
  "require('dotenv').config({ path: 'config/settings.env' });",
  "import path from 'node:path'; require('dotenv').config({ path: path.join(process.cwd(), 'config/settings.env') });",
  "require('dotenv').config();",
  "require('dotenv').config({});",
  "import 'dotenv/config';",
  "require('dotenv/config');",
  'process.loadEnvFile();',
  "require('dotenv').config({ path: '/missing/daytona-upgrade/settings.env' });",
  "require('dotenv').config({ path: unknownPath });",
])('reviews an unproven loaded file for %s', loader => {
  // Given the CLI cwd differs from the app root, with no matching file there.
  const root = temporaryProject();
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile(join(root, 'src/app.ts'), loader);
  // When
  const result = environmentLoads([source], { root, failures: new ReadFailures(root) });
  // Then loader uncertainty is visible even without a custom URL to refuse.
  expect(result.items).toEqual([expect.objectContaining({ kind: 'env-api-url', file: 'src/app.ts' })]);
});

it.each([
  "require('dotenv').config({ path: 'config/settings.env' });",
  "import path from 'node:path'; require('dotenv').config({ path: path.resolve(process.cwd(), 'config/settings.env') });",
])('reviews a cwd-relative path even when only its source-relative candidate exists: %s', loader => {
  // Given a clean file beside the source, but no file at the cwd/root candidate.
  const root = temporaryProject({ 'src/config/settings.env': 'UNRELATED=value\n' });
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile(join(root, 'src/app.ts'), loader);
  // When
  const result = environmentLoads([source], { root, failures: new ReadFailures(root) });
  // Then a source candidate cannot certify the app's unknown working directory.
  expect(result.items).toEqual([expect.objectContaining({ kind: 'env-api-url' })]);
});

it.each([
  { loader: "require('dotenv').config({ path: 'config/settings.env' });", file: 'config/settings.env' },
  { loader: "import path from 'node:path'; require('dotenv').config({ path: path.join(process.cwd(), 'config/settings.env') });", file: 'config/settings.env' },
  { loader: "require('dotenv').config();", file: '.env' },
  { loader: "import 'dotenv/config';", file: '.env' },
  { loader: "require('dotenv/config');", file: '.env' },
  { loader: "import path from 'node:path'; require('dotenv').config({ path: path.join(__dirname, 'settings.env') });", file: 'src/settings.env' },
])('scans an existing candidate without an uncertainty item: $loader', ({ loader, file }) => {
  // Given a clean file at the loader's provable candidate.
  const root = temporaryProject({ [file]: 'UNRELATED=value\n' });
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile(join(root, 'src/app.ts'), loader);
  // When
  const result = environmentLoads([source], { root, failures: new ReadFailures(root) });
  // Then the file participates in URL scanning and needs no uncertainty warning.
  expect([...result.files]).toContain(join(root, file));
  expect(result.items).toEqual([]);
});

it.each([
  "import dotenv = require('dotenv'); dotenv.config({ path: selectedPath });",
  "const { ['con' + method]: load } = require('dotenv'); load({ path: 'settings.env' });",
  "const { config: { call: load } } = require('dotenv'); load(null, { path: 'settings.env' });",
  "const { ...dotenv } = require('dotenv'); dotenv.config({ path: 'settings.env' });",
  "let load = require('dotenv').config; load = replacement; load({ path: 'settings.env' });",
  "const load = require('dotenv')['config']; load({ path: selectedPath });",
  "const load = require('dotenv').config.bind(null); load({ path: 'settings.env' });",
  "const dotenv = await import('dotenv'); dotenv.config({ path: 'settings.env' });",
  "const holder = { load: require('dotenv').config }; holder.load({ path: 'settings.env' });",
  "const { [method]: load } = require('node:process'); load('settings.env');",
])('reviews loader provenance that cannot prove its input: %s', loader => {
  // Given a known loader module in an import or alias shape needing review.
  const root = temporaryProject({ 'settings.env': 'UNRELATED=value\n' });
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile(join(root, 'app.ts'), loader);
  // When
  const result = environmentLoads([source], { root, failures: new ReadFailures(root) });
  // Then unsupported bindings never silently certify a safe load.
  expect(result.items.some(item => item.kind === 'env-api-url')).toBe(true);
});
