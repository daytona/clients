/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const client = "import { Daytona } from '@daytonaio/sdk';\nconst daytona = new Daytona();\ndaytona.create();\n";
const asset = `const logo = '${'A'.repeat(11000)}';\n` + '// source row\n'.repeat(15);
const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each(['asset.ts'])('migrates Daytona code when %s has long lines', async file => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, [file]: client + asset });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, file), 'utf8')).toContain('daytona.v0.create()');
});

it.each(['apiUrl', 'serverUrl', 'environment'])('refuses a custom %s when the source looks generated', async setting => {
  // Given
  const expression = setting === 'environment' ? "process.env.DAYTONA_API_URL = 'https://internal.example/api';" :
    `new Daytona({ ${setting}: 'https://internal.example/api' });`;
  const input = client + asset + expression;
  const root = temporaryProject({ 'package.json': manifest, 'app.min.js': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(readFileSync(join(root, 'app.min.js'), 'utf8')).toBe(input);
  expect(readFileSync(join(root, 'package.json'), 'utf8')).toBe(manifest);
});

it.each(['apiUrl', 'serverUrl'])('scans %s in a long shared config without a Daytona mention', async field => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'config.ts': asset + `export const config = { ${field}: 'https://internal.example/api' };`,
    'app.ts': "import { Daytona } from '@daytonaio/sdk'; import { config } from './config'; new Daytona(config);" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
});

it('refuses a JavaScript environment write when SDK types are absent', async () => {
  // Given
  const root = temporaryProject({ 'app.js': client + "process.env.DAYTONA_SERVER_URL = 'https://internal.example/api';" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
});

it('scans environment writes when a speed-skipped file builds its setting name', async () => {
  // Given
  const input = asset + "/* Daytona environment */ const prefix = 'DAY' + 'TONA_'; process.env[prefix + 'API_URL'] = 'https://internal.example/api';";
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': client, 'env.min.js': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(readFileSync(join(root, 'env.min.js'), 'utf8')).toBe(input);
});

it.each(['long.js'])('reports a speed-skipped source when %s is unrelated', async file => {
  // Given
  const root = temporaryProject({ 'app.ts': client, [file]: asset });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items).toContainEqual(expect.objectContaining({ file, kind: 'unsupported-file' }));
  expect(readFileSync(join(root, file), 'utf8')).toBe(asset);
});

it.each([
  { file: 'worker.js', input: "// @flow\nconst { daytona } = require('./client');\nconst value: number = 1; daytona.create();" },
  { file: 'worker.ts', input: "import { client } from './client';\nfunction run() { client.create();" },
  { file: 'worker.ts', input: "import './unrelated';\nconst value = ;" },
  { file: 'worker.ts', input: 'const DAYTONA_VALUE = ;' },
  { file: 'worker.ts', input: "import client from /* note */ './client'; const x = ;" },
  { file: 'worker.ts', input: "import client from // note\n './client'; const x = ;" },
  { file: 'worker.ts', input: "export { client } from /* note */ './client'; const x = ;" },
  { file: 'worker.ts', input: "const client = require(/* note */ './client'); const x = ;" },
  { file: 'worker.ts', input: "import(/* note */ './client'); const x = ;" },
  { file: 'worker.ts', input: "import /* note */ './client'; const x = ;" },
  { file: 'worker.ts', input: "import client from /* note */ './client/*note*/'; const x = ;" },
])('blocks unparsable $file when it may use a shared client', async ({ file, input }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'client.ts': client, [file]: input });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file, kind: 'unsupported-file' }));
});

it('reports unrelated parse errors when they do not block migration', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': client, 'unrelated.ts': 'const value = ;' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'unrelated.ts', kind: 'unsupported-file' }));
});

it.each(['app.js', 'app.ts'])('migrates valid %s when it carries a Flow pragma', async file => {
  // Given
  const root = temporaryProject({ [file]: '// @flow\n' + client });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, file), 'utf8')).toContain('daytona.v0.create()');
});

it('reports unresolved SDK types once when several files require fallback', async () => {
  // Given
  const root = temporaryProject({ 'first.ts': client, 'second.ts': client, 'unrelated.ts': 'const value = 1;' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  const items = report.items.filter(item => item.kind === 'types-unresolved');
  expect(items).toHaveLength(1);
  expect(items[0]?.message).toContain('2 files');
});

it('blocks ignored unparsable Daytona source when Git supplies ignore rules', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': 'lib/\nignored.ts\n', 'app.ts': client,
    'lib/output.js': client, 'ignored.ts': client + 'const broken = ;' });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(1);
  expect(readFileSync(join(root, 'app.ts'))).toEqual(Buffer.from(client));
  expect(readFileSync(join(root, 'package.json'))).toEqual(Buffer.from(manifest));
  expect(readFileSync(join(root, 'lib/output.js'), 'utf8')).toBe(client);
  expect(readFileSync(join(root, 'ignored.ts'), 'utf8')).toBe(client + 'const broken = ;');
});
