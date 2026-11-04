/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { analysisFiles } from '../src/discovery.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const client = "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n";

it.each([
  ['.env', 'DAYTONA_API_URL=https://internal.example/api\n'],
  ['compose.yaml', 'services:\n  app:\n    environment:\n      DAYTONA_API_URL: https://internal.example/api\n'],
  ['Dockerfile', 'ENV DAYTONA_API_URL=https://internal.example/api\n'],
  ['deploy/app.yaml', 'env:\n  - name: DAYTONA_API_URL\n    value: https://internal.example/api\n'],
  ['generated/client.ts', client + "new Daytona({ apiUrl: 'https://internal.example/api' });"],
  ['example.js', client + "process.env.DAYTONA_API_URL = 'https://internal.example/api';"],
])('refuses without writes when Git ignores custom URL in %s', async (file, input) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': client, '.gitignore': file + '\n', [file]: input });
  execFileSync('git', ['init', '-q', root]);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it('reports the ignored Daytona count and first five paths when they are not rewritten', async () => {
  // Given
  const ignored = Object.fromEntries(Array.from({ length: 7 }, (_, index) => [`generated/${index}.ts`, client]));
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': client, '.gitignore': 'generated/\n', ...ignored });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  for (const file of Object.keys(ignored)) expect(readFileSync(join(root, file), 'utf8')).toBe(client);
  const notices = report.items.filter(item => item.message.includes('Git-ignored'));
  expect(notices).toHaveLength(1);
  expect(notices[0]?.message).toContain('7');
  expect(notices[0]?.message).toContain('generated/4.ts');
  expect(notices[0]?.file).toBe('generated/0.ts');
  expect(notices[0]?.kind).toBe('fixme');
  for (const file of Object.keys(ignored).slice(0, 5)) expect(notices[0]?.message).toContain(file);
  expect(notices[0]?.message).toContain('and 2 more');
});

it.each([
  ['app.min.js', client],
  ['large.min.ts', client + `const payload = '${'a'.repeat(1_000_001)}';`],
  ['mapped.js', client + '//# sourceMappingURL=app.js.map\n'],
])('uses safety-only analysis when %s looks generated', async (file, input) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': client, [file]: input });
  // When
  const selected = analysisFiles([join(root, file)], { root });
  // Then
  expect(selected.files).not.toContain(join(root, file));
  expect(selected.skippedFiles).toContain(join(root, file));
  expect(selected.items).toContainEqual(expect.objectContaining({ file, kind: 'unsupported-file',
    message: `${file} looks generated or minified and mentions Daytona or imports another module; review it or its generator.` }));
});

it.each(['app.min.js', 'mapped.js', 'large.ts'])('refuses generated %s custom config without rewriting it', async file => {
  // Given
  const suffix = file === 'mapped.js' ? '\n//# sourceMappingURL=app.js.map\n' : file === 'large.ts' ? `\n/*${'a'.repeat(1_000_001)}*/` : '';
  const input = client + "new Daytona({ apiUrl: 'https://internal.example/api' });" + suffix;
  const root = temporaryProject({ 'package.json': manifest, [file]: input });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('reports a long-line local client consumer neutrally when it is skipped', async () => {
  // Given
  const input = "import { d } from './client'; d.create();\n" + `const payload = '${'a'.repeat(11000)}';\n` + '// source row\n'.repeat(30);
  const root = temporaryProject({ 'client.ts': client, 'consumer.ts': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'consumer.ts',
    message: 'Skipped source with long lines; review it if it uses Daytona.' }));
  expect(readFileSync(join(root, 'consumer.ts'), 'utf8')).toBe(input);
});

it('analyses source when a sourceMappingURL marker is string data, not a comment', () => {
  // Given
  const file = 'app.ts';
  const root = temporaryProject({ [file]: client + "const example = '//# sourceMappingURL=app.js.map';" });
  // When
  const selected = analysisFiles([join(root, file)], { root });
  // Then
  expect(selected.files).toContain(join(root, file));
});

it('leaves generated Daytona source unchanged when the project is upgraded', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': client, 'app.ts': client });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).changedFiles).toEqual(['app.ts', 'package.json']);
  expect(readFileSync(join(root, 'bundle.min.js'), 'utf8')).toBe(client);
});
