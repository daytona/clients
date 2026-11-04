/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { file: '.env.local', text: 'DAYTONA_API_URL=https://self.host/api\n' },
  { file: 'Dockerfile', text: 'ENV DAYTONA_API_URL https://self.host/api\n' },
  { file: 'compose.yaml', text: 'environment:\n  - DAYTONA_SERVER_URL="https://self.host/api"\n' },
  { file: 'k8s/pod.yaml', text: 'env:\n  - name: DAYTONA_API_URL\n    value: "https://self.host/api"\n' },
  { file: '.github/workflows/test.yml', text: "env:\n  DAYTONA_API_URL: 'https://self.host/api'\n" },
  { file: 'index.ts', text: "process.env.DAYTONA_API_URL = 'https://self.host/api';\n" },
  { file: 'index.ts', text: "process.env['DAYTONA_SERVER_URL'] = `https://self.host/api`;\n" },
])('refuses a known custom environment URL in $file without writing', async ({ file, text }) => {
  // Given
  const root = temporaryProject({ 'client.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();", [file]: text });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' }, items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url', file })]) });
  expect(contents(root)).toEqual(before);
});

it.each(['https://app.daytona.io/api', 'https://app.daytona.io/api/', '${API_URL}'])('only reports environment setting %s', async url => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();", '.env': `DAYTONA_API_URL=${url}\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'env-api-url' })]) });
});

it('only reports a custom environment URL when explicitly overridden', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk'; process.env.DAYTONA_API_URL = 'https://custom'; new Daytona().create();", '.env': 'DAYTONA_API_URL=https://custom\n' });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.not.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]) });
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
    expect.objectContaining({ kind: 'env-api-url', file: '.env' }),
  ]) });
});
