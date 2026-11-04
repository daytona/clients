/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const endpoints = ['https://app.daytona.io/api', 'https://self.example/api'] as const;
const formats = [
  { file: '.env', declaration: 'DAYTONA_API_URL=VALUE' },
  { file: 'Dockerfile', declaration: 'ENV DAYTONA_API_URL=VALUE' },
  { file: 'Dockerfile', declaration: 'ENV DAYTONA_API_URL VALUE' },
  { file: 'compose.yaml', declaration: 'services: {app: {environment: {DAYTONA_API_URL: "VALUE"}}}' },
  { file: 'compose.yaml', declaration: 'services:\n  app:\n    environment: ["DAYTONA_API_URL=VALUE"]' },
] as const;

it.each(formats.flatMap(format => [':-', '-', ':+', '+'].flatMap(operator =>
  endpoints.map(endpoint => ({ ...format, operator, endpoint })))))(
  'classifies $operator interpolation defaults in $file when endpoint=$endpoint', async ({ file, declaration, operator, endpoint }) => {
    // Given a declaration whose default is known independently of the ambient environment.
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      [file]: declaration.replace('VALUE', '${DAYTONA_API_URL' + operator + endpoint + '}') });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then only custom defaults refuse, and references do not add duplicate items.
    const custom = endpoint === 'https://self.example/api';
    expect(result.code).toBe(custom ? 2 : 0);
    expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind))
      .toEqual(custom ? ['env-api-url', 'custom-api-url'] : ['env-api-url']);
    if (custom) expect(contents(root)).toEqual(before);
  },
);

it.each(formats.flatMap(format => ['${DAYTONA_API_URL}', '${DAYTONA_API_URL:?}', '${DAYTONA_API_URL:?https://self.example/api}', '${DAYTONA_API_URL?https://app.daytona.io/api}', '${OTHER:-${ENDPOINT}}'].map(value => ({ ...format, value }))))(
  'reviews unresolved $value in $file without refusal', async ({ file, declaration, value }) => {
    // Given a declaration without a statically known endpoint.
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      [file]: declaration.replace('VALUE', value) });
    // When
    const result = await invoke(root, ['--strict', '--dry', '--json']);
    // Then unresolved values require review, not a custom-URL refusal.
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind)).toEqual(['env-api-url']);
  },
);

it.each([
  { file: '.env', text: 'API=${DAYTONA_API_URL:-https://self.example/api}' },
  { file: 'Dockerfile', text: 'ENV API=${DAYTONA_API_URL:-https://self.example/api}' },
  { file: 'compose.yaml', text: 'services:\n  app:\n    environment:\n      - API=${DAYTONA_API_URL:-https://self.example/api}' },
])('ignores references inside another variable in $file', async ({ file, text }) => {
  // Given an unrelated variable referencing a Daytona variable, not declaring it.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', [file]: text });
  // When
  const result = await invoke(root, ['--strict', '--dry', '--json']);
  // Then no endpoint was declared.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
