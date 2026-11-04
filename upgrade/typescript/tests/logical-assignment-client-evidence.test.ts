/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['&&=', '||=', '??='])('reviews an opaque parameter when %s assigns a Daytona client', async operator => {
  // Given
  const source = `import { Daytona } from '@daytonaio/sdk'; function run(client) { client ${operator} new Daytona(); return client.create(); } run({ create: () => 42 });`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.js': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: reassignment is evidence for review, not proof for a rewrite.
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(contents(root)['index.js']).not.toContain('client.v0.create()');
});

it.each(['&&=', '||=', '??='])('leaves unrelated logical assignments alone when %s has no SDK evidence', async operator => {
  // Given
  const source = `class Local { create() { return 42; } } function run(client) { client ${operator} new Local(); return client.create(); } run(new Local());`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.js': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
