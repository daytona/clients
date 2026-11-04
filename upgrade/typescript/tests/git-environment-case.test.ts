/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { afterEach, expect, it, vi } from 'vitest';
import { chmodSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { ignoredSources } from '../src/discovery.js';
import { loadMapping } from '../src/mapping.js';
import { gitEnvironment, gitSafety } from '../src/safety.js';
import { scan } from '../src/scans.js';
import { temporaryProject } from './helpers.js';

afterEach(() => vi.unstubAllEnvs());

it('filters mixed-case repository keys while preserving unrelated environment keys', () => {
  // Given
  const keys = ['git_dir', 'Git_Work_Tree', 'git_INDEX_file', 'Git_Object_Directory',
    'git_alternate_object_directories', 'Git_Common_Dir', 'git_ceiling_directories'];
  for (const key of keys) vi.stubEnv(key, 'redirect');
  vi.stubEnv('Git_Ssh_Command', 'ssh -F custom-config');
  // When
  const env = gitEnvironment();
  // Then
  expect(keys.filter(key => Object.hasOwn(env, key))).toEqual([]);
  expect(env['Git_Ssh_Command']).toBe('ssh -F custom-config');
});

it.skipIf(process.platform === 'win32').each(['status', 'check-ignore', 'rev-parse'] as const)(
  'strips mixed-case repository keys when spawning git %s', command => {
    // Given a wire-level Git substitute: even on Linux it rejects Windows-style redirects.
    const keys = ['git_dir', 'Git_Work_Tree', 'git_INDEX_file', 'Git_Object_Directory',
      'git_alternate_object_directories', 'Git_Common_Dir', 'git_ceiling_directories'];
    const root = temporaryProject({ '.git/HEAD': 'ref: refs/heads/main', 'app.ts': '',
      'deploy/app.yaml': 'env:\n  DAYTONA_API_URL: https://custom.example' });
    const conditions = keys.map(key => `[ -n "\${${key}+x}" ]`).join(' || ');
    writeFileSync(join(root, 'git'), `#!/bin/sh
if ${conditions}; then exit 99; fi
case "$3" in
  status) echo '?? app.ts';;
  check-ignore) printf '%s\\0' '${join(root, 'app.ts')}';;
  rev-parse) printf '%s\\n' '${join(root, 'deploy')}';;
esac
`);
    chmodSync(join(root, 'git'), 0o755);
    vi.stubEnv('PATH', root);
    for (const key of keys) vi.stubEnv(key, 'redirect');
    // When / Then: each public caller must observe the substitute's successful result.
    switch (command) {
      case 'status':
        expect(gitSafety(root, false).refusal?.reason).toBe('dirty-tree');
        break;
      case 'check-ignore':
        expect(ignoredSources([join(root, 'app.ts')], root)).toEqual(new Set([join(root, 'app.ts')]));
        break;
      case 'rev-parse':
        // Git reports deploy/ as the checkout root, so app.yaml is not deployment configuration.
        expect(scan([join(root, 'deploy/app.yaml')], root, { mapping: loadMapping(), dropApiUrl: false })).toEqual([]);
        break;
      default:
        command satisfies never;
    }
  },
);
