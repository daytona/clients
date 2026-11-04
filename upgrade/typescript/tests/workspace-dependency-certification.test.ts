/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['workspace:*', 'workspace:^', 'file:packages/core', 'link:packages/core'].flatMap(spec =>
  ['@acme/core', '@acme/core/client'].map(name => ({ spec, name }))))('leaves unresolved generic local dependency $name ($spec) clean', async ({ spec, name }) => {
  // Given an uninstalled workspace package whose built output is excluded from source discovery.
  const root = temporaryProject({ 'package.json': JSON.stringify({ workspaces: ['packages/*'],
    dependencies: { '@daytonaio/sdk': '0.224.0', '@acme/core': spec } }),
    'packages/core/package.json': '{"name":"@acme/core","main":"dist/index.js"}',
    'packages/core/dist/index.js': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    'use.ts': `import { client } from '${name}'; client.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the unresolved generic import has no traced Daytona flow or identity.
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['use.ts']).toBe(`import { client } from '${name}'; client.create();`);
  expect(result.code).toBe(0);
});
