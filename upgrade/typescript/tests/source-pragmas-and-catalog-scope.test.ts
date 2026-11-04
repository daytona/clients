/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const source = "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n";
it.each(['dist/app.js', 'build/app.js', 'out/app.js', '.next/app.js', 'coverage/app.js'])('ignores build output %s without reporting it', async file => {
  // Given
  const input = source + (file.endsWith('bundle.js') ? ' '.repeat(10001) :
    file.endsWith('mapped.js') ? '//# sourceMappingURL=data:application/json;base64,' + 'a'.repeat(10001) : '');
  const root = temporaryProject({ 'app.ts': source, [file]: input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(readFileSync(join(root, file), 'utf8')).toBe(input);
  expect(JSON.parse(result.output).items.some((item: { file: string }) => item.file === file)).toBe(false);
});

it.each(['// @flow\nconst x: number = 1;', 'const x = ;'])('reports unrelated unsupported source: %s', async input => {
  // Given
  const root = temporaryProject({ 'app.ts': source, 'fixture.js': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['fixture.js']).toBe(input);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'fixture.js', kind: 'unsupported-file' }));
});

it.each(['// @flow\nconst x: number = 1;', 'const x = ;'])('blocks unsupported source importing Daytona: %s', async input => {
  // Given
  const root = temporaryProject({ 'app.ts': source, 'fixture.js': input + source });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ file: 'fixture.js', kind: 'unsupported-file' })]));
});

it('names the source whose SDK type import failed', async () => {
  // Given
  const root = temporaryProject({ 'a.ts': 'const unrelated = 1;', 'z.ts': source });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { kind: string }) => item.kind === 'types-unresolved'))
    .toEqual([expect.objectContaining({ file: 'z.ts' })]);
});

it.each(['`text${1}// @flow`', String.raw`/\/\/@flow/`, '"// @flow"'])('keeps comment-looking literals supported: %s', async literal => {
  // Given
  const root = temporaryProject({ 'app.ts': source + `const text = ${literal};` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.some((item: { kind: string }) => item.kind === 'unsupported-file')).toBe(false);
});

it.each(['// @flow docs', '// see @flow docs'])('does not treat a trailing comment as a file pragma: %s', async comment => {
  // Given a Daytona source file with an ordinary comment after code.
  const input = `${source}const value = 1;\n${comment}\n`;
  const root = temporaryProject({ 'app.ts': input });
  // When the CLI analyzes the source.
  const result = await invoke(root, ['--json']);
  // Then the ordinary comment does not block migration.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.some((item: { kind: string }) => item.kind === 'unsupported-file')).toBe(false);
});

it.each(['catalog:', 'catalog:example'])('reports %s dependency at its workspace catalog', async version => {
  // Given
  const root = temporaryProject({ 'packages/app/package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': version } }),
    'pnpm-workspace.yaml': "packages:\n  - packages/*\ncatalog:\n  '@daytonaio/sdk': '0.224.0'\ncatalogs:\n  example:\n    '@daytonaio/sdk': '0.224.0'\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { kind: string }) => item.kind === 'manual-dependency'))
    .toEqual([expect.objectContaining({ file: 'pnpm-workspace.yaml' })]);
});

it('removes a config URL property without leaving an empty whitespace line', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytonaio/sdk';
new Daytona({
  apiUrl: 'https://app.daytona.io/api',
  apiKey: 'key',
});\n` });
  // When
  await invoke(root, ['--drop-api-url']);
  // Then
  expect(contents(root)['app.ts']).toContain("new Daytona({\n  apiKey: 'key',\n});");
});

it('inserts inline suggestions without trailing whitespace', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();" });
  // When
  const result = await invoke(root, ['--suggest-v2']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain('// daytona-v2:');
  expect(contents(root)['app.ts']).not.toMatch(/[\t ]+(?:\r?\n|$)/);
});
