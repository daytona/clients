/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";
const customWrites = [
  "import { env } from 'node:process'; env.DAYTONA_API_URL = 'https://custom/api';",
  "import { env as settings } from 'process'; settings.DAYTONA_SERVER_URL = 'https://custom/api';",
  "import { env as settings } from 'node:process'; Object.assign(settings, { DAYTONA_API_URL: 'https://custom/api' });",
  "const key = 'DAYTONA_API_URL'; process.env[key] = 'https://custom/api';",
  "const key = `DAYTONA_SERVER_URL`; Object.assign(process.env, { [key]: 'https://custom/api' });",
  "const key = 'DAYTONA_API_URL'; const alias = key; process.env = { [alias]: 'https://custom/api' };",
  "const key = 'DAYTONA_API_URL'; const urls = {}; urls[key] = 'https://custom/api'; Object.assign(process.env, urls);",
  "const environment = process.env; environment.DAYTONA_API_URL = 'https://custom/api';",
  "const prefix = 'DAYTONA'; const key = `${prefix}_API_URL`; process.env[key] = 'https://custom/api';",
  "const key = 'DAYTONA_' + 'SERVER_URL'; Object.assign(process.env, { [key]: 'https://custom/api' });",
  "process.env.DAYTONA_API_URL = 'https://custom/api';",
] as const;

it.each(customWrites)('refuses proven custom source writes with both item kinds: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({
    refused: { reason: 'custom-api-url', message: expect.stringContaining('--drop-api-url') },
    items: expect.arrayContaining([
      expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
      expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
    ]),
  });
  expect(contents(root)).toEqual(before);
});

it.each(customWrites)('preserves proven custom source writes with the Cloud assertion: %s', async code => {
  // Given
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
  "process.env[key] = 'https://custom/api';",
  "Object.assign(process.env, { [key]: 'https://custom/api' });",
  "process.env = { [key]: 'https://custom/api' };",
  "import { env as settings } from 'node:process'; settings[key] = 'https://custom/api';",
  "const key = 'DAYTONA_API_URL'; process.env[key] = endpoint;",
  "let key = 'OTHER'; key = readKey(); process.env[key] = 'https://custom/api';",
  "Object.assign(process.env, loadEnvironment());",
  "import { urls } from './settings'; Object.assign(process.env, urls);",
  "function apply(urls: Record<string, string>) { process.env = urls; }",
  "const urls = { [key]: 'https://custom/api' }; Object.assign(process.env, urls);",
  "const urls = {}; urls[key] = 'https://custom/api'; Object.assign(process.env, urls);",
  "Object.assign(process.env, { get DAYTONA_API_URL() { return 'https://custom/api'; } });",
  "process.env = { [key]() { return 'https://custom/api'; } };",
  "[process.env[key]] = values;",
  "({ endpoint: process.env.DAYTONA_API_URL } = settings);",
  "for (process.env[key] of values) {}",
])('reports unresolved source writes without refusing: %s', async code => {
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
  expect(contents(root)['index.ts']).toBe(code);
});

it.each([
  "// process.env.DAYTONA_API_URL = 'https://custom/api';",
  "const example = \"process.env.DAYTONA_API_URL = 'https://custom/api'\";",
  "console.log(process.env.DAYTONA_API_URL, process.env[key]);",
  "import { env as settings } from 'node:process'; console.log(settings.DAYTONA_API_URL);",
  "import { env as settings } from './settings'; settings.DAYTONA_API_URL = 'https://custom/api';",
  "import type { env } from 'node:process'; env.DAYTONA_API_URL = 'https://custom/api';",
  "function apply(process: Local) { process.env[key] = 'https://custom/api'; }",
  "process.env.OTHER = 'https://custom/api';",
  "const key = 'OTHER'; process.env[key] = 'https://custom/api';",
  "const key = 'OTHER'; Object.assign(process.env, { [key]: 'https://custom/api' });",
  "const key = 'OTHER'; const urls = {}; urls[key] = 'https://custom/api'; Object.assign(process.env, urls);",
  "import { env as settings } from 'node:process'; settings.OTHER = 'https://custom/api';",
  "process.env[42] = 'https://custom/api';",
  "Object.assign(process.env, { get OTHER() { return 'https://custom/api'; } });",
])('does not report reads, comments, lookalikes or known unrelated keys: %s', async code => {
  // Given
  const root = temporaryProject({ 'client.ts': client, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file: 'index.ts' }),
  ]));
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' }),
  ]));
});

it.each([
  'ENV DAYTONA_API_URL=https://app.daytona.io/api OTHER=x',
  'ENV OTHER=x DAYTONA_API_URL="https://app.daytona.io/api" LAST=y',
  ['ENV DAYTONA_SERVER_URL=https://app.daytona.io/api \\', '  OTHER=x'].join('\n'),
  ['ENV DAYTONA_API_URL \\', '  https://app.daytona.io/api'].join('\n'),
  ['ENV DAYTONA_API_URL=\\', '  # discarded comment', '  https://app.daytona.io/api OTHER=x'].join('\n'),
])('recognizes Docker Cloud values independently of other pairs: %s', async value => {
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
  { file: '.env', value: 'DAYTONA_API_URL=https://custom/api' },
  { file: 'Dockerfile', value: 'ENV OTHER=x DAYTONA_API_URL=https://custom/api LAST=y' },
])('reports both item kinds for custom values in $file', async ({ file, value }) => {
  // Given
  const root = temporaryProject({ 'client.ts': client, [file]: value });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url', file }),
    expect.objectContaining({ kind: 'custom-api-url', file }),
  ]));
  expect(contents(root)).toEqual(before);
});
