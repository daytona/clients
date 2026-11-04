/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };

it.each(["import 'dotenv/config';", "require('dotenv/config');", ''])('reviews implicit dotenv path overrides for %s', async loader => {
  // Given a clean default file but an overridable preload.
  const root = temporaryProject({ 'app.ts': client + loader, '.env': 'UNRELATED=value\n',
    'package.json': JSON.stringify({ ...manifest, ...(loader ? {} : { scripts: { start: 'node -r dotenv/config app.js dotenv_config_path=config/custom.conf' } }) }) });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then clean defaults cannot certify the preloaded file.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items.filter(item => item.kind === 'env-api-url')).toHaveLength(1);
});

const composeValues = [
  'config/runtime.conf',
  "'config/runtime.conf'",
  '[config/clean.conf, config/runtime.conf]',
  '\n      - config/clean.conf\n      - config/runtime.conf',
  '\n      - path: config/runtime.conf\n        required: false',
  '\n      - required: true\n        path: config/runtime.conf',
  '[{ path: config/runtime.conf, required: false }]',
] as const;

it.each(composeValues)('refuses custom URLs in Compose env_file %s', async value => {
  // Given the same relative name at two bases, only Compose's base has a custom URL.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'deploy/compose.yaml': `services:\n  app:\n    env_file: ${value}\n`,
    'config/runtime.conf': 'UNRELATED=value\n', 'deploy/config/clean.conf': 'UNRELATED=value\n',
    'deploy/config/runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then refusal is atomic even for optional files that exist.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url', file: 'deploy/config/runtime.conf' }));
  expect(contents(root)).toEqual(before);
});

it('scans env_file inside flow-style Compose mappings', async () => {
  // Given a valid Compose flow mapping, not a line-oriented env_file key.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'compose.yaml': 'services: { app: { env_file: config/runtime.conf } }',
    'config/runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then YAML formatting cannot hide a literal custom endpoint.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('still refuses a custom root .env with a package-script dotenv preload', async () => {
  // Given an overridable preload and a known custom default environment.
  const root = temporaryProject({ 'app.ts': client, '.env': 'DAYTONA_API_URL=https://self.example/api\n',
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'node -r dotenv/config app.js' } }) });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the new review item does not replace the existing default-file refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['--env-file=config/runtime.conf', '--env-file-if-exists="config/runtime.conf"', '--env-file config/runtime.conf'])('refuses custom URLs in package scripts using %s', async option => {
  // Given a nested package whose env file is outside normal filename discovery.
  const root = temporaryProject({ 'app.ts': client,
    'app/package.json': JSON.stringify({ ...manifest, scripts: { start: `node ${option} app.js` } }),
    'app/config/runtime.conf': 'DAYTONA_SERVER_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the package-relative file participates in custom-URL refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  { 'compose.yaml': 'services:\n  app:\n    env_file: ${ENV_FILE}\n' },
  { 'compose.yaml': 'services:\n  app:\n    env_file:\n      - path: ${ENV_FILE}\n        required: false\n' },
  { 'package.json': JSON.stringify({ ...manifest, scripts: { start: 'node --env-file="$ENV_FILE" app.js' } }) },
])('reviews unresolved environment file references: %j', async files => {
  // Given an environment-selected path that cannot be resolved statically.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest), ...files });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then unknown values need review, not a fabricated custom-URL refusal.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.refused).toBeUndefined();
  expect(report.items.filter(item => item.kind === 'env-api-url')).toHaveLength(1);
});
