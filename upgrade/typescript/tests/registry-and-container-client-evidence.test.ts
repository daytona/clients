/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}';

it.each([false, true])('reviews a setter registry when SDK types are installed=%s', async typed => {
  // Given a registry with no SDK import, populated from a separate bootstrap module.
  const root = temporaryProject({ 'package.json': manifest,
    'registry.js': 'export let client; export function setClient(c) { client = c; }',
    'main.js': "import { Daytona } from '@daytonaio/sdk'; import { setClient } from './registry.js'; setClient(new Daytona());",
    'use.js': "import { client } from './registry.js'; client.create();" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the consumer is not silently accepted or guessed to be a client.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'use.js', kind: 'fixme' }));
  expect(contents(root)['use.js']).not.toContain('client.v0.create()');
  expect(result.code).toBe(1);
});

it.each([false, true])('reviews an exported container when SDK types are installed=%s', async typed => {
  // Given a Map whose payload is a client, not the Map itself.
  const root = temporaryProject({ 'package.json': manifest,
    'registry.js': 'export const container = new Map();',
    'main.js': "import { Daytona } from '@daytonaio/sdk'; import { container } from './registry.js'; container.set('d', new Daytona());",
    'use.js': "import { container } from './registry.js'; container.get('d').create();" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the uncertain payload receives review rather than a rewrite.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'use.js', kind: 'fixme' }));
  expect(contents(root)['use.js']).not.toContain("container.get('d').v0.create()");
  expect(result.code).toBe(1);
});

it.each([false, true])('handles a structural registry when SDK types are installed=%s', async typed => {
  // Given an interface, which describes a method but does not prove an SDK instance.
  const root = temporaryProject({ 'package.json': manifest,
    'registry.ts': 'interface Client { create(): Promise<unknown> } export let client: Client; export function setClient(c: Client) { client = c; }',
    'main.ts': "import { Daytona } from '@daytonaio/sdk'; import { setClient } from './registry'; setClient(new Daytona());",
    'use.ts': "import { client } from './registry'; client.create();" });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the concrete non-Daytona interface suppresses hints despite a linked setter value.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.ts' && item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['use.ts']).toBe("import { client } from './registry'; client.create();");
  expect(report.items.filter(item => item.kind === 'types-unresolved')).toHaveLength(typed ? 0 : 1);
  expect(result.code).toBe(typed ? 0 : 1);
});

it.each([false, true])('handles a declared global client when SDK types are installed=%s', async typed => {
  // Given a global declaration whose SDK evidence lives outside its consumer.
  const root = temporaryProject({ 'package.json': manifest,
    'main.ts': "import { Daytona } from '@daytonaio/sdk'; declare global { var daytona: Daytona } globalThis.daytona = new Daytona();",
    'use.ts': 'daytona.create();' });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then even a global written from another module cannot be accepted silently.
  expect(contents(root)['use.ts']).toContain('FIXME(daytona-upgrade)');
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.ts' && item.kind === 'fixme')).toHaveLength(1);
});

it.each([
  "resolve('daytona')", "resolve('DaYtOnA')", 'ctx.daytona', 'req.app.locals.daytona', 'this.daytona',
  'globalThis.client', 'injected',
].map(receiver => ({ receiver, fixmes: ['ctx.daytona', 'req.app.locals.daytona', 'this.daytona'].includes(receiver) ? 1 : 0 })))
('requires receiver identity for injected receivers despite other SDK imports: $receiver', async ({ receiver, fixmes }) => {
  // Given project-wide SDK evidence and an uncertain injection receiver.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': "import { Daytona } from '@daytonaio/sdk';",
    'use.js': `${receiver}.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only a Daytona-named receiver path, not a string argument or generic name, is reviewed.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(fixmes);
  if (fixmes === 0) expect(contents(root)['use.js']).toBe(`${receiver}.create();`);
  expect(result.code).toBe(fixmes);
});

it.each(['@daytonaio/sdk', '@daytona/sdk', '@daytonaio/sdk/v0', '@daytona/sdk/v0'].flatMap(module =>
  ['import', 'require'].map(style => ({ module, style }))))('ignores unrelated project-wide $style evidence from $module', async ({ module, style }) => {
  // Given evidence that is not on the traced registry import path.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': style === 'import' ? `import '${module}';` : `require('${module}');`,
    'registry.js': 'export const client = globalThis.injected;',
    'use.js': "import { client } from './registry.js'; client.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then an unrelated SDK import does not make a generic global value reviewable.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['use.js']).toBe("import { client } from './registry.js'; client.create();");
  expect(report.items.filter(item => item.kind === 'types-unresolved')).toHaveLength(module === '@daytonaio/sdk/v0' ? 0 : 1);
  expect(report.items.filter(item => item.kind === 'private-import')).toHaveLength(module === '@daytonaio/sdk/v0' ? 1 : 0);
  expect(result.code).toBe(1);
});

it('leaves an external resolver clean despite a Daytona string argument', async () => {
  // Given an external DI API, not an external application factory.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': "import { Daytona } from '@daytonaio/sdk';",
    'use.js': "import { resolve } from 'injection'; resolve('DaYtOnA').create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then an arbitrary string key does not override the conclusive external origin.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['use.js']).toBe("import { resolve } from 'injection'; resolve('DaYtOnA').create();");
  expect(result.code).toBe(0);
});

it.each(['freeClient.create();', "import { client } from './registry.js'; client.create();"])(
  'leaves unknown values clean when no source imports Daytona: %s', async source => {
    // Given a dependency alone, without source-level evidence.
    const root = temporaryProject({ 'package.json': manifest, 'registry.js': 'export const client = globalThis.injected;', 'use.js': source });
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then no project-wide fallback applies.
    const report: Report = JSON.parse(result.output);
    expect(report.items).toEqual([]);
    expect(contents(root)['use.js']).toBe(source);
    expect(result.code).toBe(0);
  });
