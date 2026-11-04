/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { UpgradeError } from '../src/model.js';
import { writeChanges } from '../src/write-changes.js';
import { invoke, temporaryProject } from './helpers.js';

afterEach(() => { vi.restoreAllMocks(); syncBuiltinESMExports(); });

it.each([1, 2, 3])('restores every byte when rename %i fails', async nth => {
  // Given
  const source = "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'a.ts': '\uFEFF' + source, 'b.ts': source.replaceAll('\n', '\r\n') });
  const names = fs.readdirSync(root);
  const originals = names.map(name => fs.readFileSync(join(root, name)));
  fs.chmodSync(join(root, 'a.ts'), 0o751);
  const rename = fs.renameSync;
  let count = 0;
  vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
    if (++count === nth) throw new UpgradeError('Injected rename failure');
    rename(from, to);
  });
  syncBuiltinESMExports();
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(1);
  expect(names.map(name => fs.readFileSync(join(root, name)))).toEqual(originals);
  expect(fs.readdirSync(root)).toEqual(names);
  expect(fs.statSync(join(root, 'a.ts')).mode & 0o7777).toBe(0o751);
});

it('leaves every original intact when preparing a temp file partially fails', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'a.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();" });
  const names = fs.readdirSync(root);
  const originals = names.map(name => fs.readFileSync(join(root, name)));
  const write = fs.writeFileSync;
  let count = 0;
  vi.spyOn(fs, 'writeFileSync').mockImplementation((file, data, options) => {
    if (++count >= 2) {
      write(file, 'partial');
      throw new UpgradeError('Injected temp write failure');
    }
    write(file, data, options);
  });
  syncBuiltinESMExports();
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(1);
  expect(names.map(name => fs.readFileSync(join(root, name)))).toEqual(originals);
  expect(fs.readdirSync(root)).toEqual(names);
});

it('preserves source permissions when replacing files successfully', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();" });
  fs.chmodSync(join(root, 'app.ts'), 0o754);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(fs.statSync(join(root, 'app.ts')).mode & 0o7777).toBe(0o754);
  expect(fs.readdirSync(root)).toEqual(['app.ts']);
});

it('prepares and flushes every target before the first replacement', () => {
  // Given
  const root = temporaryProject({ 'a.txt': 'original a', 'b.txt': 'original b' });
  const changes = ['a.txt', 'b.txt'].map(name => ({ file: join(root, name), before: '', after: `new ${name}` }));
  const sync = fs.fsyncSync;
  const rename = fs.renameSync;
  let flushed = 0;
  vi.spyOn(fs, 'fsyncSync').mockImplementation(descriptor => { sync(descriptor); flushed++; });
  vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
    expect(flushed).toBe(2);
    expect(fs.readFileSync(to, 'utf8')).toMatch(/^original /);
    expect(fs.readFileSync(from, 'utf8')).toBe(`new ${String(to).split(/[\\/]/).at(-1)}`);
    rename(from, to);
  });
  // When
  writeChanges(changes);
  // Then
  expect(changes.map(change => fs.readFileSync(change.file, 'utf8'))).toEqual(['new a.txt', 'new b.txt']);
});
