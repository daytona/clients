/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const localImports = {
  'package.json': '{"workspaces":["packages/*"],"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  'packages/infra/package.json': '{"name":"@workspace/infra"}',
  'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
} as const;

it.each(['web', 'apps/web'].flatMap(directory => [true, false].map(typed => ({ directory, typed }))))('uses child aliases with an auxiliary root ($directory, typed=$typed)', async ({ directory, typed }) => {
  // Given a root config with implicit includes and no paths (auxiliary for the alias), and a child config that maps it.
  const root = temporaryProject({
    'tsconfig.json': '{"compilerOptions":{"strict":true}}',
    [`${directory}/package.json`]: manifest,
    [`${directory}/tsconfig.json`]: '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    [`${directory}/src/daytona.ts`]: "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    [`${directory}/src/app.ts`]: "import { client } from '@/daytona'; client.create();",
  });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the child config owns the file: migrate with types, otherwise mark the receiver for review.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual(typed ? [] :
    [expect.objectContaining({ file: `${directory}/src/app.ts`, line: 1, kind: 'fixme' })]);
  expect(contents(root)[`${directory}/src/app.ts`]).toBe(typed ?
    "import { client } from '@/daytona'; client.v0.create();" :
    "import { client } from '@/daytona';\n// FIXME(daytona-upgrade): if `client` is a Daytona client, use `client.v0.create(...)` (SDK 2.x moved client methods to .v0)\nclient.create();");
  expect(result.code, result.output).toBe(typed ? 0 : 1);
});

it.each(['web', 'apps/web'])('reports an unresolved alias receiver in %s without dependency types', async directory => {
  // Given a missing alias target, with no direct SDK import in the consumer.
  const root = temporaryProject({
    'tsconfig.json': '{}',
    [`${directory}/package.json`]: manifest,
    [`${directory}/tsconfig.json`]: '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    [`${directory}/src/app.ts`]: "import { client } from '@/daytona'; client.create();",
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: `${directory}/src/app.ts`, kind: 'fixme' }));
  expect(contents(root)[`${directory}/src/app.ts`]).toContain('FIXME(daytona-upgrade)');
  expect(contents(root)[`${directory}/src/app.ts`]).toContain('client.create()');
  expect(result.code).toBe(1);
});

it('keeps imported files excluded by a child config under their including root config', async () => {
  // Given a child importing a root source, with incompatible path aliases in each config.
  const root = temporaryProject({
    'package.json': manifest,
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    'src/daytona.ts': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    'src/app.ts': "import { client } from '@/daytona'; client.create();",
    'web/tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    'web/src/app.ts': "import '../../src/app';",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['src/app.ts']).toContain('client.v0.create()');
  expect(result.code, result.output).toBe(0);
});

it.each(['create', 'get', 'list', 'start', 'stop', 'delete', 'fork', 'volume', 'snapshot', 'secret', 'warmPool'])(
  'ignores an unresolved generic imported receiver for moved member %s', async member => {
    // Given
    const root = temporaryProject({ ...localImports,
      'index.ts': `import { api } from '@workspace/infra'; api.${member}();` });
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(report.items).toEqual([]);
    expect(contents(root)['index.ts']).toBe(`import { api } from '@workspace/infra'; api.${member}();`);
    expect(result.code).toBe(0);
  },
);

it.each(['./missing', '@/missing', '@workspace/infra'].flatMap(module => [
  `import { client as api } from '${module}'; api.create();`,
  `import api from '${module}'; api.volume.list();`,
  `import * as infra from '${module}'; infra.start();`,
  `import infra from '${module}'; const { api: local } = infra; local.get('id');`,
]))('leaves unresolved generic imported moved-member receivers unchanged: %s', async source => {
  // Given
  const root = temporaryProject({ ...localImports, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});

it.each([
  { annotation: 'unknown', sdk: true, fixmes: 0 },
  { annotation: 'unknown', sdk: false, fixmes: 0 },
  { annotation: '{ create(): void }', sdk: false, fixmes: 0 },
])('ignores unrelated module imports for unknown origins: $annotation, sdk=$sdk', async ({ annotation, sdk, fixmes }) => {
  // Given a declaration whose unknown type is unrelated to the module's SDK import.
  const root = temporaryProject({ 'package.json': manifest,
    'local.ts': `${sdk ? "import type { Daytona } from '@daytonaio/sdk'; " : ''}export declare const api: ${annotation};`,
    'index.ts': "import { api } from './local'; api.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(fixmes);
  expect(contents(root)['index.ts']).toBe("import { api } from './local'; api.create();");
  expect(result.code).toBe(fixmes);
});

it.each([
  "import infra from '@workspace/infra'; const { api } = infra; api.create();",
  "import infra from '@workspace/infra'; const { api: client } = infra; client.create();",
])('leaves an unresolved generic imported receiver through destructuring unchanged: %s', async source => {
  // Given an unresolved workspace import whose receiver is destructured locally.
  const root = temporaryProject({ ...localImports, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});

it('rewrites a typed client receiver reached through destructuring', async () => {
  // Given an imported object whose destructured client is typed by the SDK declarations.
  const root = temporaryProject({ 'package.json': manifest,
    'infra.ts': "import { Daytona } from '@daytonaio/sdk'; export const infra = { api: new Daytona() };",
    'index.ts': "import { infra } from './infra'; const { api: client } = infra; client.create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)['index.ts']).toContain('client.v0.create()');
  expect(contents(root)['index.ts']).not.toContain('FIXME(daytona-upgrade)');
  expect(result.code).toBe(0);
});

it.each([
  "import { api } from './missing'; function work(api: { create(): void }) { api.create(); }",
  "import { api } from './local'; api.create();",
  "import { api } from './missing'; const local = { create() {} }; local.create();",
  "import { api } from './missing'; api.unrelated();",
])('ignores resolved unrelated receivers or unmoved members: %s', async source => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': source,
    'local.ts': 'export const api = { create() {} };' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});
