/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { gitSafety } from '../src/safety.js';
import { envUrlItems } from '../src/env-api-url.js';
import { loadMapping } from '../src/mapping.js';
import { UpgradeError } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); syncBuiltinESMExports(); });

it('distinguishes missing git from a non-repository', () => {
  // Given
  const root = temporaryProject();
  vi.stubEnv('PATH', root);
  // When
  const result = gitSafety(root, false);
  // Then
  expect(result).toEqual({ note: 'Git is not installed; review the changes and keep a backup.' });
});

it('explains which namespace reads DAYTONA_API_URL', () => {
  // Given
  const context = { mapping: loadMapping(), dropApiUrl: false };
  // When
  const items = envUrlItems({ file: '.env', line: 1, column: 1 }, { name: 'DAYTONA_API_URL', literal: undefined }, context);
  // Then
  expect(items[0]?.message).toBe('DAYTONA_API_URL is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting.');
});

it('names a project-relative manifest when JSON parsing fails', async () => {
  // Given
  const root = temporaryProject({ 'packages/app/package.json': '{"dependencies": }' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(result.errors).toContain('packages/app/package.json');
  expect(result.errors).not.toContain(root);
});

it('names a project-relative target when a write fails', async () => {
  // Given
  const root = temporaryProject({ 'src/app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();" });
  const rename = fs.renameSync;
  vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
    if (to === join(root, 'src/app.ts')) throw new UpgradeError('Injected failure');
    rename(from, to);
  });
  syncBuiltinESMExports();
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(result.errors).toContain('Failed to write src/app.ts:');
  expect(result.errors).not.toContain(root);
});
