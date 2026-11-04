/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

'use strict'
// Synchronous loader for the bundled v0 namespace, used by the CommonJS root
// build through `#v0-loader` (the build copies this file to dist/cjs/loaders/
// and maps it in dist/cjs/package.json "imports").
// CommonJS on purpose: `./v0` is CommonJS only. Called on the first
// `daytona.v0` access, never at import time.
//
// A literal relative require() of the file exports["./v0"] points at: esbuild
// and plain Node follow it, so a bundled Node app (AWS CDK NodejsFunction)
// carries v0 inside the bundle and needs no node_modules, and a static
// `import ... from '@daytona/sdk/v0'` shares the same module (`instanceof`
// holds). webpack resolves `#v0-loader` to v0-node-webpack.cjs instead (its
// "webpack" condition).
exports.loadV0 = function loadV0() {
  return require('../v0/index.js')
}
