/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { expect, it, vi } from 'vitest';
import { diff } from '../src/output.js';
import { contents, temporaryProject } from './helpers.js';
import { expectWithinBudget } from './budget.js';

it('keeps a precise bounded diff when both ends of 10,000 lines change', () => {
  // Given an unchanged interior that cannot be trimmed from either edge.
  const before = Array.from({ length: 10_000 }, (_, index) => `line${index}\n`).join('');
  const after = before.replace('line0\n', 'first\n').replace('line9999\n', 'last\n');
  const allocations = vi.spyOn(globalThis, 'Uint32Array');
  const start = performance.now();
  try {
    // When
    const output = diff([{ file: 'index.ts', before, after }]);
    // Then only changed lines are emitted, without an unbounded matrix.
    expectWithinBudget(performance.now() - start, 2_000);
    expect(allocations.mock.calls.every((args: readonly unknown[]) =>
      typeof args[0] !== 'number' || args[0] <= 4_000_000)).toBe(true);
    expect(output.length).toBeLessThan(5_000);
    const lines = output.split('\n').slice(2);
    expect(lines.filter(line => line.startsWith('-')).map(line => line.slice(1))).toEqual(['line0', 'line9999']);
    expect(lines.filter(line => line.startsWith('+')).map(line => line.slice(1))).toEqual(['first', 'last']);
    if (patchAvailable) {
      const root = temporaryProject({ 'index.ts': before });
      const result = spawnSync('patch', ['--binary', '-p0'], { cwd: root, input: output, encoding: 'utf8' });
      expect(result.status, result.stdout + result.stderr).toBe(0);
      expect(contents(root)['index.ts']).toBe(after);
    }
  } finally {
    allocations.mockRestore();
  }
});

const patchAvailable = spawnSync('patch', ['--version']).status === 0;
it.skipIf(!patchAvailable).each([
  { oldCount: 2_100, newCount: 2_100, newline: '\n' },
  { oldCount: 2_100, newCount: 2_100, newline: '\r\n' },
  { oldCount: 10_000, newCount: 1, newline: '' },
  { oldCount: 1, newCount: 10_000, newline: '' },
])('applies a bounded diff with unequal spans and newline boundaries: %j', ({ oldCount, newCount, newline }) => {
  // Given unchanged context around a large replacement, including EOF without a newline.
  const before = 'context\n'.repeat(5) + Array.from({ length: oldCount }, (_, index) => `old${index}\n`).join('') + 'tail' + newline;
  const after = 'context\n'.repeat(5) + Array.from({ length: newCount }, (_, index) => `new${index}\n`).join('') + 'TAIL' + newline;
  const root = temporaryProject({ 'index.ts': before });
  const patch = diff([{ file: 'index.ts', before, after }]);
  // When
  const result = spawnSync('patch', ['--binary', '-p0'], { cwd: root, input: patch, encoding: 'utf8' });
  // Then
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(contents(root)['index.ts']).toBe(after);
});
