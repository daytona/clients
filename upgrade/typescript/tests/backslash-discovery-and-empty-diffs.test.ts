/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { diff } from '../src/output.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { pnpm: { overrides: { '@daytona/sdk@*': '0.224.0' } } },
  { overrides: { '@daytona/sdk': '$@daytonaio/sdk' } },
])('reports unsupported constraint forms instead of silently leaving them: %j', async constraints => {
  // Given
  const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '0.224.0' }, ...constraints }) });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.skipIf(process.platform === 'win32')('continues analyzing other files when a POSIX filename contains a backslash', async () => {
  // Given
  const unsupported = "import { Daytona } from '@daytonaio/sdk'; new Daytona();";
  const root = temporaryProject({ 'literal\\index.ts': unsupported,
    'index.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona({ apiUrl: 'https://custom.example' });" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'unsupported-file', file: 'literal\\index.ts' }), expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]) });
  expect(contents(root)).toEqual(before);
});

it('uses a zero origin for an empty new file in a diff', () => {
  // Given
  const change = { file: 'empty.ts', before: 'old\n', after: '' };
  // When
  const patch = diff([change]);
  // Then
  expect(patch.split('\n')[2]).toBe('@@ -1,1 +0,0 @@');
});
