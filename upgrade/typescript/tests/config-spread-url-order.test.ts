/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const cloud = '"https://app.daytona.io/api"';
const prefix = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";
const constructors = [
  { name: 'destructured', declaration: 'class Sub extends Daytona { constructor({apiUrl: url}: DaytonaConfig) { super({apiUrl: url}); } }', target: 'Sub' },
  { name: 'shorthand', declaration: 'class Sub extends Daytona { constructor({apiUrl}: DaytonaConfig) { super({apiUrl}); } }', target: 'Sub' },
  { name: 'direct', declaration: '', target: 'Daytona' },
] as const;
const tails = ['...custom', '...{apiKey: "k"}', `apiUrl: ${cloud}`, 'apiUrl: endpoint', '[key]: endpoint', 'get apiUrl() { return endpoint; }',
  // Escaped spellings of the same key overwrite it at runtime.
  "'api\\u0055rl': endpoint", 'api\\u0055rl: endpoint', '"apiUrl": endpoint', '["apiUrl"]: endpoint'] as const;

it.each(constructors.flatMap(constructor => tails.flatMap(tail => [false, true].map(drop => ({ ...constructor, tail, drop })))))(
  'keeps an overwritten URL unresolved when $name has $tail, drop=$drop', async ({ declaration, target, tail, drop }) => {
    // Given
    const code = `${declaration}\nnew ${target}({apiUrl: ${cloud}, ${tail}}).create();`;
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': prefix + code });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(drop ? 0 : 2);
    expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
      expect.objectContaining({ kind: drop ? 'config-field' : 'custom-api-url' }),
    ]) });
    if (drop) {
      expect(contents(root)['index.ts']).toContain(`{apiUrl: ${cloud}, ${tail}}`);
    } else {
      expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
      expect(contents(root)).toEqual(before);
    }
  },
);

it.each(constructors)('accepts an explicit URL when it follows a known spread for $name', async ({ declaration, target }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + declaration + `\nnew ${target}({...{apiKey: "k"}, apiUrl: ${cloud}}).create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('.v0.create()');
  expect(JSON.parse(result.output)).not.toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]) });
});

it.each(constructors.slice(0, 2))('accepts a destructured explicit URL when it follows an unknown spread for $name', async ({ declaration, target }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + declaration + `\nnew ${target}({...custom, apiUrl: ${cloud}}).create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('super({apiUrl');
});
