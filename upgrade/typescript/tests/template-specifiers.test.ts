/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();
const sharedHelpers = ['mock', 'doMock', 'unmock', 'dontMock', 'requireActual', 'requireMock', 'importActual', 'importMock'] as const;
const helpers = (['jest', 'vi'] as const).flatMap(tester => {
  const module = tester === 'jest' ? '@jest/globals' : 'vitest';
  return [...sharedHelpers, ...(tester === 'jest' ? ['unstable_mockModule', 'deepUnmock', 'createMockFromModule', 'genMockFromModule'] : ['doUnmock'])]
    .flatMap(helper => [
      { prefix: `${tester}.${helper}(`, suffix: ');', kind: 'mock-target' },
      { prefix: `const { ${tester}: t } = require(\`${module}\`); t.${helper}(`, suffix: ');', kind: 'mock-target' },
      { prefix: `const g = require(\`${module}\`); g.${tester}.${helper}(`, suffix: ');', kind: 'mock-target' },
    ]);
});
const cases = [
  { prefix: 'require(', suffix: ');', kind: 'dynamic-import' },
  { prefix: 'import(', suffix: ');', kind: 'dynamic-import' },
  { prefix: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url); require(", suffix: ');', kind: 'dynamic-import' },
  { prefix: 'type Sdk = typeof import(', suffix: ');', kind: 'private-import' },
  ...helpers,
] as const;

it.each(cases)('rewrites static templates with their existing review kind when using $prefix', ({ prefix, suffix, kind }) => {
  // Given
  const input = `${prefix}\`@daytonaio/sdk\`${suffix}`;
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(`${prefix}\`@daytona/sdk\`${suffix}`);
  expect(result.items.map(entry => entry.kind)).toEqual([kind]);
});

it.each(cases)('keeps migrated templates byte-stable when using $prefix', ({ prefix, suffix }) => {
  // Given
  const input = `${prefix}\`@daytona/sdk\`${suffix}`;
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(input);
});

it.each(cases.filter(entry => entry.kind !== 'private-import'))('reviews interpolated Daytona templates without rewriting when using $prefix', ({ prefix, suffix }) => {
  // Given
  const input = `${prefix}\`@daytonaio/\${name}\`${suffix}`;
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result.edits).toEqual([]);
  expect(result.items.map(entry => entry.kind)).toEqual(['dynamic-import']);
});

it.each([
  'require(`other/${name}`);',
  'import(`other/${name}`);',
  'vi.mock(`other/${name}`);',
  'const text = `@daytonaio/${name}`;',
  'function require(name: string) {} require(`@daytonaio/${name}`);',
  'const vi = { mock(name: string) {} }; vi.mock(`@daytonaio/${name}`);',
  'vi.mock("other", `@daytonaio/${name}`);',
  'vi.mock("other", `@daytonaio/sdk`);',
])('preserves non-specifier templates without reporting when given %s', input => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result).toEqual({ edits: [], items: [] });
});

it.each(['require', 'import', 'vi.mock'])('preserves private template subpaths with review when using %s', callee => {
  // Given
  const input = `${callee}(\`@daytonaio/sdk/dist/internal\`);`;
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result.edits).toEqual([]);
  expect(result.items.map(entry => entry.kind)).toEqual(['private-import']);
});

it.each([
  'const { Daytona, Sandbox } = require(`@daytonaio/sdk`); const d = new Daytona(); d.create();',
  'const sdk = require(`@daytonaio/sdk`); const d = new sdk.Daytona(); d.create();',
  'const sdk = await import(`@daytonaio/sdk`); const d = new sdk.Daytona(); d.create();',
])('migrates client bindings from template specifiers through the CLI when given %s', async input => {
  // Given
  const root = temporaryProject({ 'index.ts': input });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain('d.v0.create()');
  expect(contents(root)['index.ts']).not.toContain('@daytonaio/sdk');
});

it('keeps template specifier CLI output byte-stable on a rerun', async () => {
  // Given
  const input = cases.map(({ prefix, suffix }) => `${prefix}\`@daytonaio/sdk\`${suffix}`).join('\n');
  const root = temporaryProject({ 'index.ts': input });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
});

it.each(['`@daytona/sdk`', "'@daytona/sdk'"])('reports unresolved types for an uninstalled %s dynamic import', async specifier => {
  // Given an SDK loaded through a static specifier without node_modules.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': `const { Daytona } = await import(${specifier});\nnew Daytona().v0.create();\n`,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then both literal forms get the same missing-types notice.
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'types-unresolved' })]));
  expect(contents(root)['index.ts']).toContain('new Daytona().v0.create();');
});
