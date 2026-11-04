/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join, relative } from 'node:path';
import { expect, it } from 'vitest';
import { installCommand } from '../src/discovery.js';
import { invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytona/sdk":"0.224.0"}}\n';
it.each(['.', 'absolute'])('prints a single dot when the project is cwd and the path is %s', async path => {
  // Given
  const root = temporaryProject({ 'package.json': manifest });
  const originalCwd = process.cwd();
  process.chdir(root);
  try {
    // When
    const result = await invoke(path === '.' ? '.' : root);
    // Then: no sentence-ending dot is appended to the directory itself.
    expect(result.output.split('\n').filter(line => line.trim().startsWith('Run npm install in '))).toEqual(['  Run npm install in .']);
  } finally { process.chdir(originalCwd); }
});
it('prints a cwd-relative directory when the selected project is elsewhere', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest });
  const destination = relative(process.cwd(), root).replaceAll('\\', '/');
  // When
  const result = await invoke(root);
  // Then
  expect(result.output.split('\n')).toContain(`  Run npm install in ${destination}`);
});
it('ignores an unrelated parent lockfile when selecting the project install directory', async () => {
  // Given
  const parent = temporaryProject({ 'yarn.lock': '', 'project/package.json': manifest });
  const root = join(parent, 'project');
  const originalCwd = process.cwd();
  process.chdir(root);
  try {
    // When
    const result = await invoke('.');
    // Then
    expect(result.output.split('\n')).toContain('  Run npm install in .');
  } finally { process.chdir(originalCwd); }
});
it('uses a nested lockfile before the workspace root lockfile', async () => {
  // Given
  const root = temporaryProject({ 'package-lock.json': '{}', 'nested/pnpm-lock.yaml': '', 'nested/package.json': manifest });
  // When
  const command = installCommand(join(root, 'nested'), root);
  // Then
  expect(command).toEqual({ cwd: join(root, 'nested'), manager: 'pnpm', args: ['install'] });
});
