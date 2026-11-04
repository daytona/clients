/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { name: 'proven named export', provider: "import { Daytona } from '@daytonaio/sdk'; export const api = new Daytona();", fixmes: 1 },
  { name: 'proven re-export', provider: "export { api } from './origin';", fixmes: 1 },
  { name: 'unknown resolved export in a Daytona module', provider: "import type { Daytona } from '@daytonaio/sdk'; export const api = externalValue;", fixmes: 0 },
  { name: 'unresolved local import', provider: undefined, fixmes: 0 },
])('requires linked receiver evidence for dynamic access to a $name', async ({ provider, fixmes }) => {
  // Given no installed SDK types, but local provenance or an unresolved local module.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    ...(provider === undefined ? {} : { 'local.ts': provider }),
    'origin.ts': "import { Daytona } from '@daytonaio/sdk'; export const api = new Daytona();",
    'index.ts': "import { api } from './local'; api[key]();",
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only a receiver-linked origin requires review; unrelated imports do not.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'index.ts' && item.kind === 'fixme')).toHaveLength(fixmes);
  if (fixmes) expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade): review dynamic member lookup');
  else expect(contents(root)['index.ts']).toBe("import { api } from './local'; api[key]();");
  expect(contents(root)['index.ts']).toContain('api[key]()');
  // The separate origin module constructs Daytona without installed types in every case.
  expect(result.code).toBe(1);
});

it.each([
  { name: 'resolved non-Daytona local', module: './local', provider: 'export const api = { create() {} };' },
  { name: 'unknown local export without Daytona', module: './local', provider: 'export const api = externalValue;' },
  { name: 'installed npm', module: 'external', provider: 'export const api = externalValue;' },
])('leaves dynamic access untouched when the receiver is $name', async ({ module, provider }) => {
  // Given no Daytona provenance in the resolved module.
  const source = `import { api } from '${module}'; api[key]();`;
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'local.ts': provider,
    'node_modules/external/package.json': '{"main":"index.js"}',
    'node_modules/external/index.js': provider,
    'index.ts': source,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});
