// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Same as test.js for an app that imports the SDK, so webpack bundles the ESM build.

import * as sdk from '@daytona/sdk'
import check from './check.js'

check(sdk).catch((e) => {
  console.error('FAIL:', e && e.message ? e.message : e)
  process.exit(1)
})
