/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { constructor: 'Map', operation: "store.get('key');" },
  { constructor: 'Set', operation: "store.delete('key');" },
  { constructor: 'WeakMap', operation: 'store.get({});' },
  { constructor: 'WeakSet', operation: 'store.delete({});' },
])('keeps an imported native $constructor clean with SDK evidence elsewhere', async ({ constructor, operation }) => {
  // Given a native object, not a client or a value retrieved from a container.
  const source = `import { store } from './registry.js'; ${operation}`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'main.js': "import { Daytona } from '@daytonaio/sdk';",
    'registry.js': `export const store = new ${constructor}();`, 'use.js': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the container itself stays untouched; its get/delete is not an SDK operation.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['use.js']).toBe(source);
  expect(result.code).toBe(0);
});
