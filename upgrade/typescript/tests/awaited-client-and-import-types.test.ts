/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([true, false])('migrates awaited clients when SDK types are installed: %s', async installed => {
  // Given
  const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytonaio/sdk';
async function getClient(): Promise<Daytona> { return new Daytona(); }
const ready: Promise<Daytona> = getClient();
async function run() {
  (await getClient()).create();
  (await getClient()).list();
  (await ready).create();
  (await getClient()).snapshot.list();
  (await Promise.resolve(new Daytona())).create();
  const d = await getClient(); d.create();
}` });
  if (installed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  const output = contents(root)['app.ts'];
  for (const call of ['(await getClient()).v0.create()', '(await getClient()).v0.list()',
    '(await ready).v0.create()', '(await getClient()).v0.snapshot.list()', 'd.v0.create()']) expect(output).toContain(call);
  expect(output).not.toContain('snapshot.v0');
  if (installed) expect(output).toContain('(await Promise.resolve(new Daytona())).v0.create()');
  else expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]));
});

it('keeps a resolved unrelated awaited receiver unchanged', async () => {
  // Given
  const input = `import { Daytona } from '@daytonaio/sdk';
async function getClient() { return { create() {} }; }
async function run() { (await getClient()).create(); }
`;
  const root = temporaryProject({ 'app.ts': input });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['app.ts']).toBe(input.replace('@daytonaio/sdk', '@daytona/sdk'));
  expect(JSON.parse(result.output).items.filter((item: { kind: string }) => item.kind === 'fixme')).toEqual([]);
});

it.each(['ts', 'js'])('routes qualified import types in %s by their exported name', async extension => {
  // Given
  const expression = (name: string): string => `import('@daytonaio/sdk').${name}`;
  const input = extension === 'ts' ? `type S = ${expression('Sandbox')}; type I = ${expression('Image')}; type D = ${expression('Daytona')};` :
    `/** @param {${expression('Sandbox')}} sandbox */\nfunction use(sandbox) {}`;
  const root = temporaryProject({ [`app.${extension}`]: input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const output = contents(root)[`app.${extension}`];
  expect(output).toContain("import('@daytona/sdk/v0').Sandbox");
  if (extension === 'ts') {
    expect(output).toContain("import('@daytona/sdk/v0').Image");
    expect(output).toContain("import('@daytona/sdk').Daytona");
  }
  expect(JSON.parse(result.output).items.filter((item: { kind: string }) => item.kind === 'dynamic-import')).toEqual([]);
});

it.each(['Missing', 'Sandbox.Nested', ''])('reports unproven import type qualifier %s', async qualifier => {
  // Given
  const root = temporaryProject({ 'app.ts': `type T = import('@daytonaio/sdk')${qualifier ? `.${qualifier}` : ''};` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import', file: 'app.ts' })]));
});
