/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmodSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { gitSafety } from '../src/safety.js';
import { temporaryProject } from './helpers.js';

afterEach(() => vi.unstubAllEnvs());

it('recognizes a non-repository when git inherits a non-English locale', () => {
  // Given: execute a real subprocess that models translated git stderr.
  const root = temporaryProject({ git: '#!/bin/sh\nif [ "$LC_ALL" = C ] && [ "$LANG" = C ]; then\n echo "fatal: not a git repository" >&2\nelse\n echo "fatal: kein Git-Repository" >&2\nfi\nexit 128\n' });
  chmodSync(join(root, 'git'), 0o755);
  vi.stubEnv('PATH', root);
  vi.stubEnv('LC_ALL', 'de_DE.UTF-8');
  vi.stubEnv('LANG', 'de_DE.UTF-8');
  // When
  const result = gitSafety(root, false);
  // Then
  expect(result).toEqual({ note: 'Not a Git repository; review the changes and keep a backup.' });
});
