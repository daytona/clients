/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const sources = [
  "const cfg = {}; Object.assign(cfg, { apiUrl: 'https://custom.example' }); new Daytona(cfg);",
  "const cfg = {}; const alias = cfg; alias.serverUrl = 'https://custom.example'; new Daytona(cfg);",
  "const cfg = {}; configure(cfg); new Daytona(cfg); function configure(c) { c.apiUrl = 'https://custom.example'; }",
  "function config() { return { apiUrl: 'https://custom.example' }; } new Daytona(config());",
  "const D = Daytona; new D({ apiUrl: 'https://custom.example' });",
] as const;

it.each(sources)('refuses custom URLs crossing indirect config sources: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code, 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
});

it.each([
  "const cfg = {}; const alias = cfg; alias.apiUrl = readUrl(); new Daytona(cfg);",
  "function configure(c) { c.serverUrl = readUrl(); } const cfg = {}; configure(cfg); new Daytona(cfg);",
  "Object.assign(unknownConfig, { ['apiUrl']: readUrl() }); new Daytona(unknownConfig);",
  "const maybeConfig = readConfig(); new Daytona(maybeConfig);",
])('leaves unproven URL sources with an override and reports them: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prefix.replace('@daytonaio/sdk', '@daytona/sdk') + code);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it('removes a statically resolved Cloud URL through Object.assign', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "const cfg = {}; Object.assign(cfg, { apiUrl: 'https://app.daytona.io/api', apiKey: 'k' }); new Daytona(cfg);" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain("Object.assign(cfg, { apiKey: 'k' })");
});

it('removes Cloud config fields through a stable constructor alias', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + "const D = Daytona; new D({ serverUrl: 'https://app.daytona.io/api' });" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new D();');
});

it('preserves an unrelated Cloud literal found by the conservative fallback', async () => {
  // Given
  const code = "const other = { apiUrl: 'https://app.daytona.io/api' };";
  const root = temporaryProject({ 'index.ts': prefix + code });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prefix.replace('@daytonaio/sdk', '@daytona/sdk') + code);
});

it('removes a Cloud URL written through a statically resolved config alias', async () => {
  // Given
  const code = "const cfg = {}; const alias = cfg; alias.apiUrl = 'https://app.daytona.io/api'; new Daytona(cfg);";
  const root = temporaryProject({ 'index.ts': prefix + code });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('alias.apiUrl');
});
