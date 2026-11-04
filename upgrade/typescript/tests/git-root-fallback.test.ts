/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmodSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

afterEach(() => vi.unstubAllEnvs());

it.each([{ flags: ['--dry'] }, { flags: ['--print', '--force'] }])('proceeds with $flags when Git refuses ownership', async ({ flags }) => {
  // Given
  const bin = temporaryProject({ git: '#!/bin/sh\necho "fatal: detected dubious ownership in repository" >&2\nexit 128\n' });
  chmodSync(join(bin, 'git'), 0o755);
  vi.stubEnv('PATH', bin);
  const root = temporaryProject({ '.git/HEAD': 'ref: refs/heads/main\n', 'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}' });
  // When
  const result = await invoke(root, flags);
  // Then
  expect(result.code).toBe(0);
});

it.each(['', 'deploy'])('still refuses a custom deployment URL under %s when Git refuses ownership', async subdirectory => {
  // Given
  const bin = temporaryProject({ git: '#!/bin/sh\necho "fatal: detected dubious ownership in repository" >&2\nexit 128\n' });
  chmodSync(join(bin, 'git'), 0o755);
  vi.stubEnv('PATH', bin);
  const root = temporaryProject({
    '.git/HEAD': 'ref: refs/heads/main\n',
    'deploy/package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'deploy/values.yaml': 'DAYTONA_API_URL: https://daytona.internal.corp/api\n',
  });
  const before = contents(root);
  // When
  const result = await invoke(join(root, subdirectory), ['--dry', '--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).refused).toMatchObject({ reason: 'custom-api-url' });
  expect(contents(root)).toEqual(before);
});
