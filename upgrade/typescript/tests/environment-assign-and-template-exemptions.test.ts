/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";
const environmentRefusal = 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x. If it points at Daytona Cloud, rerun with --drop-api-url. No files were written.';

it.each([
  "Object.assign(process.env, { DAYTONA_API_URL: 'https://custom/api' });",
  "Object.assign(process.env, { ...{ DAYTONA_SERVER_URL: 'https://custom/api' } });",
  "process.env = { DAYTONA_API_URL: 'https://custom/api' };",
  "process.env = { ...process.env, ...{ DAYTONA_SERVER_URL: 'https://custom/api' } };",
  "import system from 'node:process'; Object.assign(system.env, { DAYTONA_SERVER_URL: 'https://custom/api' });",
  "const urls = { DAYTONA_API_URL: 'https://custom/api' }; process.env = { ...urls };",
  "Object.assign(process.env, { ['DAYTONA_SERVER_URL']: 'https://custom/api' });",
])('refuses bulk environment writes before changing files: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]));
  expect(JSON.parse(result.output).refused.message).toBe(environmentRefusal);
  expect(contents(root)).toEqual(before);
});

it.each([
  "Object.assign(process.env, { ...{ DAYTONA_API_URL: 'https://custom/api' } });",
  "process.env = { ...process.env, DAYTONA_SERVER_URL: 'https://custom/api' };",
])('preserves bulk environment settings when the user asserts Cloud: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' })]));
  expect(contents(root)['index.ts']).toBe(code);
});

it.each(['.env.example', '.env.sample', '.env.template'])('ignores template environment files entirely: %s', async file => {
  // Given
  const root = temporaryProject({ 'client.ts': client, [file]: 'DAYTONA_API_URL=https://custom/api\nDAYTONA_SERVER_URL=${URL}\n' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ file })]));
});

it.each(['.env', '.env.test', 'Dockerfile'])('keeps active environment URL refusal in %s', async file => {
  // Given
  const root = temporaryProject({ 'client.ts': client, [file]: file === 'Dockerfile' ? 'ENV DAYTONA_API_URL https://custom/api' : 'DAYTONA_API_URL=https://custom/api' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).refused.message).toBe(environmentRefusal);
});

it.each([
  "function f(process: Local) { Object.assign(process.env, { DAYTONA_API_URL: 'https://custom/api' }); }",
  "function f(Object: Local) { Object.assign(process.env, { DAYTONA_API_URL: 'https://custom/api' }); }",
  "process.env.DAYTONA_API_URL = '';",
  "const DAYTONA_API_URL = 'OTHER'; process.env = { [DAYTONA_API_URL]: 'https://custom/api' };",
])('ignores local environment lookalikes and empty source values: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ file: 'index.ts', kind: 'custom-api-url' })]));
});

it.each(['HTTPS://APP.DAYTONA.IO/api', 'https://APP.DAYTONA.IO/api/'])('recognizes an uppercase config Cloud host: %s', async url => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytonaio/sdk'; new Daytona({ apiUrl: '${url}' }).create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('apiUrl');
});

it.each(['# DAYTONA_API_URL=https://custom/api', '  # DAYTONA_SERVER_URL=https://custom/api', 'DAYTONA_API_URL=""', "DAYTONA_SERVER_URL=''", 'DAYTONA_API_URL='])('ignores comments and empty environment values: %s', async line => {
  // Given
  const root = temporaryProject({ 'client.ts': client, '.env': line });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ file: '.env' })]));
});

it.each([
  { file: '.env', value: 'DAYTONA_API_URL=HTTPS://APP.DAYTONA.IO/api/' },
  { file: 'index.ts', value: "process.env.DAYTONA_SERVER_URL = 'https://APP.DAYTONA.IO/api';" },
])('recognizes case-insensitive Cloud hosts in $file', async ({ file, value }) => {
  // Given
  const root = temporaryProject({ 'client.ts': client, [file]: value });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'env-api-url', file })]));
});

it('ignores a Kubernetes environment value when only a comment follows', async () => {
  // Given an explicitly empty deployment value with a comment.
  const root = temporaryProject({ 'client.ts': client, 'k8s/app.yaml': 'env:\n  - name: DAYTONA_API_URL\n    value: # unset\n' });
  // When the CLI scans active deployment files.
  const result = await invoke(root, ['--json']);
  // Then an empty value neither refuses nor asks for review.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ file: 'k8s/app.yaml' })]));
});
