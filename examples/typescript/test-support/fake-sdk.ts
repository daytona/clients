/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Preloaded (`node --import tsx --import ./typescript/test-support/fake-sdk.ts`)
 * by examples.test.ts: replaces the root client's `.v2` with an in-memory fake
 * so an example runs offline, end to end, in its own process. The scenario
 * comes from EXAMPLE_FAKE_SCENARIO (JSON); what the example did is written to
 * EXAMPLE_FAKE_RECORD when the process exits.
 */

import { writeFileSync } from 'node:fs'

import { Daytona } from '@daytona/sdk'
import { ImageBuildFailedError } from '@daytona/sdk/v2'

import { RUN } from '../_common'

export interface Scenario {
  /** [regex, exitCode, output]: the first pattern matching a command answers it. */
  overrides?: [string, number, string][]
  interpreterExitCode?: number
  /** The interpreter run raises this exception name (exit code stays 0). */
  interpreterError?: string
  /** followBuildLogs delivers no output. */
  emptyBuildLogs?: boolean
  secretDeleteThrows?: string
  /** followBuildLogs delivers its output, then fails. */
  buildLogsThrowAfterOutput?: boolean
  sandboxDeleteThrows?: string
  failBuildsContaining?: string
  secretSetThrows?: string
  secretIterThrows?: string
  /** Expose an attached secret's real value in the sandbox (a leak). */
  leakSecrets?: boolean
  /** Keep /data/message.txt writes and serve them back (volumes_share). */
  volumeData?: boolean
}

export interface Record {
  sandboxes: string[]
  deletedSandboxes: string[]
  secretSets: { name: string; ifNotExists: boolean }[]
  deletedSecrets: string[]
  deletedVolumes: string[]
  followedImages: string[]
}

const scenario: Scenario = JSON.parse(process.env.EXAMPLE_FAKE_SCENARIO ?? '{}')
const record: Record = {
  sandboxes: [],
  deletedSandboxes: [],
  secretSets: [],
  deletedSecrets: [],
  deletedVolumes: [],
  followedImages: [],
}
process.on('exit', () => {
  if (process.env.EXAMPLE_FAKE_RECORD) writeFileSync(process.env.EXAMPLE_FAKE_RECORD, JSON.stringify(record))
})

const secrets = new Map<string, string>()
const volumeLines: string[] = []

// Every field the SDK's ProcessResult requires, so an example reading any of them sees a real shape.
const result = (exitCode: number, output: string) => ({
  processId: 'p', reason: 'exited' as const, exitCode, output, stdout: output, stderr: '', logs: [],
})

function run(command: string, env: { [name: string]: string }, dockerfile: string) {
  for (const [pattern, exitCode, output] of scenario.overrides ?? []) {
    if (new RegExp(pattern).test(command)) return result(exitCode, output)
  }
  if (scenario.volumeData && command.includes('/data/message.txt')) {
    const write = /echo (\S+) >>? \/data\/message\.txt/.exec(command)
    if (write) {
      volumeLines.push(write[1])
      return result(0, '')
    }
    return result(0, volumeLines.join('\n'))
  }
  const variable = /^printf %s "\$(\w+)"$/.exec(command)
  if (variable) return result(0, env[variable[1]] ?? '')
  if (command === 'cat /marker') {
    const marker = /RUN echo (\S+) > \/marker/.exec(dockerfile)
    return marker ? result(0, marker[1]) : result(1, '')
  }
  if (/git -C \S+ log/.test(command)) return result(0, 'abc1234 add daytona-note')
  return result(0, '')
}

function sandbox(name: string, env: { [name: string]: string }, dockerfile: string) {
  const id = `sbx-${record.sandboxes.length}`
  record.sandboxes.push(id)
  const files = new Map<string, Uint8Array>()
  const box = {
    id,
    name,
    state: 'started',
    cpu: 1,
    memory: 1,
    process: { run: async (command: string) => run(command, env, dockerfile) },
    interpreter: {
      run: async () => ({
        process: { processId: 'p', reason: 'exited' as const, exitCode: scenario.interpreterExitCode ?? 0 },
        logs: { stdout: 'Hello World from code!\n', stderr: '' },
        results: [],
        error: scenario.interpreterError ? { name: scenario.interpreterError, value: '', traceback: [] } : undefined,
      }),
    },
    fs: {
      mkdir: async () => undefined,
      list: async () => ({ items: [] }),
      glob: async () => ({ items: [] }),
      writeBytes: async (path: string, data: Uint8Array) => void files.set(path, data),
      writeText: async (path: string, text: string) => void files.set(path, new TextEncoder().encode(text)),
      readBytes: async (path: string) => files.get(path) ?? new Uint8Array(),
      replace: async (paths: string[], pattern: string, value: string) => {
        for (const path of paths) {
          const text = new TextDecoder().decode(files.get(path))
          files.set(path, new TextEncoder().encode(text.replace(pattern, value)))
        }
        return { items: paths.map((file) => ({ file, success: true })) }
      },
    },
    git: {
      clone: async () => undefined,
      status: async () => ({ currentBranch: 'master', ahead: 0, behind: 0, branchPublished: false, fileStatus: [] }),
      add: async () => undefined,
      commit: async () => ({ hash: 'abc1234' }),
      createBranch: async () => undefined,
      checkoutBranch: async () => undefined,
      listBranches: async () => ({ items: ['master'] }),
      deleteBranch: async () => undefined,
    },
    buildLogs: async () => 'STEP 2: the marker\n',
    delete: async () => {
      if (scenario.sandboxDeleteThrows) throw new Error(scenario.sandboxDeleteThrows)
      record.deletedSandboxes.push(id)
    },
  }
  return box
}

type Box = ReturnType<typeof sandbox>
const boxes: Box[] = []

const fake = {
  create: async (image: string, options: { name?: string; env?: { [k: string]: string }; envVars?: { [k: string]: string }; secrets?: string[] } = {}) => {
    const env = { ...options.env, ...options.envVars }
    for (const name of options.secrets ?? []) {
      // The API rejects a create that attaches a secret the project lacks.
      if (!secrets.has(name)) throw new Error(`secret ${name} not found`)
      env[name] = scenario.leakSecrets ? (secrets.get(name) ?? '') : 'DAYTONA_SECRET_PLACEHOLDER'
    }
    const box = sandbox(options.name ?? image, env, '')
    boxes.push(box)
    return box
  },
  createFromBuild: async (builder: { toDockerfile(): string }, options: { name?: string } = {}) => {
    const dockerfile = builder.toDockerfile()
    if (scenario.failBuildsContaining && dockerfile.includes(scenario.failBuildsContaining)) {
      throw new ImageBuildFailedError('image build failed', { statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: 'img_failed' })
    }
    const box = sandbox(options.name ?? 'built', {}, dockerfile)
    boxes.push(box)
    return box
  },
  delete: async (box: Box) => box.delete(),
  get: async (id: string) => boxes.find((box) => box.id === id),
  iter: async function* () {
    yield* boxes
  },
  images: {
    list: async () => ({ items: [], nextCursor: undefined }),
    followBuildLogs: async (imageId: string, options: { onLogs: (chunk: string) => void }) => {
      record.followedImages.push(imageId)
      if (!scenario.emptyBuildLogs) options.onLogs(`intentional-build-failure-${RUN}\n`)
      if (scenario.buildLogsThrowAfterOutput) throw new Error('stream interrupted')
      return {}
    },
  },
  secret: {
    set: async (name: string, value: string, options: { ifNotExists?: boolean }) => {
      record.secretSets.push({ name, ifNotExists: options.ifNotExists === true })
      if (scenario.secretSetThrows) throw new Error(scenario.secretSetThrows)
      if (options.ifNotExists && secrets.has(name)) throw new Error('secret already exists')
      secrets.set(name, value)
      return { name }
    },
    iter: async function* () {
      if (scenario.secretIterThrows) throw new Error(scenario.secretIterThrows)
      for (const name of secrets.keys()) yield { name }
    },
    list: async () => ({ items: [...secrets.keys()].map((name) => ({ name })) }),
    delete: async (name: string) => {
      if (scenario.secretDeleteThrows) throw new Error(scenario.secretDeleteThrows)
      secrets.delete(name)
      record.deletedSecrets.push(name)
    },
  },
  volume: {
    create: async (name: string) => ({ id: `vol-${name}`, name, status: 'ready' }),
    delete: async (volume: { id: string }) => void record.deletedVolumes.push(volume.id),
  },
}

Object.defineProperty(Daytona.prototype, 'v2', { get: () => fake, configurable: true })
