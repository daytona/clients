/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const config = "const config = { apiKey: 'key', apiUrl: 'https://app.daytona.io/api' };\n";

it.each([
  "new Daytona(config); new URL('/health', config.apiUrl);",
  "new Daytona(config); const other = { ...config }; consume(other);",
  'export { config }; new Daytona(config);',
  'const alias = config; new Daytona(alias); console.log(config.apiUrl);',
  'const alias = config; new Daytona(config); console.log(alias.apiUrl);',
  'new Daytona(config); JSON.stringify(config);',
])('preserves shared Cloud config when another reader exists: %s', async code => {
  // Given a constructor and another reader of the same object.
  const root = temporaryProject({ 'app.ts': prefix + config + code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then no reader loses its URL; the constructor is marked for manual repair.
  const report: Report = JSON.parse(result.output);
  const output = contents(root)['app.ts'] ?? '';
  expect(output).toContain(config);
  for (const statement of code.split(';').map(value => value.trim()).filter(Boolean)) expect(output).toContain(statement);
  expect(output).toContain('FIXME(daytona-upgrade):');
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'config-field', line: 3 }));
});

it('keeps property readers intact without an override', async () => {
  // Given a shared config.apiUrl property reader.
  const root = temporaryProject({ 'app.ts': prefix + config + "new Daytona(config); new URL('/health', config.apiUrl);" });
  // When
  const result = await invoke(root, ['--json']);
  // Then the tool leaves the shared field in place rather than silently breaking it.
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain(config);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field' }));
});

it('still removes Cloud fields when all alias readers are constructors', async () => {
  // Given constructor-only use through an alias and a spread.
  const root = temporaryProject({ 'app.ts': prefix + config + 'const alias = config; new Daytona(alias); new Daytona({ ...config });' });
  // When
  const result = await invoke(root, ['--json']);
  // Then no manual copy is needed and the existing safe removal remains.
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).not.toContain('apiUrl');
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'config-field')).toEqual([]);
});

it('preserves a config read by a local subclass constructor', async () => {
  // Given a local constructor that uses the URL for something other than super.
  const root = temporaryProject({ 'app.ts': prefix + config +
    "class Custom extends Daytona { constructor(options) { new URL('/health', options.apiUrl); super(options); } }\nconst Client = Custom; new Client(config);" });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then the constructor's other reader retains its original URL too.
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain(config);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field' }));
});
