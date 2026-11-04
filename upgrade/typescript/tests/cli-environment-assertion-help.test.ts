/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { main } from '../src/cli.js';

it('describes the environment assertion when the CLI prints help', async () => {
  // Given
  const output: string[] = [];
  // When
  const code = await main(['--help'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(0);
  expect(output.join('').split('\n').find(line => line.trimStart().startsWith('--drop-api-url'))?.trim())
    .toBe('--drop-api-url        Accept API URLs the tool cannot verify or that come from the environment, asserting they point at Daytona Cloud');
});
