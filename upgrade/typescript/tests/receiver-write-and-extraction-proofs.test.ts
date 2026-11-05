/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { performance } from 'node:perf_hooks';
import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { bindingIsStable } from '../src/stable-bindings.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { expectWithinBudget } from './budget.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const codeWithoutComments = (code: string): string => code.split('\n').filter(line => !line.trimStart().startsWith('//')).join('\n');

it.each(['index.ts', 'index.js'].flatMap(file => [true, false].flatMap(types => [
  "const holder = { client: new Daytona() }; holder.client = other; holder.client.create();",
  "const holder = { client: new Daytona() }; const alias = holder; alias.client = other; holder.client.create();",
  "const holder = { client: new Daytona() }; holder['client'] = other; holder.client.create();",
  "let holder = { client: new Daytona() }; holder = other; holder.client.create();",
].map(code => ({ file, types, code })))))('keeps overwritten object properties unproven in $file with types=$types: $code', async ({ file, types, code }) => {
  // Given
  const root = temporaryProject({ [file]: prefix + code });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(codeWithoutComments(contents(root)[file] ?? '')).not.toContain('.v0.');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it.each([
  "Pick<Daytona, 'create'>", 'Partial<Daytona>', 'Client', 'InterfaceClient',
])('does not treat a structural %s receiver as an SDK instance', async type => {
  // Given
  const code = `interface Client extends Pick<Daytona, 'create'> {}\ninterface InterfaceClient extends Daytona {}\ndeclare const client: ${type};\nclient.create();`;
  const root = temporaryProject({ 'index.ts': prefix + code });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(codeWithoutComments(contents(root)['index.ts'] ?? '')).not.toContain('client.v0.');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it.each([
  'const f = daytona.create.bind(daytona);', 'daytona.create.call(daytona);',
  'daytona.create.apply(daytona, []);', 'const f = daytona.create;', 'consume(daytona.create);',
  "const f = daytona['create'];",
])('preserves method receiver semantics for %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + 'const daytona = new Daytona();\n' + code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(codeWithoutComments(contents(root)['index.ts'] ?? '')).not.toContain('.v0.');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it('rewrites service values and directly invoked methods on proven clients', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + 'const daytona = new Daytona();\nconst service = daytona.snapshot;\n(daytona.create)();\n' });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain('const service = daytona.v0.snapshot;');
  expect(contents(root)['index.ts']).toContain('(daytona.v0.create)();');
});

it('reports client destructuring without moving the binding', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + 'const daytona = new Daytona();\nconst { snapshot, create } = daytona;\n' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(codeWithoutComments(contents(root)['index.ts'] ?? '')).toContain('const { snapshot, create } = daytona;');
  expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
});

it('indexes writes once when checking 600 client bindings', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', Array.from({ length: 600 }, (_, i) =>
    `let client${i} = new Daytona(); client${i} = other; client${i}.create();`).join('\n'));
  const variables = source.getVariableDeclarations();
  const start = performance.now();
  // When
  const stable = variables.map(bindingIsStable);
  const elapsed = performance.now() - start;
  // Then
  expect(stable).toEqual(Array.from({ length: 600 }, () => false));
  expectWithinBudget(elapsed, 2000);
});
