/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { tsExports } from './v0-ts-exports.mjs';

test('collects only public names when re-exports have trailing commas', () => {
  // Given: named, aliased and star exports with a cyclic re-export.
  const files = [
    { path: '/sdk/index.ts', text: "export { Daytona, Image as Img, }; export * from './services.js';" },
    { path: '/sdk/services.ts', text: "export type { Sandbox, }; export class Process {} export * from './index.js';" },
  ];
  // When: the pinned root surface is collected.
  const names = tsExports(files, '/sdk/index.ts');
  // Then: punctuation never becomes a public import name.
  assert.deepEqual([...names].sort(), ['Daytona', 'Img', 'Process', 'Sandbox']);
});

test('collects every public variable binding when declarations share an export', () => {
  // Given: a barrel re-exporting several variable declarations and patterns.
  const files = [
    { path: '/sdk/index.ts', text: "export * from './values.js';" },
    { path: '/sdk/values.ts', text: `
      export const A = 1, B = 2;
      export let C = 3, D = 4;
      export var E = 5, F = 6;
      export const { source: G, nested: { H } } = value, [I, , ...J] = items;
      const hidden = 7;
    ` },
  ];
  // When: the pinned root surface is collected through its barrel.
  const names = tsExports(files, '/sdk/index.ts');
  // Then: every exported binding is available without leaking local names.
  assert.deepEqual([...names].sort(), ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']);
});
