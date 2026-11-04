/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();";

it.each([
  "const { env } = process; env.DAYTONA_API_URL = 'https://custom/api';",
  "const { env: e } = process; e.DAYTONA_SERVER_URL = 'https://custom/api';",
  "const { env: e } = process; const alias = e; alias.DAYTONA_API_URL = 'https://custom/api';",
  "const { env } = process; Object.assign(env, { DAYTONA_API_URL: 'https://custom/api' });",
  "import proc from 'node:process'; const { env: e } = proc; e.DAYTONA_API_URL = 'https://custom/api';",
  "import * as proc from 'process'; const { env } = proc; env.DAYTONA_API_URL = 'https://custom/api';",
  "const { ['env']: e } = process; e.DAYTONA_API_URL = 'https://custom/api';",
])('refuses custom URL when process environment is destructured: %s', async code => {
  // Given
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
  "const { env } = process; env.DAYTONA_API_URL = endpoint;",
  "const { env: e } = process; e[key] = 'https://custom/api';",
  "const { env } = process; env.DAYTONA_API_URL = 'https://app.daytona.io/api';",
])('reports review when destructured environment URL is unresolved or Cloud: %s', async code => {
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

it.each([
  "function apply(process: Local) { const { env } = process; env.DAYTONA_API_URL = 'https://custom/api'; }",
  "const { other: env } = process; env.DAYTONA_API_URL = 'https://custom/api';",
  "const { ...env } = process; env.DAYTONA_API_URL = 'https://custom/api';",
  "let { env } = process; env = {}; env.DAYTONA_API_URL = 'https://custom/api';",
])('ignores lookalikes when destructuring does not prove an environment alias: %s', async code => {
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
