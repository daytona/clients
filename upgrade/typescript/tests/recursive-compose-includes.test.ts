/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = JSON.stringify({ dependencies: { '@daytonaio/sdk': '0.224.0' } });
const custom = 'DAYTONA_API_URL=https://self.example/api\n';

it.each(['[nested/first]', '[{ path: nested/first }]', '[{ path: [nested/clean, nested/first] }]'])(
  'recursively scans included manifests when include is %s', async include => {
    // Given extensionless nested manifests with a cycle and a service environment loader.
    const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
      'compose.yaml': `include: ${include}\nservices: {}`,
      'nested/clean': 'services: {}',
      'nested/first': 'include: [deeper/second]\nservices: {}',
      'nested/deeper/second': 'include: [../first]\nservices: { app: { env_file: runtime.conf } }',
      'nested/deeper/runtime.conf': custom });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then included service loaders cause atomic refusal despite the cycle.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each(['nested', 'project space', '.'].flatMap(base => [false, true].map(list => ({ base, list }))))(
  'scans include interpolation env files at $base when list=$list', async ({ base, list }) => {
    // Given a clean service environment and a custom include interpolation input.
    const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
      'compose.yaml': `include:\n  - path: [nested/selected, nested/clean]\n    env_file: ${list ? '[clean.conf, runtime.conf]' : 'runtime.conf'}\n${base === 'project space' ? '    project_directory: project space\n' : ''}`,
      'nested/selected': 'services: {}', 'nested/clean': 'services: {}',
      'nested/runtime.conf': 'UNRELATED=value', 'nested/clean.conf': 'UNRELATED=value',
      'clean.conf': 'UNRELATED=value', 'project space/clean.conf': 'UNRELATED=value',
      [`${base}/runtime.conf`]: custom });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then project-directory, include-directory and conservative candidates are scanned.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each([
  'include: [${MANIFEST}]',
  'include: [missing]',
  'include: [*paths]',
  'include: *paths',
  'include: [{ path: *paths }]',
  'include: [{ path: [selected, *paths] }]',
  'include: [{ path: selected, env_file: ${ENV_FILE} }]',
  'include: [{ path: selected, env_file: *env }]',
  'include: [{ path: selected, env_file: [clean.conf, *env] }]',
  'include: [{ path: selected, project_directory: ${PROJECT}, env_file: clean.conf }]',
  'include: [{ path: selected, project_directory: *project, env_file: clean.conf }]',
  'include: [{ path: selected, env_file: missing }]',
  'include: [{ <<: *settings, path: selected }]',
  'include: [{ *key : clean.conf, path: selected }]',
  '*includeKey : [selected]',
])('requires review when include inputs are unverifiable: %s', async include => {
  // Given dynamic, missing or alias-based loader inputs.
  const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
    'compose.yaml': `x-paths: &paths selected\nx-env: &env clean.conf\nx-project: &project .\nx-key: &key env_file\nx-include-key: &includeKey include\nx-settings: &settings { env_file: clean.conf }\n${include}\nservices: {}`,
    'selected': 'services: {}', 'clean.conf': 'UNRELATED=value' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then unresolved includes retain the existing environment review contract.
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});

it('scans externally included YAML independently of filename discovery', async () => {
  // Given an absolute include pointing outside the project with an inline custom URL.
  const external = temporaryProject({ selected: 'services: { app: { environment: { DAYTONA_API_URL: https://self.example/api } } }' });
  const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
    'compose.yaml': `include: [${JSON.stringify(join(external, 'selected'))}]` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the included manifest is scanned as Compose rather than dotenv or source.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('passes strict mode when literal includes form a clean cycle', async () => {
  // Given only clean manifests and interpolation env files.
  const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
    'compose.yaml': 'include: [{ path: nested/selected, env_file: clean.conf }]\nservices: {}',
    'nested/selected': 'include: [../compose.yaml]\nservices: {}',
    'nested/clean.conf': 'COMPOSE_FILE=not-a-project-selection\n' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then cycles terminate without treating include interpolation as CLI control input.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.skipIf(process.platform === 'win32')('terminates when include cycles traverse a directory symlink', async () => {
  // Given a cycle whose lexical path grows on every traversal.
  const root = temporaryProject({ 'app.ts': client, 'package.json': manifest,
    'compose.yaml': 'include: [nested/selected]',
    'nested/selected': 'include: [loop/selected]\nservices: {}' });
  symlinkSync(join(root, 'nested'), join(root, 'nested/loop'), 'dir');
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then canonical manifest identities bound the traversal without review noise.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
