/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  '(key, value) => { if (value === cfg) value.apiUrl = "https://custom.example"; return value; }',
  'customReplacer',
])('refuses JSON serialization when its replacer can receive the config: %s', async replacer => {
  // Given a genuine serializer whose callback is not a read-only builtin.
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
    `const cfg = {}; JSON.stringify(cfg, ${replacer}); new Daytona(cfg).create();` });
  const before = contents(root);
  // When checking the config before migration.
  const result = await invoke(root, ['--json']);
  // Then callback access is an unknown escape, not a trusted observation.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['null', 'undefined', '["apiKey"]'])('permits inert JSON replacer %s', async replacer => {
  // Given a serializer without a user callback.
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
    `const cfg = {}; JSON.stringify(cfg, ${replacer}); new Daytona(cfg).create();` });
  // When checking the config before migration.
  const result = await invoke(root, ['--json']);
  // Then read-only serialization does not block the ordinary Cloud client.
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
});
