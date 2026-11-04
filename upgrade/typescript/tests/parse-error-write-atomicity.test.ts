/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([{ flags: [] }, { flags: ['--suggest-v2'] }, { flags: ['--remove-suggestions'] }])('blocks every write when source parse errors exist in mode $flags', async ({ flags }) => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}\n',
    'a.ts': '\uFEFFimport { Daytona } from "@daytonaio/sdk"; const first = ;\n',
    'b.tsx': 'import { Daytona } from "@daytonaio/sdk"; const second = <div>;\n',
    'valid.ts': "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n",
  });
  const before = contents(root);
  // When
  const result = await invoke(root, [...flags, '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: expect.arrayContaining([
    expect.objectContaining({ file: 'a.ts', line: 1, kind: 'unsupported-file' }),
    expect.objectContaining({ file: 'b.tsx', line: 1, kind: 'unsupported-file' }),
  ]) });
});
