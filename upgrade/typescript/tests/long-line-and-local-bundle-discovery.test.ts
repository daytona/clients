/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { analysisFiles } from '../src/discovery.js';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const client = "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();\n";

it('migrates a local client consumer when its short i18n module has one long constant', async () => {
  // Given
  const input = `import { client } from './client'; export const translations = '${'a'.repeat(900)}'; client.create();\n`;
  const root = temporaryProject({ 'package.json': manifest, 'client.ts': client, 'i18n.ts': input });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, 'i18n.ts'), 'utf8')).toContain('client.v0.create()');
});

it.each([501, 900, 9999, 10000])('applies the speed threshold when its single line has %s characters', length => {
  // Given
  const root = temporaryProject({ 'data.ts': `//${'x'.repeat(length - 2)}` });
  const file = join(root, 'data.ts');
  // When
  const selected = analysisFiles([file], { root });
  // Then
  expect(selected.files).toEqual(length < 10000 ? [file] : []);
  expect(selected.skippedFiles).toEqual(length < 10000 ? [] : [file]);
});

it.each(["import { client } from './client'", "import { client } from '@local/client'", "import { client } from 'workspace-client'",
  "const { client } = require('./client')", "export { client } from './client'", "import('./client')"])(
  'reports a skipped bundle when it uses %s without mentioning Daytona', async statement => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'bundle.min.js': `${statement}; client.create();` });
    // When
    const result = await invoke(root, ['--dry', '--json', '--strict']);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(1);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'bundle.min.js', kind: 'unsupported-file' }));
    expect(report.changedFiles).not.toContain('bundle.min.js');
  });

it('reports a skipped local-import bundle when Git also ignores it', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': 'bundle.min.js\n',
    'bundle.min.js': "import { client } from './client'; client.create();" });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--dry', '--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'bundle.min.js', kind: 'unsupported-file' }));
});

it.each([false, true])('caps ignored paths and uses mode-appropriate plural guidance when suggest=%s', async suggest => {
  // Given
  const files = Array.from({ length: 8 }, (_, index) => `ignored${index}.ts`);
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': 'ignored*.ts\n',
    ...Object.fromEntries(files.map(file => [file, '// Daytona\n'])) });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--json', ...(suggest ? ['--suggest-v2'] : [])]);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme').map(item => item.message)).toEqual([
    '8 Git-ignored source files mention Daytona or would change but were not changed: ignored0.ts, ignored1.ts, ignored2.ts, ignored3.ts, ignored4.ts and 3 more; review them or their generators.',
  ]);
});

it('uses singular analysis guidance when one ignored source is found in suggestion mode', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': 'ignored.ts\n', 'ignored.ts': '// Daytona\n' });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme', message:
    '1 Git-ignored source file mentions Daytona or would change but was not changed: ignored.ts; review it or its generator.' }));
});

it('reports an ignored Daytona bundle once with the specific skipped-file advice', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, '.gitignore': 'app.min.js\n', 'app.min.js': '// Daytona\n' });
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'app.min.js')).toEqual([expect.objectContaining({ kind: 'unsupported-file' })]);
});

it.each(['big.ts', 'app.min.ts'])('warns about the dependency bump when a skipped local consumer is %s', async file => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'client.ts': client,
    [file]: `//${'x'.repeat(9998)}\nimport { client } from './client'; client.create();\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(report.changedFiles).toContain('package.json');
  expect(report.changedFiles).not.toContain(file);
  expect(report.items).toContainEqual(expect.objectContaining({ file, kind: 'unsupported-file',
    message: expect.stringContaining('The Daytona dependency is being updated to 2.x; this file needs manual migration.') }));
});
