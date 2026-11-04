/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { expect, it } from 'vitest';
import { main } from '../src/cli.js';
import { contents, temporaryProject } from './helpers.js';

it('allows read-only JSON suggestions when the Git tree is dirty', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\nnew Daytona().v0.create();" });
  execFileSync('git', ['init', '--quiet', root]);
  const before = contents(root);
  // When
  const code = await main([root, '--suggest-v2', '--json'], { out: () => undefined, err: () => undefined });
  // Then
  expect(code).toBe(0);
  expect(contents(root)).toEqual(before);
});
