/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona, type DaytonaConfig as Config } from '@daytonaio/sdk';\n";
const constructors = [
  'const c = { Daytona }; new c.Daytona(CONFIG).create();',
  'const c = { Client: Daytona }; new c.Client(CONFIG).create();',
  'function make(Client: typeof Daytona) { return new Client(CONFIG).create(); }',
] as const;
const typedConfigs = [
  'export const config: Config = CONFIG;',
  'export const config = CONFIG satisfies Config;',
  'export const config = CONFIG as Config;',
  'export function config(): Config { return CONFIG; }',
  'export const config = (): Config => (CONFIG);',
  'export const config = function (): Config { return CONFIG; };',
] as const;
const cases = [...constructors, ...typedConfigs].flatMap(code => [false, true].map(types => ({ code, types })));

it.each(cases)('refuses custom config URLs when types=$types: $code', async ({ code, types }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': prefix + code.replace('CONFIG', "{ apiUrl: 'https://self-hosted.example/api' }") });
  if (types) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' }, items: expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]) });
  expect(contents(root)).toEqual(before);
});

it.each(cases)('removes constructor-only Cloud URL fields but preserves exports when types=$types: $code', async ({ code, types }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': prefix + code.replace('CONFIG', "{ apiUrl: 'https://app.daytona.io/api', apiKey: 'key' }") });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  if (code.startsWith('export ')) {
    expect(contents(root)['index.ts']).toContain("apiUrl: 'https://app.daytona.io/api'");
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field' }));
  } else expect(contents(root)['index.ts']).not.toContain('apiUrl');
  expect(contents(root)['index.ts']).toContain("apiKey: 'key'");
  if (constructors.some(constructor => constructor === code)) expect(contents(root)['index.ts']).toContain('.v0.create()');
});

it.each(typedConfigs)('preserves an exported config when its only field is a Cloud URL: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': prefix + code.replace('CONFIG', "{ apiUrl: 'https://app.daytona.io/api' }") });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  const parser = new Project({ useInMemoryFileSystem: true });
  const migrated = parser.createSourceFile('index.ts', contents(root)['index.ts'] ?? '');
  const object = migrated.getFirstDescendantByKindOrThrow(SyntaxKind.ObjectLiteralExpression);
  expect(object.getProperties()).toHaveLength(1);
  expect(object.getProperty('apiUrl')?.getKind()).toBe(SyntaxKind.PropertyAssignment);
  expect(parser.getProgram().getSyntacticDiagnostics(migrated)).toHaveLength(0);
});

it.each([false, true])('requires assertion for an unresolved constructor contract when drop=$drop', async drop => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'declare const Client: new (config: object) => Daytona; new Client({ apiKey: "key" }).create();' });
  installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(drop ? 0 : 2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: drop ? 'config-field' : 'custom-api-url' }),
  ]));
  if (!drop) expect(contents(root)).toEqual(before);
});

it.each(typedConfigs.flatMap(code => [false, true].flatMap(types => [false, true].map(drop => ({ code, types, drop })))))(
  'requires the Cloud assertion for non-literal configs when types=$types drop=$drop: $code', async ({ code, types, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'index.ts': prefix + code.replace('CONFIG', 'externalConfig()') });
    if (types) installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(drop ? 0 : 2);
    expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: drop ? 'config-field' : 'custom-api-url', file: 'index.ts' }),
    ]));
    if (!drop) expect(contents(root)).toEqual(before);
  },
);

it.each([
  'type DaytonaConfig = { apiUrl: string }; export const config: DaytonaConfig = { apiUrl: "https://custom" };',
  'function make(Daytona: new (config: object) => object) { return new Daytona({ apiUrl: "https://custom" }); }',
])('ignores unrelated local names when %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(code);
});
