// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Verifies a CommonJS app that requires the SDK builds with webpack in production
// mode and that the bundle runs, including Image.fromDockerfile resolving a COPY glob.

const sdk = require('@daytona/sdk')
const check = require('./check')

check(sdk).catch((e) => {
  console.error('FAIL:', e && e.message ? e.message : e)
  process.exit(1)
})
