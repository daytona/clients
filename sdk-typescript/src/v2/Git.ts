/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from './http.js'
import { BadRequestError } from './errors/DaytonaError.js'
import type { GitCommit, GitCommitResponse, GitConfigScope, GitRemote, GitStatus, ListResult } from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, optionKeys } from './options.js'
import { gitCommitFromWire, gitCommitResponseFromWire, gitRemoteFromWire, gitStatusFromWire, envelopeList, listItems, malformedResponse, record, strList } from './toolboxModels.js'
import { MAX_TIMER_DELAY_MS } from './options.js'
import { deepFrozen } from './frozen.js'
import { pathSequence } from './filesystemInputs.js'

export interface CloneOptions {
  /** Where to clone to, inside the sandbox. */
  readonly path: string
  readonly branch?: string
  readonly commitId?: string
  /** Shallow clone with this many commits. */
  readonly depth?: number
  readonly insecureSkipTls?: boolean
  readonly username?: string
  readonly password?: string
  /** Request budget in seconds; default 1800. */
  readonly timeoutSeconds?: number
}

export interface CommitOptions {
  readonly author: string
  readonly email: string
  readonly allowEmpty?: boolean
}

export interface InitOptions {
  readonly bare?: boolean
  readonly initialBranch?: string
}

export interface CredentialOptions {
  readonly username?: string
  readonly password?: string
  readonly branch?: string
  readonly remote?: string
  /** Request budget in seconds; default 1800. */
  readonly timeoutSeconds?: number
}

export interface PushOptions extends CredentialOptions {
  readonly setUpstream?: boolean
}

const GIT_REMOTE_TIMEOUT_SECONDS = 30 * 60

export interface GitConfigOptions {
  readonly path?: string
  /** Default `'global'`. */
  readonly scope?: GitConfigScope
}

export interface ResetOptions {
  readonly mode?: string
  readonly target?: string
  readonly files?: readonly string[]
}

export interface RestoreOptions {
  readonly staged?: boolean
  readonly worktree?: boolean
  readonly source?: string
}

const CLONE_OPTION_KEYS = optionKeys<CloneOptions>()({
  path: true, branch: true, commitId: true, depth: true, insecureSkipTls: true, username: true, password: true, timeoutSeconds: true,
})
const COMMIT_OPTION_KEYS = optionKeys<CommitOptions>()({ author: true, email: true, allowEmpty: true })
const INIT_OPTION_KEYS = optionKeys<InitOptions>()({ bare: true, initialBranch: true })
const CREDENTIAL_OPTION_KEYS = optionKeys<CredentialOptions>()({ username: true, password: true, branch: true, remote: true, timeoutSeconds: true })
const PUSH_OPTION_KEYS = optionKeys<PushOptions>()({
  username: true, password: true, branch: true, remote: true, timeoutSeconds: true, setUpstream: true,
})
const GIT_CONFIG_OPTION_KEYS = optionKeys<GitConfigOptions>()({ path: true, scope: true })
const RESET_OPTION_KEYS = optionKeys<ResetOptions>()({ mode: true, target: true, files: true })
const RESTORE_OPTION_KEYS = optionKeys<RestoreOptions>()({ staged: true, worktree: true, source: true })

/** Sandbox git namespace (`sandbox.git`). */
export class GitService {
  private readonly transport: HttpTransport

  /** @internal */
  constructor(token: Internal, transport: HttpTransport)
  /** Built by the SDK: get it from `sandbox.git`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: HttpTransport) {
    assertInternal(token, 'GitService')
    this.transport = transport
  }

  /** Clone a repository, optionally shallow and with per-call TLS relaxation. */
  public async clone(url: string, options: CloneOptions): Promise<void> {
    assertKnownOptions('CloneOptions', options, CLONE_OPTION_KEYS)
    const depth = validateCloneDepth(options.depth)
    if (options.insecureSkipTls !== undefined && typeof options.insecureSkipTls !== 'boolean') {
      throw new BadRequestError('insecureSkipTls must be a boolean', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    const body: toolbox.CloneRequest = {
      url,
      path: options.path,
      branch: options.branch,
      commit_id: options.commitId,
      depth,
      insecureSkipTls: options.insecureSkipTls === true ? true : undefined,
      username: options.username,
      password: options.password,
    }
    await this.transport.post<void>('/git/clone', body, timeoutConfig(options.timeoutSeconds))
  }

  public async init(path: string, options: InitOptions = {}): Promise<void> {
    assertKnownOptions('InitOptions', options, INIT_OPTION_KEYS)
    await this.transport.post<void>('/git/init', {
      path,
      bare: options.bare ?? false,
      initial_branch: options.initialBranch,
    })
  }

  public async status(path: string): Promise<GitStatus> {
    return gitStatusFromWire(await this.transport.get<toolbox.GitStatus>('/git/status', { params: { path } }))
  }

  /** Stage an array of string pathspecs (not a bare string). */
  public async add(path: string, files: readonly string[]): Promise<void> {
    const body: toolbox.AddRequest = { path, files: pathSequence('files', files) }
    await this.transport.post<void>('/git/add', body)
  }

  public async reset(path: string, options: ResetOptions = {}): Promise<void> {
    assertKnownOptions('ResetOptions', options, RESET_OPTION_KEYS)
    await this.transport.post<void>('/git/reset', {
      path,
      mode: options.mode,
      target: options.target,
      files: options.files === undefined ? undefined : pathSequence('files', options.files),
    })
  }

  public async restore(path: string, files: readonly string[], options: RestoreOptions = {}): Promise<void> {
    assertKnownOptions('RestoreOptions', options, RESTORE_OPTION_KEYS)
    await this.transport.post<void>('/git/restore', {
      path,
      files: pathSequence('files', files),
      staged: options.staged,
      worktree: options.worktree,
      source: options.source,
    })
  }

  /** List the configured remotes; URLs come back with any credentials removed. */
  public async listRemotes(path: string): Promise<ListResult<GitRemote>> {
    const data = await this.transport.get<toolbox.ListRemotesResponse>('/git/remotes', { params: { path } })
    const d = 'git remotes response'
    const remotes = record(data, d).remotes
    if (Array.isArray(remotes)) {
      remotes.forEach((remote: unknown, index) => {
        if (typeof remote !== 'object' || remote === null || Array.isArray(remote)) throw malformedResponse(`${d}: remotes[${index}] must be an object`)
      })
    }
    return deepFrozen({ items: envelopeList(data, 'remotes', d, gitRemoteFromWire) })
  }

  public async addRemote(path: string, name: string, url: string): Promise<void> {
    await this.transport.post<void>('/git/remotes', { path, name, url })
  }

  /** Commit staged changes, optionally allowing an empty commit. */
  public async commit(path: string, message: string, options: CommitOptions): Promise<GitCommitResponse> {
    assertKnownOptions('CommitOptions', options, COMMIT_OPTION_KEYS)
    const author = options?.author?.trim() ?? ''
    const email = options?.email?.trim() ?? ''
    if (!author || !email) throw new BadRequestError('git.commit requires author and email', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    const body: toolbox.CommitRequest = {
      path,
      message,
      author,
      email,
      allow_empty: options.allowEmpty === true ? true : undefined,
    }
    return gitCommitResponseFromWire(await this.transport.post<toolbox.CommitResponse>('/git/commit', body))
  }

  /** Pull from the configured or named remote. */
  public async pull(path: string, options: CredentialOptions = {}): Promise<void> {
    assertKnownOptions('CredentialOptions', options, CREDENTIAL_OPTION_KEYS)
    const body: toolbox.RepoRequest = {
      path,
      username: options.username,
      password: options.password,
      branch: options.branch,
      remote: options.remote,
    }
    await this.transport.post<void>('/git/pull', body, timeoutConfig(options.timeoutSeconds))
  }

  /** Push to the configured or named remote. */
  public async push(path: string, options: PushOptions = {}): Promise<void> {
    assertKnownOptions('PushOptions', options, PUSH_OPTION_KEYS)
    const body: toolbox.RepoRequest = {
      path,
      username: options.username,
      password: options.password,
      branch: options.branch,
      remote: options.remote,
      setUpstream: options.setUpstream === true ? true : undefined,
    }
    await this.transport.post<void>('/git/push', body, timeoutConfig(options.timeoutSeconds))
  }

  /** The config value, or undefined when the key is not set. */
  public async getConfig(key: string, options: GitConfigOptions = {}): Promise<string | undefined> {
    assertKnownOptions('GitConfigOptions', options, GIT_CONFIG_OPTION_KEYS)
    const data = await this.transport.get<unknown>('/git/config', {
      params: { key, path: options.path, scope: configScope(options.scope) },
    })
    const value = record(data, 'git config response').value
    if (value === undefined || value === null) return undefined
    if (typeof value !== 'string') throw malformedResponse('git config response: value must be a string or null')
    return value
  }

  /**
   * Set a git config value. A sandbox access token needs `toolbox:exec` as
   * well as `toolbox:git` for this (`toolbox` covers both): without
   * `toolbox:exec` it throws `ForbiddenError` (403), while {@link getConfig}
   * still works.
   */
  public async setConfig(key: string, value: string, options: GitConfigOptions = {}): Promise<void> {
    assertKnownOptions('GitConfigOptions', options, GIT_CONFIG_OPTION_KEYS)
    await this.transport.post<void>('/git/config', {
      key,
      value,
      path: options.path,
      scope: configScope(options.scope),
    })
  }

  /** List branch names: one page of `{ items }`. */
  public async listBranches(path: string): Promise<ListResult<string>> {
    const data = await this.transport.get<toolbox.BranchList>('/git/branches', { params: { path } })
    return deepFrozen({ items: strList(record(data, 'git branches response'), 'branches', 'git branches response') })
  }

  public async createBranch(path: string, name: string): Promise<void> {
    const body: toolbox.BranchRequest = { path, name }
    await this.transport.post<void>('/git/branches', body)
  }

  public async deleteBranch(path: string, name: string): Promise<void> {
    await this.transport.delete<void>('/git/branches', { data: { path, name } })
  }

  public async checkoutBranch(path: string, branch: string): Promise<void> {
    const body: toolbox.CheckoutRequest = { path, branch }
    await this.transport.post<void>('/git/checkout', body)
  }

  /** List commits, newest first. */
  public async history(path: string): Promise<ListResult<GitCommit>> {
    const data = await this.transport.get<toolbox.CommitInfo[] | ListResult<toolbox.CommitInfo>>('/git/history', { params: { path } })
    return listItems(data, 'items', 'git history response', gitCommitFromWire)
  }
}

function configScope(scope: GitConfigScope | undefined): GitConfigScope {
  if (scope === undefined) return 'global'
  if (scope !== 'global' && scope !== 'local') {
    throw new BadRequestError("scope must be 'global' or 'local'", { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return scope
}

function validateCloneDepth(depth: number | undefined): number | undefined {
  if (depth === undefined) return undefined
  if (!Number.isInteger(depth) || depth <= 0) {
    throw new BadRequestError('depth must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return depth
}

/** The request budget of a remote git operation, in axios milliseconds. */
function timeoutConfig(timeoutSeconds: number | undefined): { readonly timeout: number } {
  if (timeoutSeconds === undefined) return { timeout: GIT_REMOTE_TIMEOUT_SECONDS * 1000 }
  if (typeof timeoutSeconds !== 'number' || !Number.isFinite(timeoutSeconds) || timeoutSeconds < 0) {
    throw new BadRequestError('timeoutSeconds must be a non-negative finite number of seconds', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  // The toolbox takes no timeout on these routes, so the deadline is the
  // SDK's own: 0, or a value above the longest timer, is no limit, as for
  // every 2.x timeout.
  const timeoutMs = Math.ceil(timeoutSeconds * 1000)
  return { timeout: timeoutMs > MAX_TIMER_DELAY_MS ? 0 : timeoutMs }
}
