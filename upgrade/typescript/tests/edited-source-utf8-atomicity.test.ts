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
const invalidFiles = [
  { file: 'main.js', text: source + "const label='caf\u00e9';\n" },
  { file: 'package.json', text: '{"description":"caf\u00e9","dependencies":{"@daytonaio/sdk":"0.224.0"}}' },
] as const;

it.each(invalidFiles)('blocks all writes when $file contains invalid UTF-8', async ({ file, text }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.js': source });
  const bytes = Buffer.from(text, 'latin1');
  writeFileSync(join(root, file), bytes);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(readFileSync(join(root, file))).toEqual(bytes);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: expect.arrayContaining([
    expect.objectContaining({ file, kind: 'unsupported-file',
      message: `${file} is not valid UTF-8; convert it to UTF-8, then rerun.` }),
  ]) });
});

it('preserves a UTF-8 BOM and Unicode when migrating valid input', async () => {
  // Given
  const original = '\uFEFF' + source + "const label='caf\u00e9';\r\n";
  const root = temporaryProject({ 'package.json': '\uFEFF' + manifest, 'main.js': original });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['main.js']).toBe(original.replace('@daytonaio/sdk', '@daytona/sdk').replace('().create()', '().v0.create()'));
  expect(contents(root)['package.json']?.startsWith('\uFEFF')).toBe(true);
});
