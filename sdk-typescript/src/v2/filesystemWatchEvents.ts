/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { FilesystemEvent } from './FileSystem.js'
import { createDaytonaError } from './errors/DaytonaError.js'
import { deepFrozen } from './frozen.js'
import { nestedDeeperThan } from './jsonDepth.js'
import { bool, int, malformedResponse, record, str } from './toolboxModels.js'

function watchJson(data: string, description: string): unknown {
  let parsed: unknown
  try {
    parsed = data ? JSON.parse(data) : {}
  } catch {
    throw malformedResponse(`${description}: the data is not JSON`)
  }
  if (nestedDeeperThan(parsed)) throw malformedResponse(`${description}: the JSON is nested too deeply`)
  return parsed
}

export function parseFilesystemEvent(data: string): FilesystemEvent {
  const description = 'filesystem watch event'
  const raw = record(watchJson(data, description), description)
  return deepFrozen({
    type: str(raw, 'type', description),
    name: str(raw, 'name', description),
    isDir: bool(raw, 'isDir', description),
    timestampMs: int(raw, 'timestamp', description),
  })
}

/** Validate overflow even when no callback handles it. */
export function checkOverflowEvent(data: string): void {
  const description = 'filesystem watch overflow event'
  const parsed = watchJson(data, description)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw malformedResponse(`${description}: the data is not a JSON object`)
  bool(record(parsed, description), 'dropped', description)
}

/** A terminal server error, mapped like a process log stream's error event. */
export function parseWatchError(data: string): Error {
  const description = 'filesystem watch error event'
  const payload = record(watchJson(data, description), description)
  return createDaytonaError(
    str(payload, 'message', description),
    undefined,
    typeof payload.code === 'string' ? payload.code : undefined,
    typeof payload.source === 'string' ? payload.source : 'DAYTONA_DAEMON',
  )
}
