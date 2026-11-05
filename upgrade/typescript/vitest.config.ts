/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { configDefaults, defineConfig } from 'vitest/config';

// Tests that assert wall-clock budgets. upgrade-tool-ci runs them on every
// change; the release build (sdk_publish.yaml, DAYTONA_UPGRADE_SKIP_WALL_CLOCK=1)
// leaves them out, so a slow release runner cannot block an SDK release.
const wallClock = [
  'tests/bounded-environment-scan-inputs.test.ts',
  'tests/callback-budget-regression.test.ts',
  'tests/es5-callback-performance.test.ts',
  'tests/holder-helper-performance.test.ts',
  'tests/holder-performance.test.ts',
  'tests/output-bounded.test.ts',
  'tests/receiver-write-and-extraction-proofs.test.ts',
];

export default defineConfig({
  test: {
    globalSetup: ['tests/build-cli.ts'],
    exclude: [...configDefaults.exclude, ...(process.env['DAYTONA_UPGRADE_SKIP_WALL_CLOCK'] === '1' ? wallClock : [])],
  },
});
