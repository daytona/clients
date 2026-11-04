/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const modules = ['@daytonaio/sdk', '@daytona/sdk', '@daytonaio/sdk/v0', '@daytona/sdk/v0'] as const;
const annotations = [
  'export const config: TYPE = { apiUrl: "https://self.hosted" };',
  'export const config = { apiUrl: "https://self.hosted" } satisfies TYPE;',
  'export const config = { apiUrl: "https://self.hosted" } as TYPE;',
] as const;

it.each(modules.flatMap(module => annotations.flatMap(code => [false, true].map(drop => ({ module, code, drop })))))(
  'refuses custom URLs when an import type identifies the config: $module, drop=$drop, $code', async ({ module, code, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'index.ts': code.replace('TYPE', `import('${module}').DaytonaConfig`) });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);

it.each([
  'import("other-sdk").DaytonaConfig',
  'import("@daytona/sdk/v2").DaytonaConfig',
  'import("@daytonaio/sdk").Sandbox',
  'import("@daytonaio/sdk").Other.DaytonaConfig',
  'typeof import("@daytonaio/sdk").DaytonaConfig',
])('preserves unrelated config objects when the import type is %s', async annotation => {
  // Given
  const code = `export const config: ${annotation} = { apiUrl: "https://self.hosted" };`;
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('{ apiUrl: "https://self.hosted" }');
});

it.each([false, true].flatMap(types => ['4', '{ apiUrl: "https://self.hosted" }'].map(argument => ({ types, argument }))))(
  'preserves own-constructor arguments when the class expression has typeof Daytona, types=$types: $argument', async ({ types, argument }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts':
      "import { Daytona } from '@daytonaio/sdk';\n" +
      'const Custom: typeof Daytona = class extends Daytona { constructor(n: number) { super({ apiKey: "k" }); } };\n' +
      `new Custom(${argument});` });
    if (types) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toContain(`new Custom(${argument});`);
  },
);

it.each([
  'function make(Client: typeof Daytona) { return new Client({ apiUrl: "https://self.hosted" }); }',
  'declare const Client: typeof Daytona; new Client({ apiUrl: "https://self.hosted" });',
  'const Client: typeof Daytona = class extends Daytona {}; new Client({ apiUrl: "https://self.hosted" });',
])('refuses custom URLs when the typeof Daytona binding inherits its constructor: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" + code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each(['get config()', 'config()', 'static config()'])(
  'refuses custom URLs when a class member returns DaytonaConfig: %s', async member => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts':
      "import type { DaytonaConfig } from '@daytonaio/sdk';\n" +
      `class C { ${member}: DaytonaConfig { return { apiUrl: "https://self.hosted" }; } }` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);
