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

export type WireRecord = Readonly<Record<string, unknown>>

/** The malformed-response error: 400, `BAD_REQUEST`, source `SDK`, naming what is wrong. */
export function malformedResponse(message: string): BadRequestError {
  return new BadRequestError(`expected ${message}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function malformed(description: string, field: string): BadRequestError {
  return malformedResponse(`${description}: ${field} is required`)
}

export function record(value: unknown, description: string): WireRecord {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw malformedResponse(description)
  return value as WireRecord
}

export function str(input: WireRecord, field: string, description: string): string {
  const value = input[field]
  if (typeof value !== 'string') throw malformed(description, field)
  return value
}

/** A finite number (a fraction such as a utilization). */
export function num(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw malformed(description, field)
  return value
}

/** An integer, as the contract's integer fields are. */
export function int(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isInteger(value)) throw malformed(description, field)
  return value
}

export function bool(input: WireRecord, field: string, description: string): boolean {
  const value = input[field]
  if (typeof value !== 'boolean') throw malformed(description, field)
  return value
}

/** A list of strings. */
export function strList(input: WireRecord, field: string, description: string): string[] {
  const value = input[field]
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) throw malformed(description, field)
  return [...value]
}

/** An optional field: absent or `null` reads as absent; any other value is checked by `read`. */
export function optional<T>(input: WireRecord, field: string, read: (input: WireRecord, field: string, description: string) => T, description: string): T | undefined {
  const value = input[field]
  return value === undefined || value === null ? undefined : read(input, field, description)
}

// RFC 3339 with the offset's hours 00-23 and minutes 00-59, as the Python SDK
// parses it; the date and time fields are range-checked below.
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/

/** Whether `value` is an RFC 3339 timestamp (with a valid date and time). */
export function isRfc3339(value: string): boolean {
  const match = RFC3339.exec(value)
  if (match === null) return false
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number) as [number, number, number, number, number, number]
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
    && hour <= 23 && minute <= 59 && second <= 60
}

/** A required RFC 3339 timestamp string. */
export function timestamp(input: WireRecord, field: string, description: string): string {
  const value = str(input, field, description)
  if (!isRfc3339(value)) throw malformedResponse(`${description}: ${field} is not an RFC 3339 timestamp`)
  return value
}

/** The `items` of a list response (a bare array or an `{ items }` envelope), each mapped by `map`. */
export function listItems<T>(data: unknown, key: string, description: string, map: (item: never) => T): { items: T[]; nextCursor?: string } {
  if (Array.isArray(data)) return { items: data.map((item) => map(item as never)) }
  const envelope = record(data, description)
  const items = envelope[key]
  if (!Array.isArray(items)) throw malformed(description, key)
  const nextCursor = envelope.nextCursor
  return {
    items: items.map((item) => map(item as never)),
    ...(typeof nextCursor === 'string' && nextCursor !== '' ? { nextCursor } : {}),
  }
}

export function systemMetricsFromWire(value: toolbox.Metrics): SystemMetrics {
  const input = record(value, 'system metrics')
  const d = 'system metrics'
  return {
    timestamp: timestamp(input, 'timestamp', d),
    cpuCount: int(input, 'cpuCount', d),
    cpuUtilization: num(input, 'cpuUtilization', d),
    memoryUsageBytes: int(input, 'memoryUsageBytes', d),
    memoryTotalBytes: int(input, 'memoryTotalBytes', d),
    diskUsageBytes: int(input, 'diskUsageBytes', d),
    diskTotalBytes: int(input, 'diskTotalBytes', d),
  }
}

export function fileInfoFromWire(value: toolbox.FileInfo): FileInfo {
  const d = 'file info response'
  const input = record(value, d)
  return {
    name: str(input, 'name', d),
    path: str(input, 'path', d),
    size: int(input, 'size', d),
    isDir: bool(input, 'isDir', d),
    mode: str(input, 'mode', d),
    permissions: str(input, 'permissions', d),
    owner: str(input, 'owner', d),
    group: str(input, 'group', d),
    modifiedAt: timestamp(input, 'modifiedAt', d),
    modTime: str(input, 'modTime', d),
  }
}

export function fileMatchFromWire(value: toolbox.Match): FileMatch {
  const d = 'grep match'
  const input = record(value, d)
  return { file: str(input, 'file', d), line: int(input, 'line', d), content: str(input, 'content', d) }
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
    ahead: int(input, 'ahead', d),
    behind: int(input, 'behind', d),
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
    timestamp: timestamp(input, 'timestamp', d),
  }
}

export function gitRemoteFromWire(value: toolbox.GitRemote): GitRemote {
  const d = 'git remote'
  const input = record(value, d)
  return { name: str(input, 'name', d), url: str(input, 'url', d) }
}
