/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";

it.each([
  'ENV DAYTONA_API_URL \\\n  HTTPS://APP.DAYTONA.IO/api/',
  'ENV DAYTONA_SERVER_URL=\\\n  https://app.daytona.io/api',
  'ENV \\\n  DAYTONA_API_URL=\\\n  https://app.daytona.io/api',
])('recognizes continued Docker Cloud values: %s', async value => {
  // Given
  const root = temporaryProject({ 'client.ts': client, Dockerfile: value });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'Dockerfile', line: 1 }),
  ]));
  expect(contents(root)['Dockerfile']).toBe(value);
});

it.each([
  "const urls = {}; urls.DAYTONA_API_URL = 'https://custom/api'; Object.assign(process.env, urls);",
  "const urls = {}; const alias = urls; alias.DAYTONA_SERVER_URL = 'https://custom/api'; process.env = { ...urls };",
  "const urls = {}; urls[`DAYTONA_API_URL`] = 'https://custom/api'; Object.assign(process.env, urls);",
  "process.env = { [`DAYTONA_API_URL`]: 'https://custom/api' };",
  "Object.assign(process.env, { [`DAYTONA_SERVER_URL`]: 'https://custom/api' });",
  "process.env[`DAYTONA_API_URL`] = 'https://custom/api';",
])('refuses custom environment values through static keys or aliases: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
  expect(contents(root)).toEqual(before);
});

it.each([
  "const urls = {}; Object.assign(urls, { DAYTONA_API_URL: 'https://custom/api' }); Object.assign(process.env, urls);",
  "let urls = {}; urls = loadUrls(); Object.assign(process.env, urls);",
  "const urls = {}; urls[key] = endpoint; Object.assign(process.env, urls);",
  "const urls = {}; urls[key] = 'https://custom/api'; Object.assign(process.env, urls);",
])('reports unresolved object mutations for strict manual review: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(contents(root)['index.ts']).toBe(code);
});

it('keeps alias environment writes when the user asserts Cloud', async () => {
  // Given
  const code = "const urls = {}; urls.DAYTONA_API_URL = 'https://custom/api'; Object.assign(process.env, urls);";
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(contents(root)['index.ts']).toBe(code);
});

it('refuses a custom Docker continuation at its original line', async () => {
  // Given
  const dockerfile = ['FROM node', 'ENV DAYTONA_API_URL \\', '  https://custom/api',
    'ENV DAYTONA_SERVER_URL \\', '  https://app.daytona.io/api'].join('\r\n');
  const root = temporaryProject({ 'client.ts': client, Dockerfile: dockerfile });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'Dockerfile', line: 2 }),
    expect.objectContaining({ kind: 'env-api-url', file: 'Dockerfile', line: 4 }),
  ]));
  expect(contents(root)).toEqual(before);
});

it.each(['config', 'environment'] as const)('refuses multiple trailing Cloud slashes in %s', async surface => {
  // Given
  const root = temporaryProject(surface === 'config'
    ? { 'index.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona({ apiUrl: 'HTTPS://APP.DAYTONA.IO/api//' }).create();" }
    : { 'client.ts': client, '.env': 'DAYTONA_API_URL=HTTPS://APP.DAYTONA.IO/api//' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
