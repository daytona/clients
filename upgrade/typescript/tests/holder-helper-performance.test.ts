/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { performance } from 'node:perf_hooks';
import { expect, it } from 'vitest';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

const sizes = process.env['DAYTONA_UPGRADE_BENCHMARK'] === '1' ? [200, 400, 800] : [200];

it.each(sizes)('finishes helper-chain analysis within budget when %i holders are independent', async count => {
  // Given ten helper bodies per class (including the intentionally bounded tail).
  const helpers = Array.from({ length: 10 }, (_, index) =>
    `h${index}() { ${index === 9 ? 'return 1;' : `return this.h${index + 1}();`} }`).join('\n');
  const services = Object.fromEntries(Array.from({ length: count }, (_, index) => [`service${index}.ts`,
    `import { Daytona } from '@daytonaio/sdk';
export class Service${index} {
  private daytona = new Daytona();
  ${helpers}
  async run() {
    this.h0();
    return this.daytona.create();
  }
}`]));
  const root = temporaryProject({ ...services,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"target":"ES2022","module":"NodeNext","moduleResolution":"NodeNext","strict":true,"skipLibCheck":true},"include":["*.ts"]}',
  });
  installFakeSdk(root);
  // When the real CLI indexes every helper and expands virtual dispatch.
  const start = performance.now();
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  const elapsed = performance.now() - start;
  console.info(`holder helpers: ${count} classes, ${(elapsed / 1000).toFixed(3)} s`);
  // Then the existing depth budget stays conservative, without quadratic work.
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).changedFiles).toHaveLength(count + 1);
  expect(elapsed).toBeLessThan(count * 150);
}, 180_000);
