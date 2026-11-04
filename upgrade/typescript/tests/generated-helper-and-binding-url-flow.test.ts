/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each(['bootstrap.ts', 'bootstrap.min.ts'].flatMap(file => [
  'globalThis[`process`]', "globalThis['process']", 'global[`process`]',
  'require(`process`)', 'require(`node:process`)', "require('node:process')",
].map(process => ({ file, process }))))('refuses custom environment URLs from $process in $file', async ({ file, process }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, [file]: `${process}.env.DAYTONA_API_URL = 'https://custom.example';` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each(['apiUrl', 'serverUrl'].flatMap(key => [
  'new Daytona(config);',
  'const alias = config; new Daytona(alias);',
  'let alias = config; const next = alias; new Daytona(next);',
  'const X = Daytona; new X(config);',
  'const typed: DaytonaConfig = config;',
  'const typed: Options = config;',
  'const typed = config satisfies DaytonaConfig;',
  'const typed = config as Options;',
  'new Unknown(config);',
  'unknown(config);',
].flatMap(use => ['const', 'let'].flatMap(binding => [false, true].map(drop => ({ key, use, binding, drop }))))))(
  'classifies generated $binding $key flowing through $use with drop=$drop', async ({ key, use, binding, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'bundle.min.ts': `import { Daytona, DaytonaConfig, DaytonaConfig as Options } from '@daytonaio/sdk';
${binding} config = { ${key}: 'https://custom.example' }; ${use}` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--dry', '--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    const report = JSON.parse(result.output);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'bundle.min.ts', kind: 'custom-api-url' }));
    expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  });

it('refuses an unrelated generated URL when its binding never reaches a call and Cloud is asserted', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'bundle.min.ts': `import { Daytona } from '@daytonaio/sdk';
const config = { apiUrl: 'https://api.stripe.com/v1' }; const alias = config; new Daytona();` });
  // When
  const result = await invoke(root, ['--dry', '--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  const report = JSON.parse(result.output);
  expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'bundle.min.ts', kind: 'custom-api-url' }));
});

it('refuses an unrelated generated URL when a called binding shadows its binding', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'bundle.min.ts': `import { Daytona } from '@daytonaio/sdk';
const config = { apiUrl: 'https://api.stripe.com/v1' };
function bootstrap() { const config = {}; new Daytona(config); }` });
  // When
  const result = await invoke(root, ['--dry', '--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  const report = JSON.parse(result.output);
  expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'bundle.min.ts', kind: 'custom-api-url' }));
});

it.each(['apiUrl', 'serverUrl'].flatMap(key => ["'https://custom.example'", '`https://custom.example`']
  .flatMap(value => [false, true].map(drop => ({ key, value, drop })))))(
  'refuses generated $key=$value through an untyped helper when drop=$drop', async ({ key, value, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'bundle.min.ts': `import { Daytona } from '@daytonaio/sdk';
function use(c) { new Daytona(c) } use({ ${key}: ${value} });` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    const report = JSON.parse(result.output);
    expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'bundle.min.ts', kind: 'custom-api-url' }));
    expect(contents(root)).toEqual(before);
  });

it.each([
  "function make(): DaytonaConfig { return { apiUrl: 'https://custom.example' }; }",
  "const make = (): DaytonaConfig => ({ apiUrl: 'https://custom.example' });",
  "class F { build(): DaytonaConfig { if (x) { return { apiUrl: 'https://custom.example' }; } return {}; } }",
])('refuses a generated DaytonaConfig return of a custom URL: %s', async body => {
  // Given a generated file whose function is declared to return a config.
  const root = temporaryProject({ 'package.json': manifest,
    'bundle.min.ts': `import { DaytonaConfig } from '@daytonaio/sdk';\ndeclare const x: boolean;\n${body}` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then the known custom literal refuses, even with the flag.
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});
