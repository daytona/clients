/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

'use strict'
// Browser builds of the CommonJS root (the "browser" condition of
// `#v0-loader`): a literal require() the bundler follows at build time, of
// the ESM v0 build `exports["./v0"]` points browsers at, so an import of v0's
// types and errors shares the module. Called on the first `daytona.v0` access.
exports.loadV0 = function loadV0() {
  return require('../../esm/v0/index.js')
}
