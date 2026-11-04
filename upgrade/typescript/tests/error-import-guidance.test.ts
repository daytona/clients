/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import type { Report } from '../src/model.js';
import { suggest } from '../src/suggestions.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();

it('reports each error import when suggesting through CLI JSON', async () => {
  // Given separate shared-error imports and an unrelated resource import.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'app.ts': `import { DaytonaError } from '@daytona/sdk/v0';
import { DaytonaNotFoundError } from '@daytona/sdk/v0';
import { Sandbox } from '@daytona/sdk/v0';
`,
  });
  const before = contents(root);

  // When suggestion mode emits only JSON.
  const result = await invoke(root, ['--suggest-v2', '--json']);

  // Then each affected import has a located suggestion, without changing files.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.errors).toBe(0);
  const items = report.items.filter(item => item.kind === 'suggestion');
  expect(items.map(item => ({ file: item.file, line: item.line }))).toEqual([
    { file: 'app.ts', line: 1 }, { file: 'app.ts', line: 2 },
  ]);
  expect(items.every(item => item.message.length > 0)).toBe(true);
  expect(contents(root)).toEqual(before);
});

it('uses the same guidance in comments and report items', () => {
  // Given a shared-error import.
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    "import { DaytonaError } from '@daytona/sdk/v0';");

  // When suggestions are generated.
  const result = suggest(source, mapping, '/');

  // Then machine-readable output preserves the guidance actually rendered to users.
  const comments = result.after.split('\n').filter(line => line.startsWith('// daytona-v2: '))
    .map(line => line.slice('// daytona-v2: '.length));
  expect(result.items.map(item => item.message)).toEqual(comments);
  expect(result.items).toHaveLength(1);
});

const aliases = [
  'const errs = sdk; void errs.DaytonaError;',
  'let errs = sdk; void errs.DaytonaError;',
  'const first = sdk; const errs = first; void errs["DaytonaError"];',
  'const { DaytonaError } = sdk; void DaytonaError;',
  'const errs = sdk; const { DaytonaError: E } = errs; void E;',
  'const { "DaytonaError": E } = sdk; void E;',
] as const;

it.each(aliases)('guides namespace errors when using stable aliases: %s', alias => {
  // Given a v0 namespace used through a stable local binding.
  const original = `import * as sdk from '@daytona/sdk/v0';\n${alias}`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', original);

  // When v2 guidance is requested.
  const result = suggest(source, mapping, '/');

  // Then the original import gets one removable suggestion.
  expect(result.items.map(item => ({ kind: item.kind, line: item.line })))
    .toEqual([{ kind: 'suggestion', line: 1 }]);
  expect(result.after.match(/\/\/ daytona-v2:/g)).toHaveLength(1);
});

it.each([
  'let errs = sdk; errs = other; void errs.DaytonaError;',
  'const errs = other; void errs.DaytonaError;',
  'const errs = sdk; void errs.Sandbox;',
  'function use(sdk: unknown) { const errs = sdk; void errs.DaytonaError; }',
])('leaves unrelated or reassigned namespaces without guidance: %s', usage => {
  // Given a v0 import without a stable shared-error use.
  const original = `import * as sdk from '@daytona/sdk/v0';\ndeclare const other: unknown;\n${usage}`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', original);

  // When v2 guidance is requested.
  const result = suggest(source, mapping, '/');

  // Then no error-import guidance is fabricated.
  expect(result.items).toEqual([]);
  expect(result.after).toBe(original);
});
