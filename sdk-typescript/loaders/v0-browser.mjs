/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Browser builds (the "browser" condition of the package's `#v0-loader`
// import) have no synchronous require, so the v0 namespace is imported
// statically: the bundler resolves it at build time, `daytona.v0` stays a
// synchronous getter, and an app that imports the root carries the v0 SDK
// (its ESM build, `exports["./v0"]`'s browser target, so an import of v0's
// types and errors shares the module). Every client comes from the root,
// so every browser app that builds one carries v0.
import * as v0 from '../dist/esm/v0/index.js'

export function loadV0() {
  return v0
}
