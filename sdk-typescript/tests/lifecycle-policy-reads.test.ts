/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHttpClient } from '../src/v2/http'
import { createSandbox } from './helpers/sandbox'
import { startFakeApi } from './networkPolicyTestServer'

// `updateLifecyclePolicy()` reads the sandbox first only when its class is
// unknown, or for a macOS sandbox whose persistence is unknown, as Python
// does: a known Linux sandbox sends the PATCH alone. The Python twin is
// tests/test_lifecycle_policy_reads.py.
const ECHO = { id: 'sbx', lifecycle_policy: { timeout_seconds: 120 } }
const detail = (sandboxClass: string, extra: Record<string, unknown> = {}) => ({ id: 'sbx', class: sandboxClass, name: 'sbx', status: 'running', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, port_map: [], region_id: 'region-a', ...extra })

it.each([
  ['known linux', 'linux', [], ['PATCH']],
  ['unknown class', undefined, [detail('linux')], ['GET', 'PATCH']],
  ['macos, persistence unknown', 'macos', [detail('macos', { persistent: true })], ['GET', 'PATCH']],
] as const)('reads the sandbox before the update only when it must (%s)', async (_name, known, answers, sent) => {
  const api = await startFakeApi([...answers.map((body) => ({ status: 200, body })), { status: 200, body: ECHO }])
  try {
    const sandbox = createSandbox(known === undefined ? 'sbx' : { id: 'sbx', class: known, status: 'running' }, createHttpClient(api.url, 'key'))
    await sandbox.updateLifecyclePolicy({ timeoutSeconds: 120 })
    expect(api.requests.map((request) => request.method)).toEqual(sent)
  } finally {
    await api.close()
  }
})
