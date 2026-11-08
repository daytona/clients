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
  BulkWriteFile, BulkWriteResult, FileInfo, FileMatch, LogFrame, LogPage, GitCommit, GitCommitResponse, GitFileStatus, GitRemote, GitStatus, ReplaceResult, SystemMetrics,
} from './types.js'
import { BadRequestError } from './errors/DaytonaError.js'

export type WireRecord = Readonly<Record<string, unknown>>

/** The malformed-response error: 400, `BAD_REQUEST`, source `SDK`, naming what is wrong. */
export function malformedResponse(message: string): BadRequestError {
  return new BadRequestError(`expected ${message}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

/** A missing field "is required"; a present one of the wrong type "must be" `expected`. */
function malformed(description: string, field: string, value: unknown, expected: string): BadRequestError {
  return malformedResponse(value === undefined || value === null ? `${description}: ${field} is required` : `${description}: ${field} must be ${expected}`)
}

export function record(value: unknown, description: string): WireRecord {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw malformedResponse(description)
  return value as WireRecord
}

export function str(input: WireRecord, field: string, description: string): string {
  const value = input[field]
  if (typeof value !== 'string') throw malformed(description, field, value, 'a string')
  return value
}

/** A finite number (a fraction such as a utilization). */
export function num(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw malformed(description, field, value, 'a number')
  return value
}

/** An integer, as the contract's integer fields are. */
export function int(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isInteger(value)) throw malformed(description, field, value, 'an integer')
  return value
}

/** A non-negative integer, for the contract's integer fields with `minimum: 0`. */
export function nonNegativeInt(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw malformed(description, field, value, 'a non-negative integer')
  return value
}

/** A non-negative integer JavaScript represents exactly, for ids with the contract's `maximum: 9007199254740991`. */
export function nonNegativeSafeInt(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw malformed(description, field, value, 'a non-negative safe integer')
  return value
}

/** A positive integer, for the contract's integer fields with `minimum: 1`. */
export function positiveInt(input: WireRecord, field: string, description: string): number {
  const value = input[field]
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) throw malformed(description, field, value, 'a positive integer')
  return value
}

export function bool(input: WireRecord, field: string, description: string): boolean {
  const value = input[field]
  if (typeof value !== 'boolean') throw malformed(description, field, value, 'a boolean')
  return value
}

/** A list of strings. */
export function strList(input: WireRecord, field: string, description: string): string[] {
  const value = input[field]
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) throw malformed(description, field, value, 'a list of strings')
  return [...value]
}

/** An optional field: absent or `null` reads as absent; any other value is checked by `read`. */
export function optional<T>(input: WireRecord, field: string, read: (input: WireRecord, field: string, description: string) => T, description: string): T | undefined {
  const value = input[field]
  return value === undefined || value === null ? undefined : read(input, field, description)
}

// RFC 3339 with the offset's hours 00-23 and minutes 00-59, as the Python SDK
// parses it; the date and time fields are range-checked below (no leap
// second, which Python's datetime cannot represent).
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/

/** Whether `value` is an RFC 3339 timestamp (with a valid date and time). */
export function isRfc3339(value: string): boolean {
  const match = RFC3339.exec(value)
  if (match === null) return false
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number) as [number, number, number, number, number, number]
  // setUTCFullYear, not Date.UTC: Date.UTC maps years 0-99 to 1900-1999.
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  // Year 0000 has no Python datetime, so neither SDK accepts it.
  return year >= 1 && date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
    && hour <= 23 && minute <= 59 && second <= 59
}

/** A required RFC 3339 timestamp string. */
export function timestamp(input: WireRecord, field: string, description: string): string {
  const value = str(input, field, description)
  if (!isRfc3339(value)) throw malformedResponse(`${description}: ${field} is not an RFC 3339 timestamp`)
  return value
}

/** The list in the `key` field of a response envelope (only that shape), each mapped by `map`. */
export function envelopeList<T>(data: unknown, key: string, description: string, map: (item: never) => T): T[] {
  const envelope = record(data, description)
  const items = envelope[key]
  if (!Array.isArray(items)) throw malformed(description, key, items, 'a list')
  return items.map((item) => map(item as never))
}

/** The `items` of a list response (a bare array or an `{ items }` envelope), each mapped by `map`. */
export function listItems<T>(data: unknown, key: string, description: string, map: (item: never) => T): { items: T[]; nextCursor?: string } {
  if (Array.isArray(data)) return { items: data.map((item) => map(item as never)) }
  const envelope = record(data, description)
  const items = envelope[key]
  if (!Array.isArray(items)) throw malformed(description, key, items, 'a list')
  const nextCursor = envelope.nextCursor
  return {
    items: items.map((item) => map(item as never)),
    ...(typeof nextCursor === 'string' && nextCursor !== '' ? { nextCursor } : {}),
  }
}

/**
 * A `ProcessLogsResponse` page, checked strictly: `frames`, `nextCursor`,
 * `truncatedHead` and `eof` are required, with their types.
 */
export function logPageFromWire(value: unknown): LogPage {
  const d = 'process log page'
  const input = record(value, d)
  const firstAvailableCursor = optional(input, 'firstAvailableCursor', str, d)
  return {
    items: envelopeList(input, 'frames', d, logFrameFromWire),
    nextCursor: str(input, 'nextCursor', d),
    truncatedHead: bool(input, 'truncatedHead', d),
    eof: bool(input, 'eof', d),
    ...(firstAvailableCursor === undefined ? {} : { firstAvailableCursor }),
  }
}

/** One `LogFrameDto`. */
export function logFrameFromWire(value: unknown): LogFrame {
  const d = 'log frame'
  const input = record(value, d)
  return {
    cursor: str(input, 'cursor', d),
    channel: str(input, 'channel', d),
    data: str(input, 'data', d),
    timestampMs: int(input, 'timestamp', d),
  }
}

/** A `BulkUploadResponse`: `files` (each with `path` and `bytes`) and `errors` are required. */
export function bulkWriteResultFromWire(value: unknown): BulkWriteResult {
  const d = 'bulk upload response'
  const input = record(value, d)
  const files = envelopeList(input, 'files', d, (item: unknown): BulkWriteFile => {
    const file = record(item, `${d}: files`)
    return { path: str(file, 'path', `${d}: files`), bytes: nonNegativeInt(file, 'bytes', `${d}: files`) }
  })
  return { files, errors: strList(input, 'errors', d) }
}

/** The `id` and `kernel` of a `SessionDto`, with every field the contract requires checked (as the Python SDK does). */
export function interpreterSessionFromWire(value: unknown): { readonly id: string; readonly kernel: string } {
  const d = 'session response'
  const input = record(value, d)
  const id = str(input, 'id', d)
  const kernel = str(input, 'kernel', d)
  str(input, 'kernelProcessId', d)
  str(input, 'cwd', d)
  timestamp(input, 'createdAt', d)
  bool(input, 'active', d)
  strList(input, 'processIds', d)
  return { id, kernel }
}

export function systemMetricsFromWire(value: toolbox.Metrics): SystemMetrics {
  const input = record(value, 'system metrics')
  const d = 'system metrics'
  return {
    timestamp: timestamp(input, 'timestamp', d),
    cpuCount: nonNegativeInt(input, 'cpuCount', d),
    cpuUtilization: num(input, 'cpuUtilization', d),
    memoryUsageBytes: nonNegativeInt(input, 'memoryUsageBytes', d),
    memoryTotalBytes: nonNegativeInt(input, 'memoryTotalBytes', d),
    diskUsageBytes: nonNegativeInt(input, 'diskUsageBytes', d),
    diskTotalBytes: nonNegativeInt(input, 'diskTotalBytes', d),
  }
}

export function fileInfoFromWire(value: toolbox.FileInfo): FileInfo {
  const d = 'file info response'
  const input = record(value, d)
  return {
    name: str(input, 'name', d),
    path: str(input, 'path', d),
    size: nonNegativeInt(input, 'size', d),
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
  return { file: str(input, 'file', d), line: nonNegativeInt(input, 'line', d), content: str(input, 'content', d) }
}

export function replaceResultFromWire(value: toolbox.ReplaceResult): ReplaceResult {
  const d = 'replace result'
  const input = record(value, d)
  const error = optional(input, 'error', str, d)
  return {
    file: str(input, 'file', d),
    success: bool(input, 'success', d),
    ...(error === undefined ? {} : { error }),
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
  if (!Array.isArray(fileStatus)) throw malformed(d, 'fileStatus', fileStatus, 'a list')
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
