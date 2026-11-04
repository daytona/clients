/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import type { Refusal } from './model.js';
import { UpgradeError } from './model.js';

const repositoryVariables = new Set(['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
  'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_CEILING_DIRECTORIES']);

/** Windows environment names are case-insensitive; preserve unrelated keys verbatim. */
export function gitEnvironment(): NodeJS.ProcessEnv {
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => !repositoryVariables.has(key.toUpperCase())));
}

export function gitSafety(root: string, force: boolean): { readonly refusal?: Refusal; readonly note?: string } {
  if (force) return {};
  const env = gitEnvironment();
  const status = spawnSync('git', ['-C', root, 'status', '--porcelain', '--untracked-files=all'], { cwd: root, encoding: 'utf8', env: { ...env, LC_ALL: 'C', LANG: 'C' } });
  const unavailable = { note: 'Not a Git repository; review the changes and keep a backup.' };
  if (status.error && 'code' in status.error && status.error.code === 'ENOENT') return { note: 'Git is not installed; review the changes and keep a backup.' };
  if (status.error) throw status.error;
  if (status.status !== 0) {
    if (status.stderr.includes('not a git repository')) return unavailable;
    throw new UpgradeError(`Git safety check failed: ${status.stderr.trim()}`);
  }
  if (status.stdout.trim()) return { refusal: { reason: 'dirty-tree', message: 'Git tree has uncommitted changes. Commit or stash them, or run with --force.' } };
  return {};
}
