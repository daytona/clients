/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
import { diff } from '../src/output.js';
import { lineEdits } from '../src/line-diff.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const patchAvailable = spawnSync('patch', ['--version']).status === 0;

it.each([
  { before: 'a\nb\nc\nd\n', after: 'c\nd\na\nb\n', common: 2 },
  { before: 'a\nb\na\nc\n', after: 'b\nc\na\nb\n', common: 2 },
  { before: 'a\nb\nc\nd\ne\n', after: 'e\nd\nc\nb\na\n', common: 1 },
])('retains a longest common subsequence across reordered lines: %j', ({ before, after, common }) => {
  // Given repeated/reordered lines requiring Hirschberg alignment, not just trimming.
  // When
  const edits = lineEdits(before, after);
  // Then alignment is minimal and reconstructs both original byte sequences.
  expect(edits.filter(edit => edit.prefix === ' ')).toHaveLength(common);
  expect(edits.filter(edit => edit.prefix !== '+').map(edit => edit.text).join('')).toBe(before);
  expect(edits.filter(edit => edit.prefix !== '-').map(edit => edit.text).join('')).toBe(after);
});

it('shows only insertions when 80 new lines span a 3,000-line file', () => {
  // Given insertions spread across a middle larger than the old diff budget.
  const lines = Array.from({ length: 3_000 }, (_, index) => `line${index}\n`);
  const before = lines.join('');
  const after = lines.map((line, index) => index % 37 === 0 && index / 37 < 80 ? `added${index}\n${line}` : line).join('');
  // When
  const output = diff([{ file: 'index.ts', before, after }]);
  // Then the unchanged interior is not presented as a replacement.
  const body = output.split('\n').slice(2);
  expect(body.filter(line => line.startsWith('-'))).toHaveLength(0);
  expect(body.filter(line => line.startsWith('+'))).toHaveLength(80);
  expect(body.filter(line => line.startsWith(' ')).length).toBeLessThanOrEqual(80 * 6);
  if (patchAvailable) {
    const root = temporaryProject({ 'index.ts': before });
    const result = spawnSync('patch', ['--binary', '-p0'], { cwd: root, input: output, encoding: 'utf8' });
    expect(result.status, result.stdout + result.stderr).toBe(0);
    expect(contents(root)['index.ts']).toBe(after);
  }
});

it('keeps CLI dry output precise when migrations span a 3,000-line source file', async () => {
  // Given distant SDK calls with a large unchanged interior.
  const before = "import { Daytona } from '@daytonaio/sdk';\nnew Daytona().create();\n" +
    Array.from({ length: 2_997 }, (_, index) => `// padding${index}\n`).join('') + 'new Daytona().create();\n';
  const root = temporaryProject({ 'index.ts': before });
  // When
  const result = await invoke(root, ['--dry']);
  // Then the real CLI prints a small patch and preserves the source.
  expect(result.code).toBe(0);
  expect(result.output.split('\n')).toContain('+new Daytona().v0.create();');
  expect(result.output).not.toContain('// padding1000');
  expect(result.output.length).toBeLessThan(5_000);
  expect(contents(root)['index.ts']).toBe(before);
});
