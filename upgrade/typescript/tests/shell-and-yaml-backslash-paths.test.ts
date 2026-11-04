/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };
const custom = 'DAYTONA_API_URL=https://self.example/api\n';
const scripts = [
  String.raw`node --env-file=config\runtime.conf app.js`,
  String.raw`node --env-file="config\runtime.conf" app.js`,
  String.raw`node --env-file-if-exists='config\runtime.conf' app.js`,
  String.raw`node --env-file=config\._-9runtime.conf app.js`,
  String.raw`docker compose -f deploy\compose.yml up`,
  String.raw`COMPOSE_FILE=deploy\compose.yml docker compose up`,
  String.raw`docker compose --env-file=config\runtime.conf up`,
  String.raw`docker compose --project-directory=deploy\project -f selected up`,
] as const;

it.each(scripts.flatMap(script => [false, true].map(customUrl => ({ script, customUrl }))))(
  'scans Windows separators without review when $script (custom=$customUrl)', async ({ script, customUrl }) => {
    // Given existing files behind script paths and a YAML env_file using backslashes.
    const value = customUrl ? custom : 'UNRELATED=value\n';
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: script } }),
      'config/runtime.conf': value, 'config/._-9runtime.conf': value,
      ...(script.includes('compose.yml') ? { 'deploy/compose.yml': String.raw`services: { app: { env_file: config\runtime.conf } }` } : {}),
      'deploy/config/runtime.conf': value,
      'deploy/project/selected': 'services: { app: { env_file: runtime.conf } }',
      'deploy/project/runtime.conf': value });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then clean inputs pass strict mode; custom endpoints still refuse atomically.
    expect(result.code).toBe(customUrl ? 2 : 0);
    expect(JSON.parse(result.output).items).toEqual(customUrl ? expect.arrayContaining([
      expect.objectContaining({ kind: 'custom-api-url' }),
    ]) : []);
    expect(contents(root)).toEqual(before);
  },
);

it.runIf(process.platform !== 'win32').each([false, true])(
  'scans a literal POSIX Compose filename when the normalized file exists=%s', async normalized => {
    // Given a custom endpoint only in the literal-backslash manifest.
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: String.raw`docker compose -f deploy\selected up` } }),
      ...(normalized ? { 'deploy/selected': 'services: {}' } : {}),
      [String.raw`deploy\selected`]: 'services: { app: { env_file: runtime.conf } }',
      'runtime.conf': custom });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then normalization cannot discard an existing literal-path interpretation.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each([String.raw`config\ runtime.conf`, String.raw`config\$runtime.conf`,
  String.raw`config\"runtime.conf`, 'config\\runtime.conf\\', 'config\\\nruntime.conf']
  .filter(path => process.platform !== 'win32' || !path.includes('"'))
  .flatMap(path => ['node --env-file=', 'docker compose --file=', 'COMPOSE_FILE='].map(prefix => ({ path, prefix }))))(
  'requires review when $prefix uses an ambiguous escape: $path', async ({ path, prefix }) => {
    // Given a tempting normalized filename which cannot certify shell interpretation.
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: `${prefix}${path} app.js` } }),
      [path.replaceAll('\\', '/').replace(/\/$/, '')]: 'UNRELATED=value\n' });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--dry', '--strict', '--json']);
    // Then escaping shell metacharacters never becomes a certified path.
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
  },
);

it.each([String.raw`config\ runtime.conf`, String.raw`config\"runtime.conf`]
  .filter(path => process.platform !== 'win32' || !path.includes('"')))(
  'accepts backslashes before shell-special characters when the path comes from YAML: %s', async path => {
    // Given a single-quoted YAML path, where backslashes cannot escape shell characters.
    const root = temporaryProject({ 'app.ts': client, 'package.json': JSON.stringify(manifest),
      'compose.yaml': `services: { app: { env_file: '${path}' } }`,
      [path.replaceAll('\\', '/')]: custom });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then the normalized file is scanned rather than marked unverifiable.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.runIf(process.platform !== 'win32').each(['normalized', 'literal'] as const)(
  'scans both existing POSIX interpretations when the %s filename contains a custom URL', async selected => {
    // Given distinct files for separators and literal backslashes, with competing contents.
    const root = temporaryProject({ 'app.ts': client,
      'package.json': JSON.stringify({ ...manifest, scripts: { start: String.raw`docker compose -f deploy\selected up` } }),
      'deploy/selected': String.raw`services: { app: { env_file: config\runtime.conf } }`,
      [String.raw`deploy\selected`]: String.raw`services: { app: { env_file: config\runtime.conf } }`,
      'config/runtime.conf': selected === 'normalized' ? custom : 'UNRELATED=value\n',
      [String.raw`config\runtime.conf`]: selected === 'literal' ? custom : 'UNRELATED=value\n' });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then neither interpretation masks a custom endpoint in the other.
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
    expect(contents(root)).toEqual(before);
  },
);
