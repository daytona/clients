/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// A toolbox call on a stopped sandbox: the proxy answers 409
// SANDBOX_NOT_RUNNING (source DAYTONA_PROXY), which every toolbox path throws
// as SandboxNotRunningError, a ConflictError and not a NotFoundError, on both
// axios adapters. The Python twin is tests/test_sandbox_not_running.py.

import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { ConflictError, NotFoundError, SandboxNotRunningError } from '../src/v2/index'
import { forceFetchAdapterForTests } from '../src/v2/boundedAdapter'
import type { Sandbox } from '../src/v2/Sandbox'
import type { Daytona as DaytonaV2 } from '../src/v2/Daytona'
import { clientAt } from './helpers/client'
import { lazySandbox } from './lazySandbox'
import { startFakeApi } from './networkPolicyTestServer'

const NOT_RUNNING = { code: 'SANDBOX_NOT_RUNNING', message: 'sandbox is not running', source: 'DAYTONA_PROXY', trace_id: 'trace-1' }

const CALLS: ReadonlyArray<readonly [string, (sandbox: Sandbox, dir: string) => Promise<unknown>]> = [
  ['exec', (sandbox) => sandbox.exec('echo hi')],
  ['fs.list', (sandbox) => sandbox.fs.list('/home')],
  ['fs.readBytes', (sandbox) => sandbox.fs.readBytes('/home/a.txt')],
  ['fs.downloadFile', (sandbox, dir) => sandbox.fs.downloadFile('/home/a.txt', join(dir, 'a.txt'))],
  ['fs.watchDir', (sandbox) => sandbox.fs.watchDir('/home', () => undefined)],
  ['git.getConfig', (sandbox) => sandbox.git.getConfig('user.name')],
]

describe.each([false, true])('stopped sandbox toolbox calls (fetch adapter: %s)', (forceFetch) => {
  beforeEach(() => forceFetchAdapterForTests(forceFetch))
  afterEach(() => forceFetchAdapterForTests(false))

  it.each(CALLS)('%s throws SandboxNotRunningError', async (_name, call) => {
    const api = await startFakeApi([{ status: 409, body: NOT_RUNNING }])
    const client = clientAt(api.url)
    const dir = await mkdtemp(join(tmpdir(), 'not-running-'))
    try {
      const sandbox = lazySandbox(client.v2 as unknown as DaytonaV2, 'sbx', { toolboxUrl: api.url, accessToken: 'caller_tok' })
      const error = await call(sandbox, dir).then(() => undefined, (failure: unknown) => failure)
      expect(error).toBeInstanceOf(SandboxNotRunningError)
      expect(error).toBeInstanceOf(ConflictError)
      expect(error).not.toBeInstanceOf(NotFoundError)
      expect(error).toMatchObject({ statusCode: 409, code: 'SANDBOX_NOT_RUNNING', source: 'DAYTONA_PROXY', traceId: 'trace-1' })
    } finally {
      await client.close()
      await api.close()
      await rm(dir, { recursive: true, force: true })
    }
  })
})
