/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Wire → SDK mappers for the toolbox results that have an SDK-owned model.
// The wire types type the HTTP boundary only; a payload missing a field the
// contract requires is a malformed response (BadRequestError, source SDK,
// like the Python SDK's model boundary), never a placeholder value.

import type * as toolbox from './gen/toolbox-types.js'
import type {
  FileInfo, FileMatch, GitCommit, GitCommitResponse, GitFileStatus, GitRemote, GitStatus, ReplaceResult, SystemMetrics,
} from './types.js'
import { BadRequestError } from './errors/DaytonaError.js'

type WireRecord = Readonly<Record<string, unknown>>

function malformed(description: string, field: string): BadRequestError {
  return new BadRequestError(`expected ${description}: ${field} is required`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function record(value: unknown, description: string): WireRecord {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new BadRequestError(`expected ${description}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value as WireRecord
}

function str(input: WireRecord, field: string, description: string): string {
  const value = input[field]
  if (typeof value !== 'string') throw malformed(description, field)
  return value
}

function num(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw malformed(description, field)
  return value
}

function bool(input: WireRecord, field: string, description: string): boolean {
  const value = input[field]
  if (typeof value !== 'boolean') throw malformed(description, field)
  return value
}

export function systemMetricsFromWire(value: toolbox.Metrics): SystemMetrics {
  const input = record(value, 'system metrics')
  const d = 'system metrics'
  return {
    timestamp: str(input, 'timestamp', d),
    cpuCount: num(input, 'cpuCount', d),
    cpuUtilization: num(input, 'cpuUtilization', d),
    memoryUsageBytes: num(input, 'memoryUsageBytes', d),
    memoryTotalBytes: num(input, 'memoryTotalBytes', d),
    diskUsageBytes: num(input, 'diskUsageBytes', d),
    diskTotalBytes: num(input, 'diskTotalBytes', d),
  }
}

export function fileInfoFromWire(value: toolbox.FileInfo): FileInfo {
  const d = 'file info response'
  const input = record(value, d)
  return {
    name: str(input, 'name', d),
    path: str(input, 'path', d),
    size: num(input, 'size', d),
    isDir: bool(input, 'isDir', d),
    mode: str(input, 'mode', d),
    permissions: str(input, 'permissions', d),
    owner: str(input, 'owner', d),
    group: str(input, 'group', d),
    modifiedAt: str(input, 'modifiedAt', d),
    modTime: str(input, 'modTime', d),
  }
}

export function fileMatchFromWire(value: toolbox.Match): FileMatch {
  const d = 'grep match'
  const input = record(value, d)
  return { file: str(input, 'file', d), line: num(input, 'line', d), content: str(input, 'content', d) }
}

export function replaceResultFromWire(value: toolbox.ReplaceResult): ReplaceResult {
  const d = 'replace result'
  const input = record(value, d)
  const error = input.error
  if (error !== undefined && error !== null && typeof error !== 'string') throw malformed(d, 'error')
  return {
    file: str(input, 'file', d),
    success: bool(input, 'success', d),
    ...(typeof error === 'string' ? { error } : {}),
  }
}

function gitFileStatusFromWire(value: unknown): GitFileStatus {
  const d = 'git status file entry'
  const input = record(value, d)
  return {
    name: str(input, 'name', d),
    staging: str(input, 'staging', d),
    worktree: str(input, 'worktree', d),
    extra: str(input, 'extra', d),
  }
}

export function gitStatusFromWire(value: toolbox.GitStatus): GitStatus {
  const d = 'git status response'
  const input = record(value, d)
  const fileStatus = input.fileStatus
  if (!Array.isArray(fileStatus)) throw malformed(d, 'fileStatus')
  return {
    currentBranch: str(input, 'currentBranch', d),
    ahead: num(input, 'ahead', d),
    behind: num(input, 'behind', d),
    branchPublished: bool(input, 'branchPublished', d),
    fileStatus: fileStatus.map(gitFileStatusFromWire),
  }
}

export function gitCommitResponseFromWire(value: toolbox.CommitResponse): GitCommitResponse {
  return { hash: str(record(value, 'commit response'), 'hash', 'commit response') }
}

export function gitCommitFromWire(value: toolbox.CommitInfo): GitCommit {
  const d = 'commit history entry'
  const input = record(value, d)
  return {
    hash: str(input, 'hash', d),
    message: str(input, 'message', d),
    author: str(input, 'author', d),
    email: str(input, 'email', d),
    timestamp: str(input, 'timestamp', d),
  }
}

export function gitRemoteFromWire(value: toolbox.GitRemote): GitRemote {
  const d = 'git remote'
  const input = record(value, d)
  return { name: str(input, 'name', d), url: str(input, 'url', d) }
}
