/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  "'daytona'", "'\\x64aytona'", "'\\u0064aytona'", '`DaYtOnA`', "'other'", 'daytonaName',
])('leaves arbitrary injection key %s without receiver evidence unchanged', async key => {
  // Given a key alone, without a matching Daytona value insertion.
  const source = `container.get(${key}).create();`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}', 'use.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then even a decoded Daytona string is not receiver identity.
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['use.ts']).toBe(source);
  expect(result.code).toBe(0);
});
