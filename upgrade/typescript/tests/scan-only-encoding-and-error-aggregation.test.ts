/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const source = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const scanOnly = [
  { file: 'public/js/example.js', text: '// Copyright ©\nconsole.log("example");\n' },
  { file: '.env', text: 'DB_PASSWORD=päss\n' },
  { file: 'compose.yaml', text: 'services:\n  app:\n    environment:\n      DB_PASSWORD: päss\n' },
  { file: 'other/package.json', text: '{"description":"©"}' },
] as const;

it.each(scanOnly)('migrates when unedited $file is Latin-1', async ({ file, text }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.js': source, [file]: '' });
  const bytes = Buffer.from(text, 'latin1');
  writeFileSync(join(root, file), bytes);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, file))).toEqual(bytes);
  expect(contents(root)['main.js']).toContain('().v0.create()');
  expect(JSON.parse(result.output).changedFiles).toEqual(['main.js', 'package.json']);
});

it('refuses a custom URL when the same .env contains a Latin-1 secret', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.js': source });
  const bytes = Buffer.from('DB_PASSWORD=päss\nDAYTONA_API_URL=https://private.example\n', 'latin1');
  writeFileSync(join(root, '.env'), bytes);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' },
    items: expect.arrayContaining([expect.objectContaining({ file: '.env', kind: 'custom-api-url', line: 2 })]) });
  expect(contents(root)).toEqual(before);
  expect(readFileSync(join(root, '.env'))).toEqual(bytes);
});

it.each([{ flags: [] }, { flags: ['--dry'] }, { flags: ['--print'] }])('reports every invalid edited file with $flags', async ({ flags }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.js': source, 'other.js': source });
  const files = ['main.js', 'other.js', 'package.json'];
  const originals = files.map(file => ({ file, bytes: Buffer.from(file === 'package.json'
    ? '{"description":"©","dependencies":{"@daytonaio/sdk":"0.224.0"}}'
    : source + '// ©\n', 'latin1') }));
  for (const { file, bytes } of originals) writeFileSync(join(root, file), bytes);
  // When
  const result = await invoke(root, ['--json', ...flags]);
  // Then
  expect(result.code).toBe(1);
  const report = JSON.parse(result.output);
  expect(report.changedFiles).toEqual([]);
  expect(report.items.filter((item: { readonly kind: string }) => item.kind === 'unsupported-file'))
    .toEqual(expect.arrayContaining(files.map(file => ({ file, line: 0, column: 0, kind: 'unsupported-file',
      message: `${file} is not valid UTF-8; convert it to UTF-8, then rerun.` }))));
  expect(report.items.filter((item: { readonly kind: string }) => item.kind === 'unsupported-file')).toHaveLength(3);
  for (const { file, bytes } of originals) expect(readFileSync(join(root, file))).toEqual(bytes);
});

it('reports two invalid edited sources without writing a valid manifest edit', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest });
  const bytes = Buffer.from(source + '// ©\n', 'latin1');
  for (const file of ['one.js', 'two.js']) writeFileSync(join(root, file), bytes);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: expect.arrayContaining([
    expect.objectContaining({ file: 'one.js', kind: 'unsupported-file' }),
    expect.objectContaining({ file: 'two.js', kind: 'unsupported-file' }),
  ]) });
  expect(contents(root)).toEqual(before);
  for (const file of ['one.js', 'two.js']) expect(readFileSync(join(root, file))).toEqual(bytes);
});
