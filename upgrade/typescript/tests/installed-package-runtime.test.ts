/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, expect, it } from 'vitest';
import { version } from '../src/run.js';
import { UpgradeError } from '../src/model.js';

const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const installed = mkdtempSync(join(tmpdir(), 'daytona-upgrade-pack-'));
const shell = process.platform === 'win32';
afterAll(() => { rmSync(installed, { recursive: true, force: true }); });
beforeAll(() => {
  // Given: install the actual published artifact, including npm's bin symlink.
  const built = spawnSync('npm', ['run', 'build'], { cwd: packageRoot, encoding: 'utf8', shell });
  expect(built.status, built.stderr + built.stdout).toBe(0);
  const packed = spawnSync('npm', ['pack', '--pack-destination', installed, '--json'], { cwd: packageRoot, encoding: 'utf8', shell });
  expect(packed.status, packed.stderr).toBe(0);
  // Reuse the complete lockfile so a fresh npm ci cache needs no registry metadata.
  const manifest: unknown = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8'));
  if (typeof manifest !== 'object' || manifest === null) throw new UpgradeError('Invalid package manifest');
  writeFileSync(join(installed, 'package.json'), JSON.stringify({ ...manifest, name: 'bin-smoke', version: '1.0.0', private: true }));
  copyFileSync(join(packageRoot, 'package-lock.json'), join(installed, 'package-lock.json'));
  const result = spawnSync('npm', ['install', '--offline', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund', join(installed, `daytona-upgrade-${version()}.tgz`)], { cwd: installed, encoding: 'utf8', shell });
  expect(result.status, result.stderr + result.stdout).toBe(0);
}, 30_000);

it.each(['bin', 'npx'] as const)('prints the installed version when launched through %s', launcher => {
  // Given
  const command = launcher === 'bin' ? join(installed, `node_modules/.bin/daytona-upgrade${shell ? '.cmd' : ''}`) : 'npx';
  const args = launcher === 'bin' ? ['--version'] : ['--offline', '--no-install', '@daytona/upgrade', '--version'];
  // When
  const result = spawnSync(command, args, { cwd: installed, encoding: 'utf8', shell });
  // Then
  expect(result.status, result.stderr).toBe(0);
  expect(result.stdout.trim()).toBe(version());
});

it('advertises a runtime compatible with production dependencies when the packed manifest is installed', () => {
  // Given
  const manifest: unknown = JSON.parse(readFileSync(join(installed, 'node_modules/@daytona/upgrade/package.json'), 'utf8'));
  // When: inspect the consumer-facing engine constraint, not a source-only copy.
  // Then
  expect(manifest).toMatchObject({ engines: { node: '>=20.0.0' } });
});
