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
import { ConflictError, ImageBuildFailedError, NameConflictError, NotFoundError, RequestTimeoutError } from '@daytona/sdk/v2'
import type {
  BuildLogFollowOptions,
  CreateFromBuildOptions,
  CreateSandboxOptions,
  CreateVolumeOptions,
  DeleteVolumeOptions,
  GetVolumeOptions,
  SetSecretOptions,
} from '@daytona/sdk/v2'

import { RUN } from '../_common'

export interface Scenario {
  readonly sandboxStates?: readonly string[]
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
  /** Project images that exist before the run: [name, status, statuses its waits return (default ['ready'])]. */
  images?: [string, string, string[]?][]
  /** Every registration fails during its wait, with this error. */
  pullFails?: string
  /** Another run registers the image first: pull finds the name taken (409). */
  pullConflict?: boolean
}

export interface Record {
  readonly commands: { readonly sandboxId: string; readonly command: string }[]
  sandboxes: string[]
  deletedSandboxes: string[]
  secretSets: { name: string; ifNotExists: boolean }[]
  deletedSecrets: string[]
  createdVolumes: string[]
  deletedVolumes: string[]
  followedImages: string[]
  builtDockerfiles: string[]
  pulledImages: { ref: string; name?: string }[]
  deletedImages: string[]
  imageWaits: { wait?: string; timeoutSeconds?: number }[]
  mounts: { sandboxId: string; mounts: Mount[] }[]
}

const scenario: Scenario = JSON.parse(process.env.EXAMPLE_FAKE_SCENARIO ?? '{}')
const record: Record = {
  commands: [],
  sandboxes: [],
  deletedSandboxes: [],
  secretSets: [],
  deletedSecrets: [],
  createdVolumes: [],
  deletedVolumes: [],
  followedImages: [],
  builtDockerfiles: [],
  pulledImages: [],
  deletedImages: [],
  imageWaits: [],
  mounts: [],
}
process.on('exit', () => {
  if (process.env.EXAMPLE_FAKE_RECORD) writeFileSync(process.env.EXAMPLE_FAKE_RECORD, JSON.stringify(record))
})

const secrets = new Map<string, string>()
type FakeVolume = { readonly id: string; readonly name: string; readonly status: 'ready' }
const volumes = new Map<string, FakeVolume>()
let nextImage = 0
const newImage = (name: string, status: string, waits: string[] = ['ready'], error?: string) => ({ id: `img-${nextImage++}`, name, status, waits, error })
const images = (scenario.images ?? []).map(([name, status, waits]) => newImage(name, status, waits))
// Each volume's files (path inside the volume → bytes); a sandbox reads and
// writes a path, through its commands and its `fs` alike, in the volume
// mounted over it, else on its own disk.
type FileStore = Map<string, Uint8Array>
const volumeFiles = new Map<string, FileStore>()
type Mount = { readonly volumeId: string; readonly mountPath: string; readonly subpath?: string; readonly readOnly?: boolean }

function mountsOf(requested: CreateSandboxOptions['volumes']): Mount[] {
  return (requested ?? []).map(({ volumeId, mountPath, subpath, readOnly }) => {
    // The API rejects a mount of a volume the project lacks.
    if (!volumes.has(volumeId)) throw new Error(`volume ${volumeId} not found`)
    return {
      volumeId,
      mountPath: mountPath.replace(/\/+$/, ''),
      ...(subpath === undefined ? {} : { subpath }),
      ...(readOnly === undefined ? {} : { readOnly }),
    }
  })
}

/**
 * Where a path lives: under a mount, in that volume below its subpath (and
 * read-only when the mount is); else on the sandbox's own disk.
 */
function fileStore(path: string, mounts: readonly Mount[], disk: FileStore): { store: FileStore; key: string; readOnly: boolean } {
  const mount = mounts.find(({ mountPath }) => path === mountPath || path.startsWith(`${mountPath}/`))
  if (mount === undefined) return { store: disk, key: path, readOnly: false }
  const store = volumeFiles.get(mount.volumeId) ?? new Map<string, Uint8Array>()
  volumeFiles.set(mount.volumeId, store)
  const below = mount.subpath === undefined ? '' : `/${mount.subpath.replace(/^\/+|\/+$/g, '')}`
  return { store, key: `${below}${path.slice(mount.mountPath.length)}`, readOnly: mount.readOnly === true }
}

// Every field the SDK's ProcessResult requires, so an example reading any of them sees a real shape:
// non-empty output is one stdout log frame, as the sandbox reports it.
const result = (exitCode: number, output: string) => ({
  processId: 'p', reason: 'exited' as const, exitCode, output, stdout: output, stderr: '',
  logs: output === '' ? [] : [{ cursor: '1', channel: 'stdout', data: output, timestampMs: 0 }],
})

const encode = (text: string): Uint8Array => new TextEncoder().encode(text)
const decode = (bytes: Uint8Array | undefined): string => new TextDecoder().decode(bytes ?? new Uint8Array())
const readOnlyFailure = (path: string) => `sh: can't create ${path}: Read-only file system`

function run(command: string, env: { [name: string]: string }, dockerfile: string, mounts: readonly Mount[], disk: FileStore, name: string) {
  for (const [pattern, exitCode, output] of scenario.overrides ?? []) {
    if (new RegExp(pattern).test(command)) return result(exitCode, output)
  }
  if (scenario.volumeData && command.includes('/data/message.txt')) {
    const { store, key, readOnly } = fileStore('/data/message.txt', mounts, disk)
    const write = /echo (\S+) (>>?) \/data\/message\.txt/.exec(command)
    if (write && readOnly) return result(1, readOnlyFailure('/data/message.txt'))
    if (write) {
      store.set(key, encode(`${write[2] === '>>' ? decode(store.get(key)) : ''}${write[1]}\n`))
      return result(0, '')
    }
    const bytes = store.get(key)
    return result(0, bytes === undefined ? (command.includes("echo '<missing>'") ? '<missing>' : '') : decode(bytes).replace(/\n$/, ''))
  }
  const variable = /^printf %s "\$(\w+)"$/.exec(command)
  if (variable) return result(0, env[variable[1]] ?? '')
  // local-files copies files the fake does not read: it answers from the
  // run marker in the sandbox name, and the ignore file kept debug.log out.
  const localFiles = /^local-files-(\S+)$/.exec(name)
  if (localFiles && command === './main.sh && cat /etc/example/settings.conf') return result(0, `local-files-${localFiles[1]}\nrun=${localFiles[1]}`)
  if (localFiles && command === 'ls -a /app') return result(0, '.\n..\nmain.sh')
  if (command === 'cat /marker') {
    const marker = /RUN echo (\S+) > \/marker/.exec(dockerfile)
    return marker ? result(0, marker[1]) : result(1, '')
  }
  if (/git -C \S+ log/.test(command)) return result(0, 'abc1234 add daytona-note')
  return result(0, '')
}

/** The SDK refuses a relative sandbox path; the fake does too. */
function absolute(path: string): string {
  if (!path.startsWith('/')) throw new Error(`path must be an absolute path (starting with '/'): ${path}`)
  return path
}

function sandbox(name: string, env: { [name: string]: string }, dockerfile: string, mounts: readonly Mount[] = []) {
  const id = `sbx-${record.sandboxes.length}`
  record.sandboxes.push(id)
  record.mounts.push({ sandboxId: id, mounts: [...mounts] })
  const disk: FileStore = new Map()
  // The `fs` calls go through the same mount-aware store as the commands.
  const at = (path: string) => fileStore(absolute(path), mounts, disk)
  const write = (path: string, data: Uint8Array): void => {
    const { store, key, readOnly } = at(path)
    if (readOnly) throw new Error(readOnlyFailure(path))
    store.set(key, data)
  }
  const box = {
    id,
    name,
    state: scenario.sandboxStates?.[record.sandboxes.length - 1] ?? 'started',
    cpu: 1,
    memory: 1,
    process: { run: async (command: string) => {
      record.commands.push({ sandboxId: id, command })
      return run(command, env, dockerfile, mounts, disk, name)
    } },
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
      list: async (dir: string) => {
        // The entries directly below `dir`, from the store that holds `dir`;
        // a relative path is refused, as the SDK refuses it.
        const trimmed = absolute(dir).replace(/\/+$/, '')
        const { store, key } = at(trimmed === '' ? '/' : trimmed)
        const prefix = trimmed === '' ? '/' : `${key}/`
        const names = [...store.keys()]
          .filter((path) => path.startsWith(prefix))
          .map((path) => path.slice(prefix.length).split('/')[0])
        return { items: [...new Set(names)].map((name) => ({ name })) }
      },
      glob: async () => ({ items: [] }),
      writeBytes: async (path: string, data: Uint8Array) => write(path, data),
      writeText: async (path: string, text: string) => write(path, encode(text)),
      readBytes: async (path: string) => { const { store, key } = at(path); return store.get(key) ?? new Uint8Array() },
      readText: async (path: string) => { const { store, key } = at(path); return decode(store.get(key)) },
      replace: async (paths: string[], pattern: string, value: string) => {
        for (const path of paths) {
          const { store, key } = at(path)
          write(path, encode(decode(store.get(key)).replace(pattern, value)))
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
  create: async (image: string, options: CreateSandboxOptions = {}) => {
    // The API creates only from a project image, never a registry reference.
    if (!images.some((entry) => (entry.name === image || entry.id === image) && entry.status === 'ready')) throw new Error(`image not found: ${image}`)
    const env = { ...options.env }
    for (const name of options.secrets ?? []) {
      // The API rejects a create that attaches a secret the project lacks.
      if (!secrets.has(name)) throw new Error(`secret ${name} not found`)
      env[name] = scenario.leakSecrets ? (secrets.get(name) ?? '') : 'DAYTONA_SECRET_PLACEHOLDER'
    }
    const box = sandbox(options.name ?? image, env, '', mountsOf(options.volumes))
    boxes.push(box)
    return box
  },
  createFromBuild: async (builder: { toDockerfile(): string }, options: CreateFromBuildOptions = {}) => {
    const dockerfile = builder.toDockerfile()
    record.builtDockerfiles.push(dockerfile)
    if (scenario.failBuildsContaining && dockerfile.includes(scenario.failBuildsContaining)) {
      throw new ImageBuildFailedError('image build failed', {
        statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK', imageId: 'img_failed',
      })
    }
    const env = { ...options.env }
    for (const name of options.secrets ?? []) {
      if (!secrets.has(name)) throw new Error(`secret ${name} not found`)
      env[name] = scenario.leakSecrets ? (secrets.get(name) ?? '') : 'DAYTONA_SECRET_PLACEHOLDER'
    }
    const box = sandbox(options.name ?? 'built', env, dockerfile, mountsOf(options.volumes))
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
    iter: async function* (options: { name?: string } = {}) {
      yield* images.filter((entry) => options.name === undefined || entry.name === options.name)
    },
    get: async (id: string, options: { wait?: string; timeoutSeconds?: number } = {}) => {
      // Without wait: 'ready', the image as it is now. With it, as the SDK's
      // budgeted wait: it follows the image until it settles, or throws
      // RequestTimeoutError when it never does.
      record.imageWaits.push({ wait: options.wait, timeoutSeconds: options.timeoutSeconds })
      const entry = images.find((candidate) => candidate.id === id)
      if (entry === undefined || options.wait !== 'ready') return entry
      const settled = (): boolean => ['ready', 'failed', 'error'].includes(entry.status)
      while (!settled() && entry.waits.length > 0) entry.status = entry.waits.shift() ?? entry.status
      if (!settled()) throw new RequestTimeoutError('image did not settle', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
      return entry
    },
    pull: async (ref: string, options: { name?: string } = {}) => {
      record.pulledImages.push({ ref, name: options.name })
      if (scenario.pullConflict && record.pulledImages.length === 1) {
        images.push(newImage(options.name ?? ref, 'ready'))
        throw new NameConflictError('image name already exists', { statusCode: 409, code: 'NAME_CONFLICT', source: 'API' })
      }
      const entry = scenario.pullFails ? newImage(options.name ?? ref, 'pending', ['failed'], scenario.pullFails) : newImage(options.name ?? ref, 'ready')
      images.push(entry)
      return entry
    },
    delete: async (id: string) => {
      record.deletedImages.push(id)
      images.splice(images.findIndex((entry) => entry.id === id), 1)
    },
    followBuildLogs: async (imageId: string, options: BuildLogFollowOptions) => {
      record.followedImages.push(imageId)
      if (!scenario.emptyBuildLogs) await options.onLogs(`intentional-build-failure-${RUN}\n`)
      if (scenario.buildLogsThrowAfterOutput) throw new Error('stream interrupted')
      return {}
    },
  },
  secrets: {
    set: async (name: string, value: string, options: SetSecretOptions) => {
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
  volumes: {
    create: async ({ name }: CreateVolumeOptions): Promise<FakeVolume> => {
      const volume = { id: `vol-${name}`, name, status: 'ready' } as const
      volumes.set(volume.id, volume)
      record.createdVolumes.push(volume.id)
      return volume
    },
    get: async (nameOrId: string, options: GetVolumeOptions = {}): Promise<FakeVolume> => {
      const volume = volumes.get(nameOrId) ?? (!options.idOnly
        ? [...volumes.values()].find((entry) => entry.name === nameOrId) : undefined)
      if (volume) return volume
      if (options.create && !options.idOnly) return fake.volumes.create({ name: nameOrId })
      // As the SDK: an absent volume is NotFoundError unless create is set.
      throw new NotFoundError(`volume ${nameOrId} not found`, { statusCode: 404, code: 'NOT_FOUND', source: 'API' })
    },
    delete: async (nameOrId: string, options: DeleteVolumeOptions = {}): Promise<void> => {
      const volume = await fake.volumes.get(nameOrId, options)
      // As the API: a volume a sandbox still mounts is a conflict.
      const inUse = record.mounts.some(({ sandboxId, mounts }) => !record.deletedSandboxes.includes(sandboxId) && mounts.some((mount) => mount.volumeId === volume.id))
      if (inUse) throw new ConflictError(`volume ${volume.id} is in use`, { statusCode: 409, code: 'CONFLICT', source: 'API' })
      volumes.delete(volume.id)
      // Deleting a volume deletes its files too.
      volumeFiles.delete(volume.id)
      record.deletedVolumes.push(volume.id)
    },
  },
}

Object.defineProperty(Daytona.prototype, 'v2', { get: () => fake, configurable: true })
