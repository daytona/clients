/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { afterEach, expect, it, vi } from 'vitest';
import { main } from '../src/cli.js';
import { gitSafety } from '../src/safety.js';
import { contents, temporaryProject } from './helpers.js';

afterEach(() => vi.unstubAllEnvs());

it.each([false, true])('continues with the missing-git note when git is missing, force %s', async force => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.create();",
  });
  vi.stubEnv('PATH', root);
  const output: string[] = [];
  // When
  const code = await main([root, '--yes', '--json', ...(force ? ['--force'] : [])], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(0);
  expect(contents(root)['index.ts']).toContain('d.v0.create()');
  expect(gitSafety(root, force)).toEqual(force ? {} : { note: 'Git is not installed; review the changes and keep a backup.' });
  expect(JSON.parse(output.join('')).nextSteps.includes('Git is not installed; review the changes and keep a backup.')).toBe(!force);
});

it('still aborts without writes when Git cannot be executed for another reason', async () => {
  // Given: a present but non-executable Git binary produces EACCES, not ENOENT.
  const root = temporaryProject({ git: '#!/bin/sh\nexit 0\n',
    'package.json': '{"dependencies":{"@daytona/sdk":"^0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk'; new Daytona().create();" });
  vi.stubEnv('PATH', root);
  const before = contents(root);
  const errors: string[] = [];
  // When
  const code = await main([root, '--yes'], { out: () => undefined, err: text => errors.push(text) });
  // Then
  expect(code).toBe(1);
  expect(errors.join('')).toContain('EACCES');
  expect(contents(root)).toEqual(before);
});
