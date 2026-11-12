/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Checks one runtime's result object (runtime-checks.mjs) read from stdin:
// every check passed, and the v0 namespace is in the state the tarball was
// built for (RUNTIME_TEST_EXPECT_V0: `bundled` or `unbundled`).
//   printf '%s' "$json" | node assert-result.mjs

import { readFileSync } from 'node:fs'

const CHECKS = ['listOk', 'notFoundOk', 'toolboxListOk', 'toolboxWatchOk', 'capOk']
const expectV0 = process.env.RUNTIME_TEST_EXPECT_V0

const raw = readFileSync(0, 'utf8').trim()
console.log(`Result: ${raw}`)
let result
try {
  result = JSON.parse(raw)
} catch {
  result = undefined
}
if (result === null || typeof result !== 'object' || Array.isArray(result)) {
  console.error('FAIL: the runtime did not return a JSON result object')
  process.exit(1)
}

const failures = []
for (const key of CHECKS) {
  if (result[key] !== true) failures.push(`${key} (${result[key.replace(/Ok$/, 'Error')] ?? 'no detail'})`)
}
if (!expectV0) failures.push('RUNTIME_TEST_EXPECT_V0 is not set (run via run-all.sh)')
else if (result.v0 !== expectV0) failures.push(`v0: expected ${expectV0}, got ${result.v0}`)

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL: ${failure}`)
  process.exit(1)
}
console.log('PASS')
