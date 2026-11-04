/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ModuleKind, ModuleResolutionKind, Project, ScriptTarget } from 'ts-morph';
import { cpSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { upgrade } from '../src/upgrade.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();

it.each([false, true])('rewrites proven type-level references with resolved SDK %s', async resolved => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': `import { Daytona as D } from '@daytonaio/sdk';
const daytona = new D();
type Method = typeof daytona.create;
type Member = D['create'];
type Instance = InstanceType<typeof D>['create'];
type Service = typeof daytona.snapshot.list;
type Root = D['close'];
`,
  });
  if (resolved) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(resolved ? 0 : 1);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
  const after = contents(root)['index.ts'];
  expect(after).toContain('typeof daytona.v0.create');
  expect(after).toContain("D['v0']['create']");
  expect(after).toContain("InstanceType<typeof D>['v0']['create']");
  expect(after).toContain('typeof daytona.v0.snapshot.list');
  expect(after).toContain("D['close']");
});

it('preserves unrelated and shadowed type-level names', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `import { Daytona } from '@daytona/sdk';
class Other { create() {} }
const other = new Other();
type A = typeof other.create;
type B = Other['create'];
function shadow() {
  class Daytona { create() {} }
  const daytona = new Daytona();
  type C = Daytona['create'];
  type D = typeof daytona.create;
}
`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.after).toBe(source.getFullText());
  expect(result.items).toEqual([]);
});

it('leaves generic unknown type-level references unchanged and idempotent', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', `import { Daytona } from '@daytona/sdk';
declare const client: unknown;
type A = typeof client.create;
type B = UnknownClient['create'];
`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.filter(entry => entry.kind === 'fixme')).toHaveLength(0);
  expect(result.after).toBe(source.getFullText());
  const second = project.createSourceFile('/second.ts', result.after);
  expect(upgrade(second, mapping, { root: '/', dropApiUrl: false }).after).toBe(result.after);
});

it.each(['typeof daytonaClient.create', "DaytonaClient['create']"])(
  'retains review for strong unresolved type identity when referencing %s', reference => {
    // Given
    const project = new Project({ useInMemoryFileSystem: true });
    const source = project.createSourceFile('/index.ts', `import { Daytona } from '@daytona/sdk';
declare const daytonaClient: unknown;
type Member = ${reference};`);
    // When
    const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
    // Then
    expect(result.items.filter(entry => entry.kind === 'fixme')).toHaveLength(1);
    expect(result.after).toContain(`type Member = ${reference};`);
  });

it('leaves migrated type references unchanged on a second run', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk'; const daytona = new Daytona(); type A = typeof daytona.v0.create; type B = Daytona['v0']['create']; type C = typeof daytona.v0.snapshot.list;",
  });
  const before = contents(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
});

it('preserves type-checking of migrated method signatures against the v0 namespace', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'node_modules/original-signatures/index.d.ts': "import type { Daytona } from '../@daytonaio/sdk/index.js'; export type OriginalCreate = Daytona['create'];",
    'index.ts': `import { Daytona } from '@daytonaio/sdk';
import type { OriginalCreate } from './node_modules/original-signatures/index.js';
const daytona = new Daytona();
type A = typeof daytona.create;
type B = Daytona['create'];
type C = InstanceType<typeof Daytona>['create'];
type Equal<X, Y> = (<T>() => T extends X ? 1 : 2) extends (<T>() => T extends Y ? 1 : 2) ? true : false;
type Assert<T extends true> = T;
type SameA = Assert<Equal<A, OriginalCreate>>;
type SameB = Assert<Equal<B, OriginalCreate>>;
type SameC = Assert<Equal<C, OriginalCreate>>;
`,
  });
  installFakeSdk(root);
  const options = { strict: true, target: ScriptTarget.ESNext, module: ModuleKind.NodeNext, moduleResolution: ModuleResolutionKind.NodeNext };
  const project = new Project({ compilerOptions: options });
  const source = project.addSourceFileAtPath(join(root, 'index.ts'));
  expect(source.getPreEmitDiagnostics()).toEqual([]);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  writeFileSync(join(root, 'node_modules/@daytona/sdk/package.json'), '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}');
  writeFileSync(join(root, 'node_modules/@daytona/sdk/index.d.ts'), "import type { Sandbox } from '@daytonaio/sdk'; export declare class Daytona { v0: { create(): Promise<Sandbox> }; }");
  // The rewritten root specifier resolves to the upgraded package, without network access.
  const next = new Project({ compilerOptions: options });
  expect(next.addSourceFileAtPath(join(root, 'index.ts')).getPreEmitDiagnostics().map(diagnostic => diagnostic.getMessageText())).toEqual([]);
});

it.each([false, true])('routes indexed service type queries through their owning client with resolved SDK %s', async resolved => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'index.ts': `import { Daytona } from '@daytonaio/sdk';
const daytona = new Daytona();
type List = typeof daytona.snapshot['list'];
type Keys = typeof daytona.snapshot['list' | 'create'];
type Root = typeof daytona['snapshot'];
`,
  });
  if (resolved) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(resolved ? 0 : 1);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
  const after = contents(root)['index.ts'];
  expect(after).toContain("typeof daytona.v0.snapshot['list']");
  expect(after).toContain("typeof daytona.v0.snapshot['list' | 'create']");
  expect(after).toContain("typeof daytona['v0']['snapshot']");
  const before = contents(root);
  await invoke(root, ['--strict']);
  expect(contents(root)).toEqual(before);
});

it('preserves overrides and unrelated InstanceType aliases', async () => {
  // Given
  const code = `import { Daytona } from '@daytonaio/sdk';
class Custom extends Daytona { create() { return Promise.resolve(new Sandbox()); } }
import { Sandbox } from '@daytonaio/sdk';
const daytona = new Custom();
type A = typeof daytona.create;
type B = Custom['create'];
type C = InstanceType<typeof Custom>['create'];
`;
  const root = temporaryProject({ 'index.ts': code, 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('typeof daytona.create');
  expect(contents(root)['index.ts']).toContain("Custom['create']");
  expect(contents(root)['index.ts']).toContain("InstanceType<typeof Custom>['create']");
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toEqual([]);
});

it('reports compound indexed keys rather than silently leaving moved members behind', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', "import { Daytona } from '@daytona/sdk'; type A = Daytona['create' | 'close'];");
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.filter(entry => entry.kind === 'fixme')).toHaveLength(1);
  expect(result.after).toContain("Daytona['create' | 'close']");
  expect(result.after).toContain('FIXME(daytona-upgrade)');
});
