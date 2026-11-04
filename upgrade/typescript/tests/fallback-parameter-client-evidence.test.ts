/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

it.each(['??', '||'])('reviews an opaque parameter when its %s fallback links a Daytona constructor', async operator => {
  // Given
  const source = `import { Daytona } from '@daytonaio/sdk'; function run(client) { client = client ${operator} new Daytona(); return client.create(); } run({ create: () => 42 });`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.js': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: linked provenance merits review, not a speculative rewrite.
  expect(execute(contents(root)['index.js'] ?? '')).toBe(42);
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each(['??', '||'])('leaves unrelated clients alone when their %s fallback has no Daytona provenance', async operator => {
  // Given
  const source = `import type { Daytona } from '@daytonaio/sdk'; class Local { create() { return 99; } } function run(client) { client = client ${operator} new Local(); return client.create(); } run({ create: () => 42 });`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(42);
  expect(result.code, result.output).toBe(0);
});
