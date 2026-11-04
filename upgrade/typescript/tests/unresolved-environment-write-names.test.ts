/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';

it.each([
  'Object.assign(process.env, settings);',
  'process.env[key] = value;',
  'Reflect.set(process.env, key, value);',
  'Object.defineProperty(process.env, key, { value });',
])('reports a generic environment review when the name is unresolved: %s', async source => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^0.224.0"}}',
    'app.ts': "import { Daytona } from '@daytona/sdk'; new Daytona().create();",
    'env.ts': source,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'env-api-url')).toEqual([
    expect.objectContaining({ message: 'an environment write may set DAYTONA_API_URL or DAYTONA_SERVER_URL; check that it does not point at a custom API' }),
  ]);
});
