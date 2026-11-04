/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { configConflictItems } from '../src/config-ownership.js';
import { projects, walk } from '../src/discovery.js';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  { code: "const other = { apiUrl: 'https://api.stripe.com' };", refused: false },
  { code: "const other = { serverUrl: 'https://api.stripe.com' };", refused: false },
  { code: "const custom = 'https://custom.example'; new Daytona({ apiUrl: custom });", refused: true },
  { code: "const config: DaytonaConfig = { apiUrl: 'https://custom.example' };", refused: true },
  { code: "process.env.DAYTONA_API_URL = 'https://custom.example';", refused: true },
  { code: "new Daytona({ apiUrl: 'https://app.daytona.io/api' });", refused: false },
])('preserves conflict URL safety without rewriting when $code', async ({ code, refused }) => {
  // Given an ordinary source whose alias resolves differently across configs.
  const source = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n" +
    "import { client } from '@/d'; client.create();\n" + code;
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.app.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    'tsconfig.spec.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["stubs/*"]}},"include":["src"]}',
    'src/d.ts': 'export const client = { create() {} };',
    'stubs/d.ts': 'export const client = { create() {} };',
    'src/main.ts': source,
  });
  installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the conflict stays manual; only Daytona endpoint overrides block the run.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'src/main.ts', kind: 'fixme' }));
  expect(result.code).toBe(refused ? 2 : 1);
  expect(report.refused?.reason).toBe(refused ? 'custom-api-url' : undefined);
  expect(contents(root)['src/main.ts']).toBe(source);
  if (refused) expect(contents(root)).toEqual(before);
  else expect(report.items).toEqual([expect.objectContaining({ file: 'src/main.ts', kind: 'fixme' })]);
});

it.each([
  { app: 'src/*', spec: 'missing/*', conflict: true },
  { app: 'missing/*', spec: 'src/*', conflict: true },
  { app: 'missing/*', spec: 'also-missing/*', conflict: false },
  { app: 'src/*', spec: 'src/*', conflict: false },
])('compares unresolved alias targets when app=$app and spec=$spec', ({ app, spec, conflict }) => {
  // Given configs that include the same source but may not resolve its alias.
  const root = temporaryProject({
    'tsconfig.app.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@/*': [app] } }, include: ['src'] }),
    'tsconfig.spec.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@/*': [spec] } }, include: ['src'] }),
    'src/d.ts': 'export const client = { create() {} };',
    'src/main.ts': "import { client } from '@/d'; client.create();",
  });
  // When
  const items = configConflictItems(projects(root, walk(root)), root);
  // Then two unresolved targets agree, but one unresolved target is a conflict.
  expect(items).toEqual(conflict ? [expect.objectContaining({ file: 'src/main.ts', kind: 'fixme' })] : []);
});
