/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}';
const code = "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n";

it.each([
  'ONBUILD ENV DAYTONA_API_URL=https://self/api',
  'ONBUILD ENV DAYTONA_API_URL https://self/api',
  'onbuild env DAYTONA_SERVER_URL=https://self/api',
])('refuses a custom endpoint when declared by %s', async instruction => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, Dockerfile: instruction + '\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind))
    .toEqual(['env-api-url', 'custom-api-url']);
  expect(contents(root)).toEqual(before);
});

it.each(['<< EOF', '<<-EOF', '<<- EOF', "<< 'EOF'", '<< "EOF"', "<<- 'EOF'"])(
  'ignores the body but resumes scanning when a Docker heredoc uses %s', async marker => {
    // Given
    const tabs = marker.startsWith('<<-') ? '\t' : '';
    const docker = `RUN ${marker}\n${tabs}ENV DAYTONA_API_URL=https://ignored/api\n${tabs}EOF\nENV DAYTONA_SERVER_URL=https://self/api\n`;
    const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toEqual([
      expect.objectContaining({ kind: 'env-api-url', line: 4 }),
      expect.objectContaining({ kind: 'custom-api-url', line: 4 }),
    ]);
  },
);

it.each([false, true])('reports an undeclared sibling package when both rewritten=%s', async bothRewritten => {
  // Given
  const root = temporaryProject({
    'package.json': '{"workspaces":["packages/*"]}',
    'packages/a/package.json': manifest,
    ...(bothRewritten ? { 'packages/a/index.ts': code } : {}),
    'packages/b/package.json': '{}',
    'packages/b/index.ts': code,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency'))
    .toEqual([expect.objectContaining({ file: 'packages/b/index.ts' })]);
  expect(contents(root)['packages/b/index.ts']).toContain('d.v0.create()');
});
