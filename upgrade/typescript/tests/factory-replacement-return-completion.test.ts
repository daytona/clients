/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { replacementHasV0 } from '../src/mock-replacement-shape.js';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  { body: 'if (flag) return { v0: { create() {} } };', proven: false },
  { body: 'if (flag) { return { v0: { create() {} } }; } else {}', proven: false },
  { body: 'if (flag) return { v0: { create() {} } }; else return { v0: { create() {} } };', proven: true },
  { body: 'if (flag) return { v0: { create() {} } }; throw new Error();', proven: true },
  { body: 'if (flag) return { v0: { create() {} } }; return;', proven: false },
  { body: '{ return { v0: { create() {} } }; }', proven: true },
])('proves all normally completing paths when $body', ({ body, proven }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `function replacement() { ${body} }`);
  // When
  const result = replacementHasV0(source.getFunctionOrThrow('replacement'), [null, 'v0', 'create', null]);
  // Then
  expect(result).toBe(proven);
});

it('emits a review item when a factory implementation can fall through', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'client.ts': "import { Daytona } from '@daytonaio/sdk'; export function getDaytona() { return new Daytona(); }",
    'run.ts': "import { getDaytona } from './client'; getDaytona().create();",
    'index.ts': "import { getDaytona } from './client'; jest.mocked(getDaytona).mockImplementation(() => { if (flag) return { v0: { create() {} } }; });",
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', file: 'index.ts' }));
});

it.each([
  { input: "load(() => import('@daytonaio/sdk'), () => import('@daytonaio/sdk'));", metadata: false, count: 2 },
  { input: "load(() => import('@daytonaio/sdk'), { packageName: '@daytonaio/sdk' });", metadata: true, count: 1 },
  { input: "load(() => import('@daytonaio/sdk'), '@daytonaio/sdk');", metadata: false, count: 1 },
  { input: "load(() => import('@daytonaio/sdk'), { read() { return '@daytonaio/sdk'; } });", metadata: false, count: 1 },
  { input: "load(() => import('@daytonaio/sdk'), { nested: { packageName: '@daytonaio/sdk' } });", metadata: false, count: 1 },
])('limits metadata guidance to same-call object argument values when $input', ({ input, metadata, count }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, loadMapping(), '/');
  // Then
  expect(result.items).toHaveLength(count);
  expect(result.items.every(item => item.message.includes('also update the adjacent') === metadata)).toBe(true);
  const output = applyEdits(input, result.edits);
  expect(output.match(/import\('@daytona\/sdk'\)/gu)).toHaveLength(count);
  if (metadata) expect(output).toContain("packageName: '@daytonaio/sdk'");
});
