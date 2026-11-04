/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
import { diff } from '../src/output.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it('limits a replacement hunk to three unchanged lines on each side', () => {
  // Given
  const before = Array.from({ length: 15 }, (_, index) => `line${index + 1}\n`).join('');
  // When
  const output = diff([{ file: 'index.ts', before, after: before.replace('line8\n', 'changed\n') }]);
  // Then
  expect(output).toBe('--- index.ts\n+++ index.ts\n@@ -5,7 +5,7 @@\n line5\n line6\n line7\n-line8\n+changed\n line9\n line10\n line11\n');
});

it('splits distant edits into hunks with independently shifted line numbers', () => {
  // Given
  const before = Array.from({ length: 25 }, (_, index) => `line${index + 1}\n`).join('');
  const after = before.replace('line5\n', 'added1\nadded2\n').replace('line20\n', 'changed\n');
  // When
  const output = diff([{ file: 'index.ts', before, after }]);
  // Then
  expect(output).toBe('--- index.ts\n+++ index.ts\n' +
    '@@ -2,7 +2,8 @@\n line2\n line3\n line4\n-line5\n+added1\n+added2\n line6\n line7\n line8\n' +
    '@@ -17,7 +18,7 @@\n line17\n line18\n line19\n-line20\n+changed\n line21\n line22\n line23\n');
});

it('merges nearby edits without repeating overlapping context', () => {
  // Given
  const before = 'a\nb\nc\nd\ne\nf\ng\nh\ni\n';
  // When
  const output = diff([{ file: 'index.ts', before, after: before.replace('b\n', 'B\n').replace('g\n', 'G\n') }]);
  // Then
  expect(output).toBe('--- index.ts\n+++ index.ts\n@@ -1,9 +1,9 @@\n a\n-b\n+B\n c\n d\n e\n f\n-g\n+G\n h\n i\n');
});

it('emits nothing when all supplied files are unchanged', () => {
  // Given
  const changes = ['', 'same\n', 'same'].map(text => ({ file: 'index.ts', before: text, after: text }));
  // When
  const output = diff(changes);
  // Then
  expect(output).toBe('');
});

const patchAvailable = spawnSync('patch', ['--version']).status === 0;
const longFile = Array.from({ length: 30 }, (_, index) => `line${index + 1}\n`).join('');
it.skipIf(!patchAvailable).each([
  { before: '', after: 'new\n' },
  { before: 'old\n', after: '' },
  { before: 'a\nb\n', after: 'first\na\nb\n' },
  { before: 'a\nb\n', after: 'a\nb\nlast\n' },
  { before: 'a\nb\nc\n', after: 'a\nc\n' },
  { before: 'a\nb\na\nb\nc\n', after: 'a\nx\na\nb\nc\n' },
  { before: 'same', after: 'same\n' },
  { before: 'same\n', after: 'same' },
  { before: 'old', after: 'new' },
  { before: 'old\nunchanged', after: 'new\nunchanged' },
  { before: 'α\r\nβ\r\nγ\r\n', after: 'α\r\nδ\r\nγ\r\n' },
  { before: longFile, after: longFile.replace('line5\n', 'extra\nreplacement\n').replace('line25\n', '') },
])('emits an applicable patch for line and newline boundaries: %j', change => {
  // Given
  const root = temporaryProject({ 'index.ts': change.before });
  const patch = diff([{ file: 'index.ts', ...change }]);
  // When
  const result = spawnSync('patch', ['--binary', '-p0'], { cwd: root, input: patch, encoding: 'utf8' });
  // Then
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(contents(root)['index.ts']).toBe(change.after);
});

it.skipIf(!patchAvailable)('applies each changed file while omitting unchanged files', () => {
  // Given
  const root = temporaryProject({ 'first.ts': 'a\nb\nc\n', 'second.ts': 'old', 'same.ts': 'same\n' });
  const patch = diff([
    { file: 'first.ts', before: 'a\nb\nc\n', after: 'a\nB\nc\n' },
    { file: 'second.ts', before: 'old', after: 'new\n' },
    { file: 'same.ts', before: 'same\n', after: 'same\n' },
  ]);
  // When
  const result = spawnSync('patch', ['-p0'], { cwd: root, input: patch, encoding: 'utf8' });
  // Then
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(patch).not.toContain('same.ts');
  expect(contents(root)).toEqual({ 'first.ts': 'a\nB\nc\n', 'second.ts': 'new\n', 'same.ts': 'same\n' });
});

it.each(['--dry', '--print'])('preserves files while showing the requested output for %s', async flag => {
  // Given
  const before = Array.from({ length: 9 }, (_, index) => `// padding${index}\n`).join('') +
    "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n";
  const root = temporaryProject({ 'index.ts': before });
  // When
  const result = await invoke(root, [flag]);
  // Then
  expect(result.code).toBe(0);
  expect(result.output).toContain('new Daytona().v0.create()');
  expect(result.output.includes('// padding0')).toBe(flag === '--print');
  expect(result.output.includes('@@ ')).toBe(flag === '--dry');
  expect(contents(root)['index.ts']).toBe(before);
});
