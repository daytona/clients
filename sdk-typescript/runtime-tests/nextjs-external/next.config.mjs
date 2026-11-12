/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Not bundled: Node loads the package's own build from node_modules at run
// time, the path that breaks when the published `exports` layout is wrong.
export default {
  serverExternalPackages: ['@daytona/sdk'],
}
