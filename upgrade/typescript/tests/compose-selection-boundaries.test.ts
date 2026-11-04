/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };
const custom = 'https://self.example/api';

it.each(['*key:', '*key :'])('requires review when an alias is used as a service env_file key: %s', async key => {
  // Given an aliased key, not merely an alias in the env_file value.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'compose.yaml': `x: &key env_file\nservices:\n  app:\n    ${key} runtime.conf\n`,
    'runtime.conf': 'UNRELATED=value\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then unresolved loader keys cannot silently pass strict mode.
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});

it.each(['Dockerfile.conf', '.env.example', '.env.local'].flatMap(file => ['-f', 'COMPOSE_FILE'].map(selection => ({ file, selection }))))(
  'scans selected $file as Compose YAML when selected by $selection', async ({ file, selection }) => {
    // Given a manifest whose basename would otherwise select another scanner.
    const script = selection === '-f' ? `docker compose -f ${file} up` : `COMPOSE_FILE=${file} docker compose up`;
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }),
      [file]: `services:\n  app:\n    environment:\n      DAYTONA_API_URL: >-\n        ${custom}\n` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then basename rules cannot hide a custom URL in a YAML block scalar.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each(['-f', 'COMPOSE_FILE'].flatMap(selection => [true, false].map(readable => ({ selection, readable }))))(
  'handles external absolute selections when $selection points at a readable=$readable manifest', async ({ selection, readable }) => {
    // Given an absolute selection outside the project, plus a manifest-relative env_file.
    const external = temporaryProject({ 'selected': 'services: { app: { env_file: runtime.conf } }',
      'runtime.conf': `DAYTONA_API_URL=${custom}\n` });
    const path = join(external, readable ? 'selected' : 'missing');
    const script = selection === '-f' ? `docker compose -f ${path} up` : `COMPOSE_FILE=${path} docker compose up`;
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }) });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then external references scan or require review, never throw.
    expect(result.code).toBe(readable ? 2 : 1);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: readable ? 'custom-api-url' : 'env-api-url' }));
    expect(contents(root)).toEqual(before);
  },
);

it.each(['@', '::', '*', '?', '$SEP', '`separator`'])('honors literal separators or reviews dynamic ones when separator=%s', async separator => {
  // Given two extensionless manifests which only the literal separator can split completely.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: {
      start: `COMPOSE_FILE="config/first${separator}config/second" COMPOSE_PATH_SEPARATOR="${separator}" docker compose up`,
    } }),
    'config/first': 'services: {}', 'config/second': 'services: { app: { env_file: runtime.conf } }',
    'config/runtime.conf': `DAYTONA_API_URL=${custom}\n` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then custom URLs cannot hide behind literal splitting, and dynamic controls need review.
  const dynamic = separator === '$SEP' || separator === '`separator`';
  expect(result.code).toBe(dynamic ? 1 : 2);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: dynamic ? 'env-api-url' : 'custom-api-url' }));
});

it.each(['env_file: runtime.conf\nservices: {}',
  'x-unused: { env_file: runtime.conf }\nservices: {}',
  'x: &key image\nservices:\n  app:\n    *key : runtime.conf\n',
  'services: { app: { labels: { env_file: runtime.conf } } }'])(
  'ignores env_file metadata outside service loader fields: %s', async yaml => {
    // Given a non-loader key referring to a file with a custom URL.
    const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
      'compose.yaml': yaml, 'runtime.conf': `DAYTONA_API_URL=${custom}\n` });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then unrelated metadata causes neither refusal nor review.
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
  },
);
