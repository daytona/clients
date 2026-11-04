/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";

it.each([
  "const backend = { apiUrl: 'https://backend.example/api' }; new Daytona();",
  "const backend = {}; backend.serverUrl = 'https://backend.example/api'; new Daytona();",
  "Object.assign(backend, { apiUrl: readUrl() }); new Daytona();",
  "type BackendConfig = { apiUrl: string }; const backend: BackendConfig = { apiUrl: 'https://backend.example/api' };",
  "function unused() { return { apiUrl: 'https://backend.example/api' }; } new Daytona();",
])('leaves URL objects alone when they never reach a client: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': prefix + code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prefix.replace('@daytonaio/sdk', '@daytona/sdk') + code);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind !== 'types-unresolved')).toEqual([]);
});

it.each([
  'function run(config: DaytonaConfig) { new Daytona(config); }',
  "import { config } from './config.js'; new Daytona(config);",
  "import { makeConfig } from './config.js'; new Daytona(makeConfig());",
  "import { configure } from './config.js'; const config = {}; configure(config); new Daytona(config);",
  'const config = {}; const alias = config; customize(alias); new Daytona(config);',
  'const config = {}; customize(config); const alias = config; new Daytona({ ...alias });',
  'function makeConfig() { const config = {}; customize(config); return config; } new Daytona(makeConfig());',
])('refuses a construction when its config origin or mutation is unknown: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code,
    'config.ts': 'export const config = {}; export function makeConfig() { return {}; } export function configure(c) { c.apiUrl = "https://custom.example"; }' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({
    kind: 'custom-api-url', message: 'cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url', file: 'index.ts',
  })]) });
});

it.each([
  'function run(config: DaytonaConfig) { new Daytona(config); }',
  "import { config } from './config.js'; new Daytona(config);",
  'const config = {}; customize(config); new Daytona(config);',
])('reports unknown config with the explicit override: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code, 'config.ts': 'export const config = {};' });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.each([
  "const backend: DaytonaConfig = { apiUrl: 'https://backend.example/api' };",
  "const config = { apiUrl: 'https://custom.example' }; new Daytona(config);",
  "const base = { serverUrl: 'https://custom.example' }; const alias = base; new Daytona({ ...alias });",
  "const config = {}; Object['assign'](config, { apiUrl: 'https://custom.example' }); new Daytona(config);",
  "const build = () => ({ apiUrl: 'https://custom.example' }); new Daytona(build());",
  "const identity = config => config; new Daytona(identity({ apiUrl: 'https://custom.example' }));",
  "function configure(config) { config.apiUrl = 'https://custom.example'; } const config = {}; configure(config); new Daytona(config);",
  "function run(config) { new Daytona(config); } run({ apiUrl: 'https://custom.example' });",
])('still refuses a known custom URL reaching a client with override: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  'new Daytona();',
  'const config = { apiKey: "key" }; const alias = config; new Daytona({ ...alias });',
  'const config = {}; Object.assign(config, { apiKey: "key" }); new Daytona(config);',
  'const defaults = { apiKey: "key" }; new Daytona(Object.assign({}, defaults));',
  'const makeConfig = config => config; new Daytona(makeConfig({ apiKey: "key" }));',
  'function makeConfig() { return { apiKey: "key" }; } new Daytona(makeConfig());',
])('migrates a resolved config without URL findings: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => ['custom-api-url', 'config-field'].includes(entry.kind))).toEqual([]);
});
