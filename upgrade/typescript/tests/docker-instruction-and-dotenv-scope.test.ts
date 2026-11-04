/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}';

it.each([
  'RUN echo "DAYTONA_API_URL=https://self/api"\n',
  'RUN <<EOF\nDAYTONA_API_URL=https://self/api\nEOF\n',
  "COPY <<'EOF' /example\nENV DAYTONA_API_URL=https://self/api\nEOF\n",
  'ENV A="DAYTONA_API_URL=https://self/api"\n',
  'ARG A="DAYTONA_SERVER_URL=https://self/api"\n',
  'LABEL example="DAYTONA_API_URL=https://self/api"\n',
])('ignores Docker text when it does not declare an endpoint: %s', async docker => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['Dockerfile']).toBe(docker);
});

it.each([
  'RUN <<EOF\nENV DAYTONA_API_URL=https://ignored/api\nEOF\nENV DAYTONA_API_URL=https://self/api\n',
  'ENV A="DAYTONA_API_URL=https://ignored/api" DAYTONA_API_URL=https://self/api\n',
  'ARG DAYTONA_API_URL="https://self/api"\n',
  'ENV "DAYTONA_API_URL=https://self/api"\n',
  'RUN <<END-OF-TEXT\nENV DAYTONA_API_URL=https://ignored/api\nEND-OF-TEXT\nENV DAYTONA_API_URL=https://self/api\n',
  'RUN echo "<<EOF"\nENV DAYTONA_API_URL=https://self/api\n',
  'RUN cat <<<EOF\nENV DAYTONA_API_URL=https://self/api\n',
  'RUN <<-EOF\n\tDAYTONA_API_URL=https://ignored/api\n\tEOF\nENV DAYTONA_API_URL=https://self/api\n',
])('reports only the real Docker setter when irrelevant text surrounds it: %s', async docker => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind))
    .toEqual(['env-api-url', 'custom-api-url']);
  expect(contents(root)).toEqual(before);
});

it.each([
  'DAYTONA_API_URL=https://self/api\n',
  'ENV DAYTONA_API_URL="https://self/api\n',
  'ARG DAYTONA_API_URL="https://self/api\n',
  'ARG DAYTONA_API_URL\n',
  'ENV DAYTONA_API_URL=https://self/api\\\n',
])('keeps malformed or nonliteral Docker declarations review-only: %s', async docker => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'Dockerfile.dev': docker });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual([
    expect.objectContaining({ kind: 'env-api-url', file: 'Dockerfile.dev' }),
  ]);
  expect(contents(root)['Dockerfile.dev']).toBe(docker);
});
