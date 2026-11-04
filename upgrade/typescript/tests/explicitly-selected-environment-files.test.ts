/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };

it.each(['example', 'sample', 'template'].flatMap(suffix => ['dotenv', 'compose', 'node'].map(loader => ({ suffix, loader }))))(
  'refuses explicitly loaded .env.$suffix when using $loader', async ({ suffix, loader }) => {
    // Given an example-named file that is actually loaded at runtime.
    const file = `.env.${suffix}`;
    const root = temporaryProject({
      'app.ts': client + (loader === 'dotenv' ? `import dotenv from 'dotenv'; dotenv.config({ path: '${file}' });` : ''),
      'package.json': JSON.stringify({ ...manifest, scripts: { start: loader === 'node' ? `node --env-file=${file} app.js` : loader === 'compose' ? 'docker compose -f runtime-selection.yml up' : 'node app.js' } }),
      ...(loader === 'compose' ? { 'runtime-selection.yml': `services:\n  app:\n    env_file: ${file}\n` } : {}),
      [file]: 'DAYTONA_API_URL=https://self.example/api\n',
    });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then explicit loading overrides the name-based exemption atomically.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each(['infra/staging.yml', 'infra/staging.yaml'])('refuses custom env_file URLs when Compose is named %s', async file => {
  // Given a content-identified Compose manifest outside deployment directories.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    [file]: 'services: { app: { env_file: runtime.conf } }',
    'infra/runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then its relative environment input participates in refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('refuses inline custom URLs when a nonstandard YAML file contains Compose services', async () => {
  // Given inline environment settings outside the old basename/path rules.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'infra/staging.yml': 'services:\n  app:\n    environment:\n      DAYTONA_API_URL: https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then inline and file-loaded Compose settings follow the same classification.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['-f config/staging', '--file config/staging', '--file="config/staging"'])('scans a literal Compose file when scripts use %s', async flag => {
  // Given an explicitly selected manifest with no YAML extension.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: `docker compose ${flag} up` } }),
    'config/staging': 'services:\n  app:\n    env_file: runtime.conf\n',
    'config/runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then literal command-line selection overrides filename discovery.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['$COMPOSE_FILE', 'missing.yml'])('reviews an unprovable Compose selection when path is %s', async path => {
  // Given a dynamic or missing manifest selected by the app's start script.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: `docker compose -f ${path} up` } }) });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then absence of proof is not a silent pass.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});

it('refuses inline custom URLs when a script selects an extensionless Compose manifest', async () => {
  // Given explicit selection of a file not found by YAML name discovery.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'docker compose -f config/staging up' } }),
    'config/staging': 'services:\n  app:\n    environment:\n      DAYTONA_API_URL: https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then both Compose scan paths honor explicit manifest selection.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['example', 'sample', 'template'])('keeps the discovery exemption when .env.%s is not loaded', async suffix => {
  // Given a documentation-only example with no runtime reference.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    [`.env.${suffix}`]: 'DAYTONA_API_URL=https://self.example/api\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the name-based exemption remains valid for discovery alone.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it('leaves unrelated YAML env_file metadata outside deployment scanning', async () => {
  // Given non-Compose YAML with a nested services key, not top-level services.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'infra/metadata.yaml': 'metadata:\n  services:\n    app:\n      env_file: runtime.conf\n',
    'infra/runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then unrelated YAML does not become a deployment input.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
