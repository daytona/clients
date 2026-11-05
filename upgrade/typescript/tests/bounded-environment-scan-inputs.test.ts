/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { composeEnvFiles } from '../src/compose-env-files.js';
import { environmentText, MAX_ENVIRONMENT_BYTES } from '../src/environment-text.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { expectWithinBudget, testTimeout } from './budget.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const cli = fileURLToPath(new URL('../dist/cli.js', import.meta.url));

it('ignores a large unrelated YAML specification within a bounded CLI run', async () => {
  // Given a generated 20 MB spec with nested services/include words, not Compose keys.
  const root = temporaryProject({ 'package.json': manifest,
    'openapi.yaml': 'openapi: 3.0.0\npaths:\n' + '  /services/include: {description: unrelated}\n'.repeat(500_000) });
  const start = performance.now();
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then unrelated specs neither enter the YAML parser nor produce review noise.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expectWithinBudget(performance.now() - start, 2_000);
}, testTimeout(3_000));

it.each(['compose.yaml', 'stack.yaml'])('reviews oversized Compose-looking %s without parsing it', async file => {
  // Given a recognized Compose file larger than the parser budget.
  const root = temporaryProject({ 'package.json': manifest,
    [file]: 'services: {}\nx-padding: |\n' + '  padding\n'.repeat(600_000) });
  const start = performance.now();
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the existing unresolved environment review replaces an unbounded parse.
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file, kind: 'env-api-url' }));
  expectWithinBudget(performance.now() - start, 2_000);
}, testTimeout(3_000));

it('caps direct Compose parsing even when called without a bounded file reader', () => {
  // Given an oversized input with a costly YAML collection, rather than a block scalar.
  const lines = ['services: {}', ...Array.from({ length: 180_000 }, () => 'x-unused: [one, two, three, four]')];
  const start = performance.now();
  // When
  const result = composeEnvFiles(lines);
  // Then an unresolved reference records the skipped parse.
  expect(result).toEqual({ compose: true, references: [{ value: undefined, line: 0, column: 0 }], includes: [] });
  expectWithinBudget(performance.now() - start, 500);
});

it.each(['.env', 'Dockerfile'])('reviews oversized %s instead of certifying unseen endpoints', async file => {
  // Given a runtime environment input with a custom URL beyond the byte budget.
  const root = temporaryProject({ 'package.json': manifest,
    [file]: '# padding\n'.repeat(600_000) + '\nDAYTONA_API_URL=https://self.example/api\n' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then omitted bytes remain unresolved, never a false Cloud certification.
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual([expect.objectContaining({ file, kind: 'env-api-url' })]);
  expect(contents(root)).toEqual(before);
});

it('discards a partial endpoint at the read cap instead of refusing a truncated Cloud URL', () => {
  // Given an endpoint crossing the byte boundary.
  const padding = '#'.repeat(MAX_ENVIRONMENT_BYTES - 25) + '\n';
  const root = temporaryProject({ '.env': padding + 'DAYTONA_API_URL=https://app.daytona.io/api\n' });
  // When
  const result = environmentText(join(root, '.env'));
  // Then only complete lines are available for classification.
  expect(result?.truncated).toBe(true);
  expect(result?.text.includes('DAYTONA_API_URL')).toBe(false);
  expect(Buffer.byteLength(result?.text ?? '')).toBeLessThanOrEqual(MAX_ENVIRONMENT_BYTES);
});

it.skipIf(process.platform === 'win32')('skips FIFO scan inputs without blocking the real CLI', () => {
  // Given named pipes with YAML and environment filenames.
  const root = temporaryProject({ 'package.json': manifest });
  for (const file of ['pipe.yaml', 'compose.yaml', '.env', 'Dockerfile']) {
    const fifo = spawnSync('mkfifo', [join(root, file)], { encoding: 'utf8' });
    expect(fifo.status, fifo.stderr).toBe(0);
  }
  // When the CLI runs in a child process with a deadline to bound blocking reads.
  const result = spawnSync(process.execPath, [cli, root, '--dry', '--strict', '--json'], { encoding: 'utf8', timeout: 3_000 });
  // Then non-regular files are skipped, not refused or flagged.
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  expect(JSON.parse(result.stdout).items).toEqual([]);
});
