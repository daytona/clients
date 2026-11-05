/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { performance } from 'node:perf_hooks';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

const sizes = process.env['DAYTONA_UPGRADE_BENCHMARK'] === '1' ? [1, 3] : [1];

it.each(sizes)('finishes callback analysis within budget when %i files contain real ES5 async helpers', async count => {
  // Given real compiler output, not a hand-simplified __awaiter/__generator pair.
  const compiled = ts.transpileModule(`import { Daytona } from '@daytonaio/sdk';
async function main() {
  const d = new Daytona();
  const s = await d.create();
  await s.process.executeCommand('ls');
  await d.delete(s);
}
main();`, { compilerOptions: { target: ts.ScriptTarget.ES5, module: ts.ModuleKind.CommonJS } }).outputText;
  const root = temporaryProject({
    ...Object.fromEntries(Array.from({ length: count }, (_, index) => [`main${index}.js`, compiled])),
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  // When the real CLI analyzes all helpers together in a plain-JS project.
  const start = performance.now();
  const result = await invoke(root, ['--dry', '--json']);
  const elapsed = performance.now() - start;
  console.info(`ES5 callbacks: ${count} files, ${(elapsed / 1000).toFixed(3)} s`);
  // Then imports/dependencies migrate, while the assignment-backed ES5 client
  // remains review-only at both call sites rather than gaining a guessed .v0.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  expect(report.changedFiles).toHaveLength(count + 1);
  expect(report.items.filter(item => item.kind === 'fixme').map(item => item.file).sort()).toEqual(
    Array.from({ length: count }, (_, index) => [`main${index}.js`, `main${index}.js`]).flat());
  expect(elapsed).toBeLessThan(10_000);
}, 20_000);
