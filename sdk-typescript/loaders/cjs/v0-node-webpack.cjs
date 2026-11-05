/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

'use strict'
// The `#v0-loader` entry webpack resolves (its default "webpack" condition).
// v0 is required at run time from node_modules, not inlined: following v0's
// dependency graph makes every webpack production build of the root fail,
// v2-only code included (braces / picomatch: "Self-reference dependency has
// unused export name"). Next.js server builds keep node_modules at run time.
// Called on the first `daytona.v0` access, never at import time.
exports.loadV0 = function loadV0() {
  return __non_webpack_require__('@daytona/sdk/v0')
}
