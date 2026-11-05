/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect } from 'vitest';

/**
 * Assert a wall-clock budget. The release build (sdk_publish.yaml,
 * DAYTONA_UPGRADE_SKIP_WALL_CLOCK=1) skips only this comparison, so its runner's
 * speed cannot block an SDK release while every other assertion of the test
 * still runs; upgrade-tool-ci asserts the budgets on every change.
 */
export function expectWithinBudget(elapsed: number, budget: number): void {
  if (process.env['DAYTONA_UPGRADE_SKIP_WALL_CLOCK'] === '1') return;
  expect(elapsed).toBeLessThan(budget);
}

/** A test's own timeout, which the release build raises like the config's default. */
export function testTimeout(milliseconds: number): number {
  return process.env['DAYTONA_UPGRADE_SKIP_WALL_CLOCK'] === '1' ? Math.max(milliseconds, 300_000) : milliseconds;
}
