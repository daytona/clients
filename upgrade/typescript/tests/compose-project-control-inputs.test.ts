/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };
const custom = 'DAYTONA_API_URL=https://self.example/api\n';

it.each(['base', 'overlay', '.', 'project space'])('refuses when env_file is custom at candidate base %s', async base => {
  // Given multiple manifests with competing clean and custom environment files.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'docker compose -f base/first.yml -f overlay/second.yml --project-directory="project space" up' } }),
    'base/first.yml': 'services: {}',
    'overlay/second.yml': 'services: { app: { env_file: runtime.conf } }',
    'base/runtime.conf': 'UNRELATED=base', 'overlay/runtime.conf': 'UNRELATED=overlay',
    'runtime.conf': 'UNRELATED=root', 'project space/runtime.conf': 'UNRELATED=project',
    [`${base}/runtime.conf`]: custom });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then every plausible project directory participates in atomic refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  { script: 'COMPOSE_FILE=config/first:config/second docker compose up' },
  { script: 'COMPOSE_FILE="config/first;config/second" docker compose up' },
  { script: 'COMPOSE_FILE="config/first@config/second" COMPOSE_PATH_SEPARATOR=@ docker compose up' },
  { script: 'export COMPOSE_PATH_SEPARATOR=@; export COMPOSE_FILE=config/first@config/second; docker compose up' },
  { script: 'export COMPOSE_FILE=config/first:config/second&&docker compose up' },
  { script: 'COMPOSE_PATH_SEPARATOR=$SEP COMPOSE_FILE=config/first:config/second docker compose up' },
  { script: 'docker compose up', env: 'COMPOSE_FILE=config/first:config/second\n' },
  { script: 'docker compose up', env: 'export COMPOSE_FILE=config/first@config/second\nCOMPOSE_PATH_SEPARATOR=@\n' },
  { script: 'COMPOSE_FILE=config/first@config/second docker compose up', env: 'COMPOSE_PATH_SEPARATOR=@\n' },
  { script: 'COMPOSE_FILE="config/first::config/second" COMPOSE_PATH_SEPARATOR="::" docker compose up' },
  { script: 'docker compose --env-file=selection.conf up', selection: 'COMPOSE_FILE=config/first:config/second\n' },
])('refuses custom URLs when Compose is selected by $script with $env', async ({ script, env, selection }) => {
  // Given extensionless manifests selected by shell or dotenv control values.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }),
    ...(env === undefined ? {} : { '.env': env }),
    ...(selection === undefined ? {} : { 'selection.conf': selection }),
    'config/first': 'services: {}',
    'config/second': 'services: { app: { env_file: runtime.conf } }',
    'config/runtime.conf': custom });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then selection does not depend on a YAML extension or default filename.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('refuses when the Compose project dotenv selects another extensionless manifest', async () => {
  // Given .env next to the first manifest, not next to package.json.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'docker compose -f infra/first up' } }),
    'infra/first': 'services: {}', 'infra/.env': 'COMPOSE_FILE=second\n',
    'infra/second': 'services: { app: { environment: { DAYTONA_API_URL: https://self.example/api } } }' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then project-local selection is included in atomic refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  'COMPOSE_FILE=$FILES docker compose up',
  'export COMPOSE_FILE=$(choose); docker compose up',
  'COMPOSE_FILE=missing docker compose up',
  'COMPOSE_FILE=config/first:missing docker compose up',
  'COMPOSE_PATH_SEPARATOR=$SEP docker compose up',
  'docker compose --project-directory=$PROJECT up',
  'docker compose --project-directory=missing up',
])('requires environment review when Compose inputs are unverifiable: %s', async script => {
  // Given an unresolved selection, separator, or project directory.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }) });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then no unverifiable selection silently passes.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});

it.each(['COMPOSE_FILE=$FILES', 'COMPOSE_PATH_SEPARATOR=${SEP}', 'export COMPOSE_FILE=`choose`'])(
  'requires environment review when project dotenv contains %s', async setting => {
    // Given a dynamic Compose control in a project-local dotenv file.
    const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest), '.env': setting });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then uncertainty has the same review contract as script controls.
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(1);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
  },
);

it('refuses a project-directory-relative manifest selected from a package script', async () => {
  // Given a literal project directory and a manifest outside filename discovery.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'docker compose --project-directory project -f selected up' } }),
    'project/selected': 'services: { app: { env_file: runtime.conf } }', 'project/runtime.conf': custom });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then manifest selection considers the explicit project directory too.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('scans literal separator candidates without discarding separator characters inside filenames', async () => {
  // Given a non-default separator and a colon that belongs to a filename.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: 'COMPOSE_PATH_SEPARATOR=@ COMPOSE_FILE="config/a:b@config/clean" docker compose up' } }),
    'config/a:b': 'services: { app: { env_file: runtime.conf } }', 'config/clean': 'services: {}',
    'config/runtime.conf': custom });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then conservative splitting preserves every existing interpretation.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('requires environment review when no env_file candidate exists', async () => {
  // Given a missing environment input in an otherwise valid manifest.
  const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
    'compose.yaml': 'services: { app: { env_file: missing.conf } }' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then a failed file resolution retains the existing review behavior.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});

it.each(['.env.example', '.env.sample', '.env.template', '.env.local', 'unselected/.env'])(
  'ignores Compose controls in unreferenced %s', async file => {
    // Given a control value outside Compose's project-variable inputs.
    const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
      [file]: 'COMPOSE_FILE=missing\n' });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then unused control values cannot change the upgrade outcome.
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
  },
);

it.each(['example', 'sample', 'template'])('honors Compose controls when --env-file selects .env.%s', async suffix => {
  // Given an example-named file explicitly selected for Compose project variables.
  const root = temporaryProject({ 'app.ts': client,
    'package.json': JSON.stringify({ ...manifest, scripts: { start: `docker compose --env-file .env.${suffix} up` } }),
    [`.env.${suffix}`]: 'COMPOSE_FILE=config/selected\n',
    'config/selected': 'services: { app: { environment: { DAYTONA_API_URL: https://self.example/api } } }' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then explicit Compose loading overrides only the discovery exemption.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['node --env-file=runtime.conf app.js', 'docker compose up & node --env-file=runtime.conf app.js',
  'docker compose -f config/selected up'])(
  'does not treat service or Node environment files as Compose control inputs: %s', async script => {
    // Given an environment input loaded by Node or a Compose service, not Compose itself.
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }),
      'config/selected': 'services: { app: { env_file: ../runtime.conf } }',
      'runtime.conf': 'COMPOSE_FILE=missing\n' });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then loading application variables does not activate Compose selections.
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
  },
);

it('reads project controls from a nested package script working directory', async () => {
  // Given a nested Compose invocation selecting its manifest through its own .env.
  const root = temporaryProject({ 'app.ts': client,
    'app/package.json': JSON.stringify({ ...manifest, scripts: { start: 'docker compose up' } }),
    'app/.env': 'COMPOSE_FILE=selected\n',
    'app/selected': 'services: { app: { env_file: runtime.conf } }', 'app/runtime.conf': custom });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the package working directory is a candidate Compose project directory.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
