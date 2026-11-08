/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from './http.js'
import { BadRequestError, DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import type { GitCommitResponse, GitConfigScope, GitRemote, GitStatus, ListResult } from './types.js'
import type * as toolbox from './gen/toolbox-types.js'

export type ListBranchResponse = toolbox.BranchList

/** One commit returned by `git.history()`. */
export type GitCommit = toolbox.CommitInfo

export interface CloneOptions {
  path: string
  branch?: string
  commitId?: string
  depth?: number
  insecureSkipTls?: boolean
  username?: string
  password?: string
  timeout?: number
}

export interface CommitOptions {
  author: string
  email: string
  allowEmpty?: boolean
}

export interface InitOptions {
  bare?: boolean
  initialBranch?: string
}

export interface CredentialOptions {
  username?: string
  password?: string
  branch?: string
  remote?: string
  timeout?: number
}

export interface PushOptions extends CredentialOptions {
  setUpstream?: boolean
}

const GIT_REMOTE_TIMEOUT_MS = 30 * 60 * 1000

export interface GitConfigOptions {
  path?: string
  scope?: GitConfigScope
}

export interface ResetOptions {
  mode?: string
  target?: string
  files?: string[]
}

export interface RestoreOptions {
  staged?: boolean
  worktree?: boolean
  source?: string
}

export class GitService {
  constructor(private readonly transport: HttpTransport) {}

  public async clone(url: string, options: CloneOptions): Promise<void> {
    const depth = validateCloneDepth(options.depth)
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
    await this.transport.post<void>('/git/clone', body, timeoutConfig(options.timeout))
  }

  public async init(path: string, options: InitOptions = {}): Promise<void> {
    await this.transport.post<void>('/git/init', {
      path,
      bare: options.bare ?? false,
      initial_branch: options.initialBranch,
    })
  }

  public async status(path: string): Promise<GitStatus> {
    return this.transport.get<toolbox.GitStatus>('/git/status', { params: { path } })
  }

  public async add(path: string, files: string[]): Promise<void> {
    const body: toolbox.AddRequest = { path, files }
    await this.transport.post<void>('/git/add', body)
  }

  public async reset(path: string, options: ResetOptions = {}): Promise<void> {
    await this.transport.post<void>('/git/reset', {
      path,
      mode: options.mode,
      target: options.target,
      files: options.files,
    })
  }

  public async restore(path: string, files: string[], options: RestoreOptions = {}): Promise<void> {
    await this.transport.post<void>('/git/restore', {
      path,
      files,
      staged: options.staged,
      worktree: options.worktree,
      source: options.source,
    })
  }

  public async listRemotes(path: string): Promise<ListResult<GitRemote>> {
    const data = await this.transport.get<{ remotes: GitRemote[] } | ListResult<GitRemote>>('/git/remotes', { params: { path } })
    return 'items' in data ? data : { items: data.remotes }
  }

  public async addRemote(path: string, name: string, url: string): Promise<void> {
    await this.transport.post<void>('/git/remotes', { path, name, url })
  }

  public async commit(path: string, message: string, options: CommitOptions): Promise<GitCommitResponse> {
    const author = options?.author?.trim() ?? ''
    const email = options?.email?.trim() ?? ''
    if (!author || !email) throw createDaytonaError('git.commit requires author and email', 400, 'BAD_REQUEST', 'SDK')
    const body: toolbox.CommitRequest = {
      path,
      message,
      author,
      email,
      allow_empty: options.allowEmpty === true ? true : undefined,
    }
    return this.transport.post<toolbox.CommitResponse>('/git/commit', body)
  }

  public async pull(path: string, options: CredentialOptions = {}): Promise<void> {
    const body: toolbox.RepoRequest = {
      path,
      username: options.username,
      password: options.password,
      branch: options.branch,
      remote: options.remote,
    }
    await this.transport.post<void>('/git/pull', body, timeoutConfig(options.timeout))
  }

  public async push(path: string, options: PushOptions = {}): Promise<void> {
    const body: toolbox.RepoRequest = {
      path,
      username: options.username,
      password: options.password,
      branch: options.branch,
      remote: options.remote,
      setUpstream: options.setUpstream === true ? true : undefined,
    }
    await this.transport.post<void>('/git/push', body, timeoutConfig(options.timeout))
  }

  public async getConfig(key: string, options: GitConfigOptions = {}): Promise<string | null> {
    const data = await this.transport.get<{ value?: string | null }>('/git/config', {
      params: { key, path: options.path, scope: options.scope ?? 'global' },
    })
    return typeof data.value === 'string' ? data.value : null
  }

  public async setConfig(key: string, value: string, options: GitConfigOptions = {}): Promise<void> {
    await this.transport.post<void>('/git/config', {
      key,
      value,
      path: options.path,
      scope: options.scope ?? 'global',
    })
  }

  /** List branches; response always has `{items,nextCursor}`. */
  public async listBranches(path: string): Promise<ListResult<string>> {
    const data = await this.transport.get<toolbox.BranchList | string[] | ListResult<string>>('/git/branches', { params: { path } })
    if (Array.isArray(data)) return { items: data }
    if ('items' in data) return data
    return { items: data.branches }
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

  public async history(path: string): Promise<ListResult<GitCommit>> {
    const data = await this.transport.get<toolbox.CommitInfo[] | ListResult<toolbox.CommitInfo>>('/git/history', { params: { path } })
    return Array.isArray(data) ? { items: data } : data
  }
}

function validateCloneDepth(depth: number | undefined): number | undefined {
  if (depth === undefined) return undefined
  if (!Number.isInteger(depth) || depth <= 0) {
    throw new BadRequestError('depth must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return depth
}

function timeoutConfig(timeout: number | undefined): { readonly timeout: number } {
  if (timeout === undefined) return { timeout: GIT_REMOTE_TIMEOUT_MS }
  if (!Number.isFinite(timeout) || timeout <= 0) {
    throw new BadRequestError('timeout must be a positive finite number', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return { timeout }
}
