/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";
const setters = [
  "const p = process; p.env.DAYTONA_API_URL = VALUE;",
  "Reflect.set(process.env, 'DAYTONA_API_URL', VALUE);",
  "Object.defineProperty(process.env, 'DAYTONA_API_URL', { value: VALUE });",
  "let { env } = process; env.DAYTONA_API_URL = VALUE; env = {};",
  "let env = process.env; env.DAYTONA_API_URL = VALUE; env = {};",
  "const p = process; const { env } = p; env.DAYTONA_API_URL = VALUE;",
  "let p = process; p.env.DAYTONA_API_URL = VALUE; p = local;",
] as const;

it.each(setters)('refuses custom URL when a supported environment setter writes it: %s', async setter => {
  // Given
  const code = setter.replace('VALUE', "'https://custom/api'");
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
  expect(contents(root)).toEqual(before);
});

it.each([
  "Reflect.set(process.env, key, 'https://custom/api');",
  "Object.defineProperty(process.env, key, { value: 'https://custom/api' });",
  "Object.defineProperty(process.env, 'DAYTONA_API_URL', { get() { return 'https://custom/api'; } });",
])('reports review-only when a reflective write has an unresolved key or descriptor: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url' }),
  ]));
});

it.each(setters)('reports unresolved URL when a supported environment setter writes it: %s', async setter => {
  // Given
  const code = setter.replace('VALUE', 'endpoint');
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url' }),
  ]));
});

it.each(setters)('preserves environment writes when the caller asserts Cloud with the override: %s', async setter => {
  // Given
  const code = setter.replace('VALUE', "'https://custom/api'");
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url' }),
  ]));
  expect(contents(root)['index.ts']).toBe(code);
});

it.each([
  "var { env } = process; var { env } = { env: {} }; env.DAYTONA_API_URL = 'https://custom/api';",
  "var p = process; var p = { env: {} }; p.env.DAYTONA_API_URL = 'https://custom/api';",
])('refuses custom writes when multiple declarations retain possible environment provenance: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}', 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
});

it.each([
  "let { env } = process; env = {}; env.DAYTONA_API_URL = 'https://custom/api';",
  "const p = { env: {} }; p.env.DAYTONA_API_URL = 'https://custom/api';",
  "function apply(Reflect: Local) { Reflect.set(process.env, 'DAYTONA_API_URL', 'https://custom/api'); }",
  "function apply(Object: Local) { Object.defineProperty(process.env, 'DAYTONA_API_URL', { value: 'https://custom/api' }); }",
  "Reflect.set(process.env, 'OTHER', 'https://custom/api');",
  "Object.defineProperty(process.env, 'OTHER', { value: 'https://custom/api' });",
])('ignores unrelated writes when provenance does not prove an environment setting: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
});

it.each([
  'RUN echo "DAYTONA_API_URL=https://custom/api"',
  'ENV A="DAYTONA_API_URL=https://custom/api"',
  'RUN <<EOF\nENV DAYTONA_API_URL=https://custom/api\nEOF',
  'COPY <<EOF /example\nDAYTONA_API_URL=https://custom/api\nEOF',
])('ignores Docker mentions when they are not environment setters: %s', async docker => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}', Dockerfile: docker });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['Dockerfile']).toBe(docker);
});

it.each([
  'RUN echo "<<EOF"\nENV DAYTONA_API_URL=https://custom/api',
  '# RUN <<EOF\nENV DAYTONA_API_URL=https://custom/api',
  'RUN <<EOF\nexample\nEOF\nENV DAYTONA_API_URL=https://custom/api',
  'ENV OTHER=x DAYTONA_API_URL=https://custom/api',
  'ARG DAYTONA_API_URL=https://custom/api',
])('still refuses real Docker setters when quoted text or a completed heredoc precedes them: %s', async docker => {
  // Given
  const root = temporaryProject({ 'client.ts': client, Dockerfile: docker });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'Dockerfile' }),
  ]));
});

it('ignores later mapping mutation when Object.assign has already copied the mapping', async () => {
  // Given
  const code = "const urls = {}; Object.assign(process.env, urls); urls.DAYTONA_API_URL = 'https://custom/api';";
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
});

it('retains conservative mapping evidence when a later-declared function can execute before the copy', async () => {
  // Given
  const code = "const urls = {}; mutate(); Object.assign(process.env, urls); function mutate() { urls.DAYTONA_API_URL = 'https://custom/api'; }";
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
});

it.each([
  "const urls = {}; for (let i = 0; i < 2; i++) { Object.assign(process.env, urls); urls.DAYTONA_API_URL = 'https://custom/api'; }",
  "const urls = {}; function apply() { Object.assign(process.env, urls); urls.DAYTONA_API_URL = 'https://custom/api'; } apply(); apply();",
])('retains mapping evidence when repeated execution can observe a lexically later mutation: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
});
