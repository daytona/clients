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
import { invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const client = "import { Daytona } from '@daytonaio/sdk'; export const d = new Daytona();\n";

it.each(['"https://api.stripe.com/v1"', '"/api/v1"', '""', '"http://localhost:3000"'])(
  'ignores unrelated vendor settings when apiUrl is %s', async value => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'public/vendor.min.js': `var vendor = {apiUrl:${value}};` });
    // When
    const result = await invoke(root, ['--dry', '--json']);
    // Then
    expect(result.code).toBe(0);
    const report: Report = JSON.parse(result.output);
    expect(report.items).toEqual([]);
  });

it.each(['apiUrl', 'serverUrl'].flatMap(key => [false, true].map(drop => ({ key, drop }))))(
  'refuses marker-only generated custom $key when drop-api-url=$drop', async ({ key, drop }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': `/* DAYTONA */ var config = {apiKey:"key",${key}:"https://custom.test"};` });
  // When
  const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url',
    message: 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.' }));
});

it.each(['apiUrl', 'serverUrl'])('accepts Daytona generated non-literal %s when drop-api-url asserts Cloud', async key => {
  // Given
  const input = `/* Daytona */ var config = {${key}:runtimeUrl};`;
  const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': input });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, 'bundle.min.js'), 'utf8')).toBe(input);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'bundle.min.js', kind: 'config-field' }));
});

it.each(['""', '"https://app.daytona.io/api"'])('accepts Daytona generated URL %s when it is empty or Cloud', async value => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': `/* Daytona */ var config = {apiUrl:${value}};` });
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
});

it('refuses unverifiable generated URL when Daytona is mentioned without an assertion', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': '/* Daytona */ var config = {apiUrl:runtimeUrl};' });
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).nextSteps).toContain('rerun with --drop-api-url if every DaytonaConfig targets Daytona Cloud');
});

it.each(['src/gen.ts', 'src/worker.js'])('reports ignored local client consumer when %s would change', async file => {
  // Given
  const input = "import { d } from './client'; d.create();\n";
  const root = temporaryProject({ 'package.json': manifest, 'src/client.ts': client, [file]: input,
    '.gitignore': file.endsWith('.js') ? '*.js\n' : file + '\n' });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file, kind: 'fixme',
    message: `1 Git-ignored source file mentions Daytona or would change but was not changed: ${file}; review it or its generator.` }));
  expect(report.changedFiles).not.toContain(file);
  expect(readFileSync(join(root, file), 'utf8')).toBe(input);
});

it.each([{ files: ['one.ts'] }, { files: ['one.ts', 'two.ts'] }])('reports ignored mention-only sources when the files are $files', async ({ files }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': '*.ts\n',
    ...Object.fromEntries(files.map(file => [file, '// Daytona documentation only\n'])) });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  for (const file of files) expect(report.changedFiles).not.toContain(file);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme', message: files.length === 1
    ? '1 Git-ignored source file mentions Daytona or would change but was not changed: one.ts; review it or its generator.'
    : '2 Git-ignored source files mention Daytona or would change but were not changed: one.ts, two.ts; review them or their generators.' }));
});

it('excludes ignored SDK sources when reporting unresolved types', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': client, 'ignored.ts': client,
    '.gitignore': 'ignored.ts\n' });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'types-unresolved')).toEqual([
    expect.objectContaining({ file: 'app.ts', message: expect.stringContaining('in 1 file;') }),
  ]);
});

it('selects large hand-written source when no generated signal is present', () => {
  // Given
  const root = temporaryProject({ 'large.ts': client + '// ordinary source row\n'.repeat(50_000) });
  const file = join(root, 'large.ts');
  // When
  const selected = analysisFiles([file], { root });
  // Then
  expect(selected.files).toContain(file);
  expect(selected.items).toEqual([]);
});

it('rewrites large hand-written source when size is its only generated hint', async () => {
  // Given
  const input = client + 'd.create();\n' + '// ordinary source row\n'.repeat(50_000);
  const root = temporaryProject({ 'package.json': manifest, 'large.ts': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, 'large.ts'), 'utf8')).toContain('d.v0.create();');
});

it.each(['bundle.min.ts', 'bundle.js'])('skips source when %s has a real minification signal', file => {
  // Given
  const input = file.endsWith('.min.ts') ? client : 'var data = 1;'.repeat(1000) + '\n';
  const root = temporaryProject({ [file]: input });
  // When
  const selected = analysisFiles([join(root, file)], { root });
  // Then
  expect(selected.files).toEqual([]);
});
