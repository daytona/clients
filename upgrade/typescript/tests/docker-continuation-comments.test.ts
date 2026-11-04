/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";

it.each(['# note \\', '  # note \\'])('refuses an ENV after a backslashed full-line comment: %s', async comment => {
  // Given
  const root = temporaryProject({ 'client.ts': client, Dockerfile: `${comment}\nENV DAYTONA_API_URL https://custom/api\n` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'Dockerfile', line: 2 }),
  ]));
  expect(contents(root)).toEqual(before);
});

it.each([
  [' ', '# note'], [' ', '  # note \\'], [' ', '# first\n# second'],
  ['=', '# note'], ['=', '  # note \\'], ['=', '# first\n# second'],
])('refuses continued custom values interrupted by comments (%s, %s)', async (separator, comment) => {
  // Given
  const dockerfile = `ENV DAYTONA_API_URL${separator}\\\n${comment}\n https://custom/api\n`;
  const root = temporaryProject({ 'client.ts': client, Dockerfile: dockerfile });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'Dockerfile', line: 1 }),
  ]));
  expect(contents(root)).toEqual(before);
});

it('discards comments without injecting whitespace into a continued Cloud URL', async () => {
  // Given
  const dockerfile = 'ENV DAYTONA_API_URL https://app.daytona.\\\n# discarded\nio/api\n';
  const root = temporaryProject({ 'client.ts': client, Dockerfile: dockerfile });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'Dockerfile', line: 1 }),
  ]));
  expect(contents(root)['Dockerfile']).toBe(dockerfile);
});
