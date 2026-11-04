/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const dependency = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' } as const;
const overrides = [
  'static createAxiosInstance() { return 42; }',
  'static createAxiosInstance = () => 42;',
  "static ['createAxiosInstance']() { return 42; }",
  'static [`createAxiosInstance`] = () => 42;',
] as const;
const uses = [
  'Child.createAxiosInstance();',
  "Child['createAxiosInstance']();",
  'const { createAxiosInstance: factory } = Child;',
] as const;
const customCases = overrides.flatMap(override => [
  `class Child extends Daytona { ${override} }`,
  `class Base extends Daytona { ${override} }\nclass Child extends Base {}`,
  `const Base = class extends Daytona { ${override} };\nconst Parent = Base;\nclass Child extends Parent {}`,
].flatMap(declaration => uses.map(use => `${declaration}\n${use}`)));

it.each(customCases.flatMap(source => [false, true].map(installed => ({ source, installed }))))(
  'keeps custom static implementations clean when installed=$installed: $source', async ({ source, installed }) => {
    // Given
    const root = temporaryProject({ ...dependency, 'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${source}` });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(result.code).toBe(installed ? 0 : 1);
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
    expect(contents(root)['index.ts']).toBe(`import { Daytona } from '@daytona/sdk';\n${source}`);
  });

const inheritedCases = [
  'class Child extends Daytona {}',
  'class Base extends Daytona {}\nclass Child extends Base {}',
  'class Child extends Daytona { createAxiosInstance() { return 42; } }',
  'class Child extends Daytona { static otherHelper() { return 42; } }',
] as const;
it.each(inheritedCases.flatMap(declaration => [false, true].map(installed => ({ declaration, installed }))))(
  'still reports an inherited SDK static helper when installed=$installed: $declaration', async ({ declaration, installed }) => {
    // Given
    const root = temporaryProject({ ...dependency, 'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${declaration}\nChild.createAxiosInstance();` });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(result.code).toBe(1);
    const report: Report = JSON.parse(result.output);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme', message: expect.stringContaining('createAxiosInstance') }));
  });

const bindings = [
  'createAxiosInstance',
  'createAxiosInstance: factory',
  "'createAxiosInstance': factory",
  "['createAxiosInstance']: factory",
  '[`createAxiosInstance`]: factory',
] as const;
const constructors = [
  { declaration: '', receiver: 'Daytona' },
  { declaration: 'const Alias = Daytona;', receiver: 'Alias' },
  { declaration: 'class Base extends Daytona {}\nclass Child extends Base {}', receiver: 'Child' },
] as const;
const extractionCases = constructors.flatMap(({ declaration, receiver }) => bindings.map(binding =>
  `${declaration}\nconst { ${binding} } = ${receiver};`));

it.each(extractionCases.flatMap(source => [false, true].map(installed => ({ source, installed }))))(
  'reports static helper destructuring when installed=$installed: $source', async ({ source, installed }) => {
    // Given
    const root = temporaryProject({ ...dependency, 'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${source}` });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(result.code).toBe(1);
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(1);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme', message: expect.stringContaining('createAxiosInstance') }));
    expect(contents(root)['index.ts']).toMatch(/FIXME\(daytona-upgrade\).*\nconst \{/);
    expect(contents(root)['index.ts']).not.toContain('.v0.createAxiosInstance');
  });

it.each([
  'const Other = { createAxiosInstance: () => 42 };\nconst { createAxiosInstance } = Other;',
  'const { prototype } = Daytona;',
  "const key = 'createAxiosInstance';\nconst { [key]: factory } = Daytona;",
  'const { ...rest } = Daytona;',
])('leaves unrelated or unproven destructuring clean when using %s', async source => {
  // Given
  const root = temporaryProject({ ...dependency, 'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${source}` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(`import { Daytona } from '@daytona/sdk';\n${source}`);
});

it.each([
  { source: 'const Alias = Daytona;\nconst { createAxiosInstance: factory } = Alias;', fixmes: 1 },
  { source: 'class Base extends Daytona { static createAxiosInstance = () => 42; }\nclass Child extends Base {}\nChild.createAxiosInstance();', fixmes: 0 },
])('keeps static-helper output idempotent when using $source', async ({ source, fixmes }) => {
  // Given
  const root = temporaryProject({ ...dependency, 'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${source}` });
  installFakeSdk(root);
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(contents(root)).toEqual(before);
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(fixmes);
  expect(report.changedFiles).toEqual([]);
});

it.each([
  'const Alias = Child;\nAlias.createAxiosInstance();',
  'const Alias: typeof Daytona = Child;\nAlias.createAxiosInstance();',
])('retains subclass instance proof when a static override is accessed through %s', async use => {
  // Given
  const source = `import { Daytona } from '@daytonaio/sdk';\nclass Child extends Daytona { static createAxiosInstance = () => 42; }\n${use}\nconst client = new Child();\nclient.create();`;
  const root = temporaryProject({ ...dependency, 'index.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk').replace('client.create()', 'client.v0.create()'));
});
