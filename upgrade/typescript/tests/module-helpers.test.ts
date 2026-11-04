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
const helpers = ['mock', 'doMock', 'unmock', 'dontMock', 'requireActual', 'requireMock', 'importActual', 'importMock'] as const;
const bindings = ['global', 'named', 'alias', 'namespace', 'CJS named', 'CJS alias', 'CJS namespace'] as const;
const cases = (['jest', 'vi'] as const).flatMap(tester => {
  const module = tester === 'jest' ? '@jest/globals' : 'vitest';
  return bindings.flatMap(binding => {
    const variants = {
      global: { setup: '', receiver: tester },
      named: { setup: `import { ${tester} } from '${module}';\n`, receiver: tester },
      alias: { setup: `import { ${tester} as t } from '${module}';\n`, receiver: 't' },
      namespace: { setup: `import * as g from '${module}';\n`, receiver: `g.${tester}` },
      'CJS named': { setup: `const { ${tester} } = require('${module}');\n`, receiver: tester },
      'CJS alias': { setup: `const { ${tester}: t } = require('${module}');\n`, receiver: 't' },
      'CJS namespace': { setup: `const g = require('${module}');\n`, receiver: `g.${tester}` },
    } as const;
    return [...helpers, ...(tester === 'jest' ? ['unstable_mockModule', 'deepUnmock', 'createMockFromModule', 'genMockFromModule'] : ['doUnmock'])].map(helper => ({
      name: `${tester}.${helper} (${binding})`, ...variants[binding], helper,
    }));
  });
});

it.each(cases)('renames the package argument and reports review when using $name', ({ setup, receiver, helper }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const input = `${setup}${receiver}.${helper}('@daytonaio/sdk');\n`;
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(`${setup}${receiver}.${helper}('@daytona/sdk');\n`);
  expect(result.items).toMatchObject([{ kind: 'mock-target', file: 'index.ts' }]);
});

it.each(cases)('preserves private subpaths with a review item when using $name', ({ setup, receiver, helper }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const input = `${setup}${receiver}.${helper}('@daytonaio/sdk/dist/internal');\n`;
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result.edits).toEqual([]);
  expect(result.items).toMatchObject([{ kind: 'private-import' }]);
});

it.each(cases)('leaves migrated package arguments unchanged when using $name', ({ setup, receiver, helper }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const input = `${setup}${receiver}.${helper}('@daytona/sdk');\n`;
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(input);
  expect(result.items).toMatchObject([{ kind: 'mock-target' }]);
});

it.each(helpers.flatMap(helper => [
  { helper, setup: `import { ${helper} as load } from 'vitest';\n`, callee: 'load' },
  { helper, setup: "import * as t from 'vitest';\n", callee: `t.${helper}` },
  { helper, setup: `const { ${helper}: load } = require('vitest');\n`, callee: 'load' },
  { helper, setup: "const t = require('vitest');\n", callee: `t.${helper}` },
]))('renames targets when $helper is a direct export via $setup', ({ setup, callee }) => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const input = `${setup}${callee}('@daytonaio/sdk');\n`;
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(`${setup}${callee}('@daytona/sdk');\n`);
  expect(result.items).toMatchObject([{ kind: 'mock-target' }]);
});

it.each([
  "function requireActual(name: string) { return name; }\nrequireActual('@daytonaio/sdk');",
  "const obj = { mock(name: string) { return name; } };\nobj.mock('@daytonaio/sdk');",
  "const jest = { mock(name: string) { return name; } };\njest.mock('@daytonaio/sdk');",
  "function f(vi: unknown) { vi.doMock('@daytonaio/sdk'); }",
  "import { vi } from 'other';\nvi.importActual('@daytonaio/sdk');",
  "const { jest } = require('other');\njest.doMock('@daytonaio/sdk');",
  "const g = require('other');\ng.jest.doMock('@daytonaio/sdk');",
  "function f(require: Function) { const { vi } = require('vitest'); vi.mock('@daytonaio/sdk'); }",
  "const { ...jest } = require('@jest/globals');\njest.mock('@daytonaio/sdk');",
  "let { vi } = require('vitest');\nvi = custom;\nvi.mock('@daytonaio/sdk');",
  "let g = require('@jest/globals');\ng = custom;\ng.jest.mock('@daytonaio/sdk');",
  "jest.mock('other', () => '@daytonaio/sdk');",
  "jest.mock('other', '@daytonaio/sdk');",
  "vi.unstable_mockModule('@daytonaio/sdk');",
  "vi.deepUnmock('@daytonaio/sdk');",
  "jest.doUnmock('@daytonaio/sdk');",
])('preserves unrelated calls and non-package arguments when given %s', input => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result.edits).toEqual([]);
  expect(result.items).toEqual([]);
});

it.each(['@daytona/sdk/v0', '@daytona/sdk/v2'])('preserves public namespaces when mocking %s', module => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { noLib: true } });
  const source = project.createSourceFile('/index.ts', `vi.importActual('${module}');`);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(result.edits).toEqual([]);
  expect(result.items).toEqual([]);
});

it('rewrites nested factory helpers and typeof import arguments when running the CLI', async () => {
  // Given
  const input = `jest.mock('@daytonaio/sdk', () => ({ ...jest.requireActual('@daytonaio/sdk'), Daytona: jest.fn() }));
vi.mock('@daytonaio/sdk', async () => ({ ...(await vi.importActual<typeof import('@daytonaio/sdk')>('@daytonaio/sdk')), extra: 1 }));
vi.doMock('@daytonaio/sdk');
jest.doMock('@daytonaio/sdk');
`;
  const root = temporaryProject({ 'index.ts': input });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['index.ts']).toBe(input.replaceAll('@daytonaio/sdk', '@daytona/sdk'));
  expect(JSON.parse(result.output).items.filter((entry: { kind: string }) => entry.kind === 'mock-target')).toHaveLength(6);
});

it('keeps module helper rewrites byte-stable on a second CLI run', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "vi.importActual<typeof import('@daytonaio/sdk')>('@daytonaio/sdk');\njest.doMock('@daytonaio/sdk');\n" });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).changedFiles).toEqual([]);
});
