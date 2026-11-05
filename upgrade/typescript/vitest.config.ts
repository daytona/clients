/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig } from 'vitest/config';

// The release build (sdk_publish.yaml) sets DAYTONA_UPGRADE_SKIP_WALL_CLOCK=1:
// tests/budget.ts then skips the wall-clock budget assertions and every test
// gets a generous timeout, so a slow release runner cannot block an SDK
// release. upgrade-tool-ci runs with the budgets on every change.
const release = process.env['DAYTONA_UPGRADE_SKIP_WALL_CLOCK'] === '1';

export default defineConfig({
  test: {
    globalSetup: ['tests/build-cli.ts'],
    // Set here, not on the command line: a CLI --testTimeout would override
    // the release value (publish-gates.test.mjs checks the npm script).
    // The 20 s default (vitest's own is 5 s) is the timeout the suite has
    // always run with; release mode raises it further.
    testTimeout: release ? 300_000 : 20_000,
    hookTimeout: release ? 300_000 : 20_000,
  },
});
