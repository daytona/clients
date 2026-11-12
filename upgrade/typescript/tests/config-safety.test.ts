/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prelude = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";
const customSources = [
  "new Daytona({ ...{ apiUrl: 'https://custom.example' } });",
  "const base = { serverUrl: 'https://custom.example' }; new Daytona({ ...base });",
  "const base = { apiUrl: 'https://custom.example' }; const nested = { ...base }; new Daytona({ ...nested });",
  "const config = {}; config.apiUrl = 'https://custom.example'; new Daytona(config);",
  "const config: DaytonaConfig = {}; config.serverUrl = 'https://custom.example'; new Daytona(config);",
  "const config = {} satisfies DaytonaConfig; config.apiUrl = 'https://custom.example'; new Daytona(config);",
  "const base = { apiUrl: 'https://custom.example' }; new Daytona({ ...unknown, ...base });",
] as const;

it.each(customSources.flatMap(code => [false, true].map(drop => ({ code, drop }))))(
  'refuses explicit custom config sources with drop=$drop: $code', async ({ code, drop }) => {
    // Given
    const root = temporaryProject({ 'index.ts': prelude + code + '\n', 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
    expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]) });
  },
);

const unresolvedSources = [
  'new Daytona(makeConfig());',
  'const config = makeConfig(); new Daytona(config);',
  'new Daytona(unknownConfig);',
  'new Daytona({ ...unknownConfig });',
  'let base = {}; new Daytona({ ...base });',
  "new Daytona({ [key]: 'https://app.daytona.io/api' });",
  "const config = {}; config[key] = 'https://app.daytona.io/api'; new Daytona(config);",
  'const config: DaytonaConfig = makeConfig(); new Daytona(config);',
  'const config = { ...config }; new Daytona(config);',
  'const config = {}; config.apiUrl = readUrl(); new Daytona(config);',
  'new Daytona({ get apiUrl() { return readUrl(); } });',
  "new Daytona({ get 'apiUrl'() { return readUrl(); } });",
] as const;

it.each(unresolvedSources)('refuses unverifiable config source: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prelude + code + '\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({
    kind: 'custom-api-url', message: 'cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url',
  })]) });
});

it.each(unresolvedSources)('leaves unresolved config source intact with explicit override: %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prelude + code + '\n' });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code + '\n');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
  // The flag was passed: no item asks for it again.
  expect(result.output).not.toContain('rerun with --drop-api-url');
});

it.each([
  ["new Daytona({ apiKey: k, apiUrl: 'https://app.daytona.io/api', target: 'us' })", "new Daytona({ apiKey: k, target: 'us' })"],
  ["new Daytona({ apiUrl: 'https://app.daytona.io/api', apiKey: k })", 'new Daytona({ apiKey: k })'],
  ["new Daytona({ apiKey: k, apiUrl: 'https://app.daytona.io/api' })", 'new Daytona({ apiKey: k })'],
])('removes a Cloud API URL without leaving extra spaces: %s', async (code, expected) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${prelude}const k = process.env.K;\n${code};\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain(`${expected};\n`);
});

it('removes cloud fields in shared nested spreads exactly once while preserving other fields', async () => {
  // Given
  const code = "const base = { apiUrl: 'https://app.daytona.io/api/', serverUrl: `https://app.daytona.io/api`, apiKey: 'key', target: 'us', organizationId: 'org', useDeprecatedPolling: true };\nconst nested = { ...base };\nnew Daytona({ ...nested }); new Daytona({ ...base });\n";
  const root = temporaryProject({ 'index.ts': prelude + code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code
    .replace("apiUrl: 'https://app.daytona.io/api/', ", '').replace('serverUrl: `https://app.daytona.io/api`, ', ''));
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'deprecated-target' }), expect.objectContaining({ kind: 'config-field' }),
  ]) });
});

it('safely removes standalone cloud assignments without changing BOM, CRLF or adjacent statements', async () => {
  // Given
  const code = "\uFEFF" + prelude.replaceAll('\n', '\r\n') + "const config = {};\r\nconfig.apiUrl = 'https://app.daytona.io/api'; keep();\r\nconfig.serverUrl = `https://app.daytona.io/api/`;\r\nnew Daytona(config); new Daytona(config);\r\n";
  const root = temporaryProject({ 'index.ts': code });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(code.replace('@daytonaio/sdk', '@daytona/sdk')
    .replace("config.apiUrl = 'https://app.daytona.io/api';", '').replace('config.serverUrl = `https://app.daytona.io/api/`;\r\n', ''));
});

it('leaves an embedded cloud assignment intact with an override instead of breaking its surrounding expression', async () => {
  // Given
  const code = "const config = {}; use(config.apiUrl = 'https://app.daytona.io/api'); new Daytona(config);\n";
  const root = temporaryProject({ 'index.ts': prelude + code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it('preserves control-flow bodies when removing literal cloud assignments', async () => {
  // Given
  const code = "const config = {}; if (flag) config.apiUrl = 'https://app.daytona.io/api'; else keep();\nnew Daytona(config);\n";
  const root = temporaryProject({ 'index.ts': prelude + code });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code.replace("config.apiUrl = 'https://app.daytona.io/api';", ';'));
});

it('does not follow an imported spread even when the imported declaration is a literal', async () => {
  // Given
  const code = "import { base } from './base.js'; new Daytona({ ...base });\n";
  const root = temporaryProject({ 'index.ts': prelude + code, 'base.ts': "export const base = { apiKey: 'key' };\n" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('refuses an imported config rather than assuming its defining module has no other mutations', async () => {
  // Given
  const config = "const base = { apiUrl: 'https://app.daytona.io/api' };\nexport const config = { ...base };\nconfig.serverUrl = 'https://app.daytona.io/api';\n";
  const root = temporaryProject({ 'index.ts': prelude + "import { config } from './config.js'; new Daytona(config);\n", 'config.ts': config });
  const before = contents(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('does not write cloud config removals during dry analysis', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prelude + "const config = { ...{ apiUrl: 'https://app.daytona.io/api' } }; config.serverUrl = 'https://app.daytona.io/api'; new Daytona(config);\n" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ dryRun: true, changedFiles: ['index.ts'] });
});
