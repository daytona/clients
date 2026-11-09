/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Offline tests for the TypeScript examples. Each test runs one example in its
 * own process with test-support/fake-sdk.ts preloaded (the root client's
 * `.v2` becomes an in-memory fake) and asserts the exit code run-examples.sh
 * would see, plus what the example created and cleaned up. Run from examples:
 *
 *     node --import tsx --test typescript/examples.test.ts
 */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import type { Record, Scenario } from './test-support/fake-sdk.js'

const EXAMPLES = dirname(dirname(fileURLToPath(import.meta.url)))

function runExample(entry: string, scenario: Scenario = {}): { code: number | null; record: Record; output: string } {
  const dir = mkdtempSync(join(tmpdir(), 'daytona-example-'))
  const recordPath = join(dir, 'record.json')
  try {
    const child = spawnSync(
      process.execPath,
      ['--import', 'tsx', '--import', './typescript/test-support/fake-sdk.ts', entry],
      {
        cwd: EXAMPLES,
        encoding: 'utf8',
        env: {
          ...process.env,
          // DAYTONA_KEEP would make volumes_share skip its cleanup.
          DAYTONA_KEEP: '',
          DAYTONA_API_KEY: 'test-key',
          EXAMPLE_FAKE_SCENARIO: JSON.stringify(scenario),
          EXAMPLE_FAKE_RECORD: recordPath,
          EXAMPLE_SYNC_WAIT_SECONDS: '0',
        },
        timeout: 60_000,
      },
    )
    const output = `${child.stdout}${child.stderr}`
    if (!existsSync(recordPath)) {
      throw new Error(`${entry} exited (status=${child.status}, signal=${child.signal}) before writing its record:\n${output}`)
    }
    const record = JSON.parse(readFileSync(recordPath, 'utf8')) as Record
    return { code: child.status, record, output }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

const example = (name: string) => `typescript/${name}/index.ts`
const SECRET_NAME = /^EXAMPLE_API_KEY_[0-9A-F]{8}$/

for (const name of ['exec-command', 'file-operations', 'git', 'secrets-env', 'dynamic-build', 'dockerfile-sandbox', 'volumes', 'lifecycle']) {
  test(`${name} skips work and cleans up when create returns a non-started sandbox`, () => {
    // Given an accepted create whose readiness wait did not finish.
    const scenario = { sandboxStates: ['creating'] }
    // When the real example runs through its public entrypoint.
    const { code, record } = runExample(example(name), scenario)
    // Then no commands run on that sandbox and acquisition is still cleaned up.
    assert.equal(code, 1)
    assert.equal(record.sandboxes.length, 1)
    assert.deepEqual(record.commands, [])
    assertCleanedUp(record)
    // The other resources the example acquired are cleaned up too.
    if (name === 'secrets-env') {
      assert.equal(record.secretSets.length, 1)
      assert.deepEqual(record.deletedSecrets, record.secretSets.map((set) => set.name))
    }
    if (name === 'volumes') {
      assert.equal(record.createdVolumes.length, 1)
      assert.deepEqual(record.deletedVolumes, record.createdVolumes)
    }
  })
}

for (const name of ['dynamic-build', 'dockerfile-sandbox']) test(`${name} skips a non-started second sandbox`, () => {
  // Given a first sandbox that starts and a second one still provisioning.
  const scenario = { sandboxStates: ['started', 'creating'] }
  // When running the multi-sandbox example.
  const { code, record } = runExample(example(name), scenario)
  // Then the second sandbox never receives a command, and both are deleted.
  assert.equal(code, 1)
  assert.equal(record.sandboxes.length, 2)
  assert.equal(record.commands.some((entry) => entry.sandboxId === record.sandboxes[1]), false)
  assertCleanedUp(record)
})

test('volume sharing skips a non-started sandbox and still deletes its resources', () => {
  // Given a volume-backed create that returns before readiness.
  const scenario = { sandboxStates: ['creating'] }
  // When running the persistence example.
  const { code, record } = runExample('volumes_share.ts', scenario)
  // Then no sandbox work runs and both sandbox and volume are cleaned up.
  assert.equal(code, 1)
  assert.equal(record.sandboxes.length, 1)
  assert.equal(record.createdVolumes.length, 1)
  assert.deepEqual(record.commands, [])
  assertCleanedUp(record)
  assert.deepEqual(record.deletedVolumes, record.createdVolumes)
})

function assertCleanedUp(record: Record): void {
  assert.deepEqual([...record.deletedSandboxes].sort(), [...record.sandboxes].sort())
}

for (const name of ['exec-command', 'file-operations', 'git', 'lifecycle', 'secrets-env', 'dynamic-build']) {
  test(`${name} succeeds against the fake SDK`, () => {
    const { code, record, output } = runExample(example(name))
    assert.equal(code, 0, output)
    assertCleanedUp(record)
  })
}

// Each case names the FAIL line the injected failure must produce, so an
// unrelated crash (also exit 1) cannot pass the test.
for (const [name, override, reported] of [
  ['exec-command', ['Hello World from CMD', 1, ''], /FAIL.* command failed \(exit=1\)/],
  ['file-operations', ['script\\.sh$', 127, 'not found'], /FAIL.* the uploaded script failed \(exit=127\)/],
  ['git', ['command -v git', 100, 'apt failed'], /FAIL.* could not ensure git: apt failed/],
  ['lifecycle', ['Hello World from run', 2, ''], /FAIL.* run failed \(exit=2\)/],
] as const) {
  test(`${name} exits non-zero when its command fails`, () => {
    const { code, record, output } = runExample(example(name), { overrides: [[...override]] })
    assert.equal(code, 1, output)
    assert.match(output, reported)
    assertCleanedUp(record)
  })
}

test('build-logs fails when the failed build log is empty', () => {
  const { code, output } = runExample(example('build-logs'), {
    failBuildsContaining: 'intentional-build-failure-',
    emptyBuildLogs: true,
  })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* the failed build's log was empty/)
})

test('build-logs reads the failed build through the error image id', () => {
  const { code, record, output } = runExample(example('build-logs'), { failBuildsContaining: 'intentional-build-failure-' })
  assert.equal(code, 0, output)
  assert.deepEqual(record.followedImages, ['img_failed'])
  assertCleanedUp(record)
})

test('build-logs fails when a deliberately broken build unexpectedly succeeds', () => {
  // Given: the fake accepts even the Dockerfile containing a failing command.
  // When: the example evaluates the failed-build demonstration.
  const { code, record, output } = runExample(example('build-logs'))
  // Then: accepting the broken build cannot produce a successful example run.
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* the deliberately broken build unexpectedly created/)
  assertCleanedUp(record)
})

test('build-logs does not pass partial output after a log read error', () => {
  const { code, output } = runExample(example('build-logs'), {
    failBuildsContaining: 'intentional-build-failure-',
    buildLogsThrowAfterOutput: true,
  })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* the failed build's log could not be read: Error: stream interrupted/)
  assert.doesNotMatch(output, /PASS.* the failed build's log contains/)
})

test('exec-command exits non-zero when the interpreter run fails', () => {
  const { code, output } = runExample(example('exec-command'), { interpreterExitCode: 1 })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* code failed \(exit=1, error=none\)/)
})

test('exec-command exits non-zero when the interpreter code raises', () => {
  const { code, output } = runExample(example('exec-command'), { interpreterError: 'NameError' })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* code failed \(exit=0, error=NameError\)/)
})

test('secrets-env uses a run-scoped secret it never overwrites, and deletes it', () => {
  const { code, record, output } = runExample(example('secrets-env'))
  assert.equal(code, 0, output)
  assert.equal(record.secretSets.length, 1)
  const [{ name, ifNotExists }] = record.secretSets
  assert.match(name, SECRET_NAME)
  assert.equal(ifNotExists, true)
  assert.deepEqual(record.deletedSecrets, [name])
})

for (const [label, scenario, reported] of [
  ['an env var mismatch', { overrides: [['"\\$LOG_LEVEL"', 0, 'info']] }, /FAIL.* \$LOG_LEVEL = "info" \(expected "debug", exit=0\)/],
  ['a failed env var read', { overrides: [['"\\$LOG_LEVEL"', 1, 'debug']] }, /FAIL.* \$LOG_LEVEL = "debug" \(expected "debug", exit=1\)/],
  ['a leaked secret', { leakSecrets: true }, /FAIL.* leaked the real secret value/],
  ['a missing placeholder', { overrides: [['"\\$EXAMPLE_API_KEY_', 0, '']] }, /FAIL.* is not set inside the sandbox/],
  ['a failed secret read', { overrides: [['"\\$EXAMPLE_API_KEY_', 1, 'x']] }, /FAIL.* reading \$EXAMPLE_API_KEY_\w+ failed \(exit=1\)/],
] as [string, Scenario, RegExp][]) {
  test(`secrets-env exits non-zero on ${label}`, () => {
    const { code, record, output } = runExample(example('secrets-env'), scenario)
    assert.equal(code, 1, output)
    assert.match(output, reported)
    assert.equal(record.deletedSecrets.length, 1)
    assert.match(record.deletedSecrets[0], SECRET_NAME)
  })
}

test('secrets-env deletes its secret when listing fails', () => {
  const { code, record, output } = runExample(example('secrets-env'), { secretIterThrows: 'list failed' })
  assert.equal(code, 1, output)
  assert.match(output, /list failed/)
  assert.equal(record.deletedSecrets.length, 1)
  assert.match(record.deletedSecrets[0], SECRET_NAME)
})

test('secrets-env fails when it cannot delete its secret', () => {
  const { code, output } = runExample(example('secrets-env'), { secretDeleteThrows: 'delete failed' })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* delete secret EXAMPLE_API_KEY_\w+ failed: Error: delete failed/)
})

test('secrets-env fails when it cannot delete its sandbox', () => {
  const { code, record, output } = runExample(example('secrets-env'), { sandboxDeleteThrows: 'delete failed' })
  assert.equal(code, 1, output)
  assert.match(output, /FAIL.* delete sandbox sbx-0 failed: Error: delete failed/)
  assert.equal(record.deletedSecrets.length, 1)
})

test('secrets-env leaves a secret it did not create', () => {
  const { code, record, output } = runExample(example('secrets-env'), { secretSetThrows: 'already exists' })
  assert.equal(code, 1, output)
  assert.deepEqual(record.deletedSecrets, [])
})

test('volumes_share round-trips through the volume', () => {
  const { code, record, output } = runExample('volumes_share.ts', { volumeData: true })
  assert.equal(code, 0, output)
  assert.equal(record.deletedVolumes.length, 1)
  assert.deepEqual(record.deletedVolumes, record.createdVolumes)
  assertCleanedUp(record)
})

test('volumes cleans up the volume acquired with create enabled', () => {
  // Given: no volumes exist in the fake project.
  // When: the example acquires a named volume and mounts it in three sandboxes.
  const { code, record, output } = runExample(example('volumes'))
  // Then: cleanup removes that volume and every created sandbox.
  assert.equal(code, 0, output)
  assert.equal(record.createdVolumes.length, 1)
  assert.deepEqual(record.deletedVolumes, record.createdVolumes)
  assertCleanedUp(record)
})

test('volumes_share reports a failed read instead of a partial result', () => {
  const { code, record, output } = runExample('volumes_share.ts', {
    volumeData: true,
    overrides: [['then cat /data/message\\.txt; fi$', 1, 'Input/output error']],
  })
  assert.equal(code, 1, output)
  assert.match(output, /failed \(exit=1\): Input\/output error/)
  assert.equal(record.deletedVolumes.length, 1)
})

test('volumes_share reports a failed write at its source', () => {
  const { code, record, output } = runExample('volumes_share.ts', {
    overrides: [['> /data/message\\.txt', 1, 'read-only file system']],
  })
  assert.equal(code, 1, output)
  assert.match(output, /message\.txt.*failed \(exit=1\): read-only file system/)
  assertCleanedUp(record)
  assert.equal(record.deletedVolumes.length, 1)
})
