/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

it.each([
  { expression: 'client && new Daytona()', review: false },
  { expression: 'client && ((new Daytona()))', review: false },
  { expression: 'flag && client', review: true },
  { expression: 'client && client', review: true },
  { expression: 'new Daytona() && client', review: true },
])('tracks the selected value when using $expression', async ({ expression, review }) => {
  // Given: only a selected fake can conflict with a rewritten get call.
  const source = `import { Daytona } from '@daytonaio/sdk';
function fetchSandbox(client?: Daytona) { const flag = true; const d = ${expression}; return d?.get('id'); }
fetchSandbox({ get: () => 42 } as unknown as Daytona);`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(review ? 42 : 11);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: real SDK constructions need no mock review; selected fakes do.
  expect(result.code, result.output).toBe(review ? 1 : 0);
  const migrated = contents(root)['index.ts'] ?? '';
  if (review) {
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
    expect(() => execute(migrated)).toThrow();
  } else {
    expect(JSON.parse(result.output).items).toEqual([]);
    expect(execute(migrated)).toBe(23);
  }
});
