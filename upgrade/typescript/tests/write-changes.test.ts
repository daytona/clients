/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { UpgradeError } from '../src/model.js';
import { WriteFailure, writeChanges } from '../src/write-changes.js';
import { temporaryProject } from './helpers.js';

it('preserves exact bytes when a temp write partially succeeds', () => {
  // Given: fault injection at the filesystem writer avoids exhausting a real disk.
  const root = temporaryProject();
  const first = join(root, 'first.bin'); const second = join(root, 'second.bin');
  const originals = [Buffer.from([0xff, 0xfe, 0x41]), Buffer.from('\uFEFForiginal\r\n')] as const;
  fs.writeFileSync(first, originals[0]); fs.writeFileSync(second, originals[1]);
  const realWrite = fs.writeFileSync;
  let writes = 0;
  const writer = vi.spyOn(fs, 'writeFileSync').mockImplementation((file, data, options) => {
    if (++writes === 2) {
      realWrite(file, 'partial');
      throw new UpgradeError('Injected partial write');
    }
    realWrite(file, data, options);
  });
  syncBuiltinESMExports();
  try {
    // When
    expect(() => writeChanges([
      { file: first, before: '', after: 'updated first' }, { file: second, before: '', after: 'updated second' },
    ])).toThrow(WriteFailure);
    // Then
    expect([fs.readFileSync(first), fs.readFileSync(second)]).toEqual(originals);
  } finally { writer.mockRestore(); syncBuiltinESMExports(); }
});

it('removes newly created targets when a later rename fails', () => {
  // Given
  const root = temporaryProject();
  const first = join(root, 'first.txt'); const second = join(root, 'second.txt');
  const realRename = fs.renameSync;
  const writer = vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
    if (to === second) throw new UpgradeError('Injected rename failure');
    realRename(from, to);
  });
  syncBuiltinESMExports();
  try {
    // When
    expect(() => writeChanges([
      { file: first, before: '', after: 'new first' }, { file: second, before: '', after: 'new second' },
    ])).toThrow(WriteFailure);
    // Then
    expect(fs.readdirSync(root)).toEqual([]);
  } finally { writer.mockRestore(); syncBuiltinESMExports(); }
});

it('continues restoring other files and reports affected paths when rollback itself fails', () => {
  // Given
  const root = temporaryProject({ 'a.txt': 'original a', 'b.txt': 'original b', 'c.txt': 'original c' });
  const first = join(root, 'a.txt'); const second = join(root, 'b.txt'); const third = join(root, 'c.txt');
  const realWrite = fs.writeFileSync;
  const realRename = fs.renameSync;
  const renamer = vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
    if (to === third) throw new UpgradeError('Injected rename failure');
    realRename(from, to);
  });
  const writer = vi.spyOn(fs, 'writeFileSync').mockImplementation((file, data, options) => {
    if (Buffer.isBuffer(data) && data.equals(Buffer.from('original b'))) throw new UpgradeError('Injected rollback failure');
    realWrite(file, data, options);
  });
  syncBuiltinESMExports();
  try {
    // When
    expect(() => writeChanges([first, second, third].map(file => ({ file, before: '', after: 'updated' }))))
      .toThrow(expect.objectContaining({ rollbackFailures: [{ file: second, error: expect.any(UpgradeError) }] }));
    // Then
    expect([fs.readFileSync(first, 'utf8'), fs.readFileSync(third, 'utf8')]).toEqual(['original a', 'original c']);
    expect(fs.readdirSync(root)).toEqual(['a.txt', 'b.txt', 'c.txt']);
  } finally { writer.mockRestore(); renamer.mockRestore(); syncBuiltinESMExports(); }
});
