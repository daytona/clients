/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const cases = [
  { annotation: "Sandbox['codeInterpreter']", imports: 'Sandbox', call: 'ci.listContexts()', target: 'sandbox.interpreter.listContexts()' },
  { annotation: 'Process', imports: 'Process', call: 'ci.listPtySessions()', target: 'sandbox.pty.list()' },
];
const declarations = 'export declare class CodeInterpreter { listContexts(): void; } export declare class Sandbox { codeInterpreter: CodeInterpreter; } export declare class Process { listPtySessions(): void; }';

it.each(cases)('requires the sandbox when an ownerless $annotation service moved', async ({ annotation, imports, call, target }) => {
  // Given
  const root = temporaryProject({
    'index.ts': `import type { ${imports} } from '@daytonaio/sdk'; function f(ci: ${annotation}) { ${call}; }`,
    'node_modules/@daytonaio/sdk/package.json': JSON.stringify({ name: '@daytonaio/sdk', version: '0.224.0', types: 'index.d.ts' }),
    'node_modules/@daytonaio/sdk/index.d.ts': declarations,
  });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme', message: expect.stringContaining(target) })]));
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'suggestion' })]));
});

it.each(cases)('writes a manual comment when an ownerless $annotation service moved', async ({ annotation, imports, call, target }) => {
  // Given an ownerless service with installed SDK declarations.
  const root = temporaryProject({
    'index.ts': `import type { ${imports} } from '@daytonaio/sdk'; function f(ci: ${annotation}) { ${call}; }`,
    'node_modules/@daytonaio/sdk/package.json': JSON.stringify({ name: '@daytonaio/sdk', version: '0.224.0', types: 'index.d.ts' }),
    'node_modules/@daytonaio/sdk/index.d.ts': declarations,
  });
  // When the CLI writes suggestion guidance.
  await invoke(root, ['--suggest-v2']);
  // Then the source carries the manual sandbox-based form, not only the report.
  expect(contents(root)['index.ts']).toContain(`// FIXME(daytona-upgrade): move this call to the owning sandbox: ${target}`);
});

it.each(cases)('keeps manual comments stable when $annotation suggestions rerun', async ({ annotation, imports, call }) => {
  // Given guidance already written by the CLI.
  const root = temporaryProject({
    'index.ts': `import type { ${imports} } from '@daytonaio/sdk'; function f(ci: ${annotation}) { ${call}; }`,
    'node_modules/@daytonaio/sdk/package.json': JSON.stringify({ name: '@daytonaio/sdk', version: '0.224.0', types: 'index.d.ts' }),
    'node_modules/@daytonaio/sdk/index.d.ts': declarations,
  });
  await invoke(root, ['--suggest-v2']);
  const before = contents(root);
  // When guidance runs again.
  await invoke(root, ['--suggest-v2']);
  // Then no duplicate manual comment is inserted.
  expect(contents(root)).toEqual(before);
});
