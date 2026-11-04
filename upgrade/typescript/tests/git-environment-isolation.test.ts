/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { chmodSync, existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { main } from '../src/cli.js';
import { gitSafety } from '../src/safety.js';
import { contents, temporaryProject } from './helpers.js';

const gitVariables = ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_CEILING_DIRECTORIES'] as const;
afterEach(() => vi.unstubAllEnvs());

it('detects a dirty target when the environment redirects to a clean repository', () => {
  // Given
  const root = temporaryProject({ 'app.ts': 'const changed = true;' });
  const decoy = temporaryProject();
  for (const directory of [root, decoy]) execFileSync('git', ['init', directory]);
  vi.stubEnv('GIT_DIR', join(decoy, '.git'));
  vi.stubEnv('GIT_WORK_TREE', decoy);
  // When
  const safety = gitSafety(root, false);
  // Then
  expect(safety.refusal?.reason).toBe('dirty-tree');
});

it('removes all repository-affecting variables and passes the target with -C', () => {
  // Given
  const root = temporaryProject();
  const conditions = gitVariables.map(name => `[ -n "\${${name}+x}" ]`).join(' || ');
  writeFileSync(join(root, 'git'), `#!/bin/sh\nif ${conditions}; then exit 99; fi\n[ "$1" = -C ] && [ "$2" = "${root}" ] || exit 98\necho '?? app.ts'\n`);
  chmodSync(join(root, 'git'), 0o755);
  vi.stubEnv('PATH', root);
  for (const name of gitVariables) vi.stubEnv(name, 'redirect');
  // When
  const safety = gitSafety(root, false);
  // Then
  expect(safety.refusal?.reason).toBe('dirty-tree');
});

it('writes without invoking broken Git when force is requested', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^0.224.0"}}',
    'app.ts': "import { Daytona } from '@daytona/sdk'; new Daytona().create();",
  });
  const marker = join(root, 'git-called');
  writeFileSync(join(root, 'git'), `#!/bin/sh\necho called > "${marker}"\nexit 99\n`);
  chmodSync(join(root, 'git'), 0o755);
  vi.stubEnv('PATH', root);
  // When
  const code = await main([root, '--force', '--yes'], { out: () => undefined, err: () => undefined });
  // Then
  expect(code).toBe(0);
  expect(existsSync(marker)).toBe(false);
  expect(contents(root)['app.ts']).toContain('new Daytona().v0.create()');
});
