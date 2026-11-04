/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([false, true])('migrates indexed service types without manual items with resolved SDK %s', async resolved => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\ntype List = Daytona['snapshot']['list'];\n",
  });
  if (resolved) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe("import { Daytona } from '@daytona/sdk';\ntype List = Daytona['v0']['snapshot']['list'];\n");
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it.each([false, true])('leaves already-versioned indexed service types untouched with resolved SDK %s', async resolved => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk';\ntype List = Daytona['v0']['snapshot']['list'];\n",
  });
  if (resolved) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it.each([false, true])('keeps migrated indexed service types unchanged on rerun with resolved SDK %s', async resolved => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\ntype List = Daytona['snapshot']['list'];\n",
  });
  if (resolved) installFakeSdk(root);
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});
