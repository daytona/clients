/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { performance } from 'node:perf_hooks';
import { expect, it } from 'vitest';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { expectWithinBudget, testTimeout } from './budget.js';

it('finishes holder analysis within 30 seconds when 200 service files are independent', async () => {
  // Given a two-client-call service shape, scaled to 200 files.
  const services = Object.fromEntries(Array.from({ length: 200 }, (_, index) => [`service${index}.ts`,
    `import { Daytona } from '@daytonaio/sdk';
export class Service${index} {
  private daytona = new Daytona();
  async run() {
    const s = await this.daytona.create();
    return this.daytona.delete(s);
  }
}`]));
  const root = temporaryProject({
    ...services,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"target":"ES2022","module":"NodeNext","moduleResolution":"NodeNext","strict":true,"skipLibCheck":true},"include":["*.ts"]}',
  });
  installFakeSdk(root);
  // When the real CLI analyzes the whole project, including checker startup.
  const start = performance.now();
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  const elapsed = performance.now() - start;
  // Then every service is migrated within a generous CI budget.
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).changedFiles).toHaveLength(201);
  expectWithinBudget(elapsed, 30_000);
}, testTimeout(60_000));
