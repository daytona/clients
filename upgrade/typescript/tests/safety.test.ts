/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { main } from '../src/cli.js';
import { contents, fixture, invoke, temporaryProject } from './helpers.js';

it('refuses without writes when the Git tree is dirty', async () => {
  // Given
  const root = fixture('named-imports');
  const initialized = spawnSync('git', ['init', '-q'], { cwd: root, env: { ...process.env, LC_ALL: 'C', LANG: 'C' } });
  expect(initialized.status).toBe(0);
  const before = contents(root);
  const output: string[] = [];
  // When
  const code = await main([root, '--yes', '--json'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(2);
  expect(JSON.parse(output.join(''))).toMatchObject({ refused: { reason: 'dirty-tree' } });
  expect(contents(root)).toEqual(before);
});
it('skips the Git check and prints a diff when dry', async () => {
  // Given
  const root = fixture('named-imports');
  const before = contents(root);
  // When
  const result = await invoke(root, ['--dry']);
  // Then
  expect(result.code).toBe(0);
  expect(result.output).toContain('@@ -1,');
  expect(contents(root)).toEqual(before);
});
it('prints changed files without writes when print is requested', async () => {
  // Given
  const root = fixture('named-imports');
  const before = contents(root);
  // When
  const result = await invoke(root, ['--print']);
  // Then
  expect(result.code).toBe(0);
  expect(result.output).toContain('daytona.v0.create()');
  expect(contents(root)).toEqual(before);
});
it('keeps strict mode clean for generic unannotated parameters without receiver evidence', async () => {
  // Given
  const root = fixture('unannotated-param-fixme');
  // When
  const result = await invoke(root, ['--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('FIXME(daytona-upgrade)');
});
it('drops a dynamic URL with a warning when the user asserts Cloud', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\nnew Daytona({ apiUrl: process.env.URL });\n" });
  // When
  const result = await invoke(root, ['--drop-api-url', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('apiUrl');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});
it('still refuses explicit custom URLs when drop-api-url is requested', async () => {
  // Given
  const root = fixture('custom-api-url-stop');
  const before = contents(root);
  // When
  const result = await invoke(root, ['--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
it.each(['DAYTONA_API_URL', 'DAYTONA_SERVER_URL'])('reports %s when found in deployment files', async name => {
  // Given
  const root = fixture('named-imports');
  writeFileSync(join(root, '.env.local'), `${name}=https://custom.example\n`);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ file: '.env.local', kind: 'custom-api-url', line: 1 })]) });
});
it.each([{ flags: ['--unknown'] }, { flags: ['--suggest-v2', '--remove-suggestions'] }, { flags: ['one', 'two'] }])('refuses bad arguments $flags', async ({ flags }) => {
  // Given
  const output: string[] = [];
  // When
  const code = await main([...flags, '--json'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(2);
  expect(JSON.parse(output.join(''))).toMatchObject({ refused: { reason: 'bad-arguments' } });
});
it('refuses when no Daytona code or dependency exists', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "console.log('@daytona/sdk');\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'no-daytona' } });
});
it.each(['--help', '--version'])('prints %s without project discovery', async flag => {
  // Given
  const output: string[] = [];
  // When
  const code = await main([flag], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(0);
  expect(output.join('')).toContain(flag === '--version' ? '0.0.0' : '--suggest-v2');
});
