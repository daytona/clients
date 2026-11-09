/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from './errors/DaytonaError.js'
import { COMPUTER_USE_DESKTOP_FAILURE_CODES } from './types.js'
import type {
  ComputerUseA11yFindResponse,
  ComputerUseA11yTreeResponse,
  ComputerUseAccessibilityNode,
  ComputerUseDesktopFailure,
  ComputerUseDisplay,
  ComputerUseDisplayInfoResponse,
  ComputerUseDisplayWindowsResponse,
  ComputerUseLifecycleResponse,
  ComputerUsePoint,
  ComputerUseProcessErrorsResponse,
  ComputerUseProcessLogsResponse,
  ComputerUseProcessRestartResponse,
  ComputerUseProcessRunningStatus,
  ComputerUseProcessStatus,
  ComputerUseRecording,
  ComputerUseRecordingsResponse,
  ComputerUseRestartResponse,
  ComputerUseScreenshotResponse,
  ComputerUseScrollResponse,
  ComputerUseStatusResponse,
  ComputerUseWindow,
} from './ComputerUseTypes.js'
import type * as toolbox from './gen/toolbox-types.js'
import { knownOrUnknown } from './knownValue.js'
import { bool, int, int32, malformedResponse, nonNegativeInt, nonNegativeInt32, nonNegativeSafeInt, num, optional, record, str, strList, timestamp } from './toolboxModels.js'
import type { WireRecord } from './toolboxModels.js'
import { deepFrozen } from './frozen.js'
import { enumerableSymbolKey } from './symbolKeys.js'
import type { KeyedObject } from './symbolKeys.js'

// Every computer-use result is checked against the contract at this boundary:
// a missing or mistyped required field is the malformed-response
// BadRequestError (400, BAD_REQUEST, source SDK), unknown fields are dropped
// (except the named `extraFields` bags), and optional fields read `null` as
// absent. The Python SDK's generated models enforce the same.

// Each malformed status or restart response names its field, with the
// Python SDK's messages for the same body.
const INVALID_STATUS_RESPONSE = 'Invalid computer-use status response'
const INVALID_RESTART_RESPONSE = 'Invalid computer-use restart response'

type Mutable<T> = { -readonly [K in keyof T]: T[K] }

function invalid(prefix: string, detail: string): BadRequestError {
  return new BadRequestError(`${prefix}: ${detail}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }

  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function isComputerUseDesktopFailureWire(
  value: unknown,
): value is toolbox.ComputerUseDesktopFailure {
  if (!isPlainObject(value)) {
    return false
  }

  return typeof value.code === 'string' && typeof value.message === 'string'
}

function mapComputerUseDesktopFailure(
  failure: toolbox.ComputerUseDesktopFailure,
): ComputerUseDesktopFailure {
  const { code, message, ...extraFields } = failure
  const mapped: Mutable<ComputerUseDesktopFailure> = {
    code: knownOrUnknown(code, COMPUTER_USE_DESKTOP_FAILURE_CODES),
    rawCode: code,
    message,
  }
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return mapped
}

// An attached cause is a failure object regardless of the accompanying state.
// Python's generated model parses it before the state matrix runs, so
// both SDKs reject a malformed one the same way; `null` is the contract's
// "no cause".
function mapWireCause(cause: unknown): ComputerUseDesktopFailure | undefined {
  if (cause === undefined || cause === null) {
    return undefined
  }
  if (!isPlainObject(cause)) throw invalid(INVALID_STATUS_RESPONSE, 'cause must be null or an object')
  for (const field of ['code', 'message'] as const) {
    if (typeof cause[field] !== 'string') throw invalid(INVALID_STATUS_RESPONSE, `cause.${field} must be a string`)
  }
  if (!isComputerUseDesktopFailureWire(cause)) throw invalid(INVALID_STATUS_RESPONSE, 'cause must be null or an object')
  return mapComputerUseDesktopFailure(cause)
}

export function mapComputerUseStatusResponse(
  response: unknown,
): ComputerUseStatusResponse {
  if (!isPlainObject(response)) throw invalid(INVALID_STATUS_RESPONSE, 'the body must be an object')

  const { status, desktop, cause, ...extraFields } = response

  if (typeof status !== 'string') throw invalid(INVALID_STATUS_RESPONSE, 'status must be a string')
  if (typeof desktop !== 'string') throw invalid(INVALID_STATUS_RESPONSE, 'desktop must be a string')

  const mappedCause = mapWireCause(cause)
  const extras: { extraFields?: Record<string, unknown> } =
    Object.keys(extraFields).length > 0 ? { extraFields } : {}

  // Outside the failed state the contract allows `cause` to be omitted or null.
  if (status === 'inactive' && (desktop === 'not_started' || desktop === 'starting')) {
    if (mappedCause === undefined) {
      return deepFrozen({ status, desktop, ...extras })
    }
  } else if (status === 'active' && desktop === 'ready') {
    if (mappedCause === undefined) {
      return deepFrozen({ status, desktop, ...extras })
    }
  } else if (status === 'inactive' && desktop === 'failed') {
    if (mappedCause !== undefined) {
      return deepFrozen({ status, desktop, cause: mappedCause, ...extras })
    }
    throw invalid(INVALID_STATUS_RESPONSE, "cause is required when status is 'inactive' and desktop is 'failed'")
  } else {
    // Preserve unrecognized pairs as raw values so callers can inspect them.
    return deepFrozen({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: status,
      rawDesktop: desktop,
      ...(mappedCause !== undefined ? { cause: mappedCause } : {}),
      ...extras,
    })
  }

  throw invalid(INVALID_STATUS_RESPONSE, `cause must be absent when status is '${status}' and desktop is '${desktop}'`)
}

export function mapComputerUseRestartResponse(
  response: unknown,
): ComputerUseRestartResponse {
  if (!isPlainObject(response)) throw invalid(INVALID_RESTART_RESPONSE, 'the body must be an object')

  const { message, status, desktop, ...extraFields } = response

  if (typeof message !== 'string') throw invalid(INVALID_RESTART_RESPONSE, 'message must be a string')
  if (status !== 'active') throw invalid(INVALID_RESTART_RESPONSE, "status must be 'active'")
  if (desktop !== 'ready') throw invalid(INVALID_RESTART_RESPONSE, "desktop must be 'ready'")

  const mapped: Mutable<ComputerUseRestartResponse> = { message, status, desktop }
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return deepFrozen(mapped)
}

function wireKeyToSdkKey(key: string): string {
  return key.replace(/^[A-Z]+(?=[A-Z][a-z]|$)|^[A-Z]/, (match) => match.toLowerCase())
}

const PROCESS_STATUS_FIELDS = new Set(['Running', 'Priority', 'AutoRestart', 'Pid', 'Failed', 'RestartAttempts', 'RestartBackoffMs', 'Status'])

export function mapComputerUseProcessStatus(status: toolbox.ComputerUseProcessStatus): ComputerUseProcessStatus {
  const d = 'computer-use process status'
  const wire = record(status, d)
  const extraFields: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(wire)) {
    // An own data property even for a key named `__proto__` (assignment would
    // set the bag's prototype instead).
    if (!PROCESS_STATUS_FIELDS.has(key)) Object.defineProperty(extraFields, wireKeyToSdkKey(key), { value, enumerable: true, writable: true, configurable: true })
  }
  const pid = optional(wire, 'Pid', int32, d)
  const mapped: Mutable<ComputerUseProcessStatus> = {
    running: bool(wire, 'Running', d),
    priority: int32(wire, 'Priority', d),
    autoRestart: bool(wire, 'AutoRestart', d),
    ...(pid === undefined ? {} : { pid }),
    failed: bool(wire, 'Failed', d),
    restartAttempts: nonNegativeInt32(wire, 'RestartAttempts', d),
    restartBackoffSeconds: nonNegativeSafeInt(wire, 'RestartBackoffMs', d) / 1000,
    status: str(wire, 'Status', d),
  }
  // Unknown wire fields go in the named bag only, as in the Python SDK.
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return deepFrozen(mapped)
}

export function mapComputerUseProcessStatusMap(statusMap: unknown): Readonly<Record<string, ComputerUseProcessStatus>> {
  const d = 'computer-use process status map'
  const map = record(statusMap, d)
  // A map keyed by process name: a symbol key is no name (JSON has none).
  // Own keys only, as Object.entries reads the names: an inherited key is not
  // part of the response (JSON.parse makes own keys only).
  const symbol = enumerableSymbolKey(map, Object.getPrototypeOf(map) as KeyedObject | null)
  if (symbol !== undefined) throw malformedResponse(`${d}: unexpected key ${String(symbol)}`)
  return deepFrozen(Object.fromEntries(
    Object.entries(map).map(([name, status]) => [name, mapComputerUseProcessStatus(status as toolbox.ComputerUseProcessStatus)]),
  ))
}

export function mapComputerUseLifecycleResponse(response: unknown): ComputerUseLifecycleResponse {
  const d = 'computer-use lifecycle response'
  const wire = record(response, d)
  if (wire.status === undefined || wire.status === null) throw malformedResponse(`${d}: status is required`)
  return deepFrozen({ message: str(wire, 'message', d), status: mapComputerUseProcessStatusMap(wire.status) })
}

export function mapComputerUseProcessRunningStatus(response: unknown): ComputerUseProcessRunningStatus {
  const d = 'computer-use process running status'
  const wire = record(response, d)
  return deepFrozen({ processName: str(wire, 'processName', d), running: bool(wire, 'running', d) })
}

export function mapComputerUseProcessRestartResponse(response: unknown): ComputerUseProcessRestartResponse {
  const d = 'computer-use process restart response'
  const wire = record(response, d)
  return deepFrozen({ message: str(wire, 'message', d), processName: str(wire, 'processName', d) })
}

export function mapComputerUseProcessLogsResponse(response: unknown): ComputerUseProcessLogsResponse {
  const d = 'computer-use process logs response'
  const wire = record(response, d)
  return deepFrozen({ processName: str(wire, 'processName', d), logs: str(wire, 'logs', d) })
}

export function mapComputerUseProcessErrorsResponse(response: unknown): ComputerUseProcessErrorsResponse {
  const d = 'computer-use process errors response'
  const wire = record(response, d)
  return deepFrozen({ processName: str(wire, 'processName', d), errors: str(wire, 'errors', d) })
}

export function mapComputerUsePoint(response: unknown, d = 'computer-use point'): ComputerUsePoint {
  const wire = record(response, d)
  return deepFrozen({ x: int32(wire, 'x', d), y: int32(wire, 'y', d) })
}

export function mapComputerUseScrollResponse(response: unknown): ComputerUseScrollResponse {
  return deepFrozen({ success: bool(record(response, 'computer-use scroll response'), 'success', 'computer-use scroll response') })
}

function mapComputerUseDisplay(display: unknown): ComputerUseDisplay {
  const d = 'computer-use display'
  const wire = record(display, d)
  return {
    id: nonNegativeInt(wire, 'id', d), x: int32(wire, 'x', d), y: int32(wire, 'y', d),
    width: nonNegativeInt32(wire, 'width', d), height: nonNegativeInt32(wire, 'height', d), isActive: bool(wire, 'isActive', d),
  }
}

function mapComputerUseWindow(window: unknown): ComputerUseWindow {
  const d = 'computer-use window'
  const wire = record(window, d)
  return {
    id: nonNegativeSafeInt(wire, 'id', d), title: str(wire, 'title', d), x: int32(wire, 'x', d), y: int32(wire, 'y', d),
    width: nonNegativeInt32(wire, 'width', d), height: nonNegativeInt32(wire, 'height', d), isActive: bool(wire, 'isActive', d),
  }
}

function arrayField(wire: WireRecord, field: string, d: string): unknown[] {
  const value = wire[field]
  if (value === undefined || value === null) throw malformedResponse(`${d}: ${field} is required`)
  if (!Array.isArray(value)) throw malformedResponse(`${d}: ${field} must be a list`)
  return value
}

export function mapComputerUseDisplayInfo(response: unknown): ComputerUseDisplayInfoResponse {
  const d = 'computer-use display info response'
  return deepFrozen({ displays: arrayField(record(response, d), 'displays', d).map(mapComputerUseDisplay) })
}

export function mapComputerUseDisplayWindows(response: unknown): ComputerUseDisplayWindowsResponse {
  const d = 'computer-use display windows response'
  return deepFrozen({ windows: arrayField(record(response, d), 'windows', d).map(mapComputerUseWindow) })
}

/**
 * The deepest accessibility tree the SDK reads: `a11y.tree()` asks the sandbox
 * for at most this many levels below the root (`maxDepth`) unless the caller
 * asks for fewer, and a response nested deeper is malformed. Raising it later
 * is additive.
 */
export const A11Y_MAX_DEPTH = 256

type MutableA11yNode = { -readonly [K in keyof ComputerUseAccessibilityNode]: ComputerUseAccessibilityNode[K] }

// One node's own fields, without its children. An omitted `valueTruncated`
// means false. Both SDKs expose a required boolean.
function mapA11yNodeFields(wire: WireRecord): MutableA11yNode {
  const d = 'computer-use accessibility node'
  const boundsWire = record(wire.bounds, `${d}: bounds`)
  const description = optional(wire, 'description', str, d)
  const actions = optional(wire, 'actions', strList, d)
  const value = optional(wire, 'value', str, d)
  return {
    id: str(wire, 'id', d),
    role: str(wire, 'role', d),
    name: str(wire, 'name', d),
    bounds: {
      x: int32(boundsWire, 'x', `${d}: bounds`), y: int32(boundsWire, 'y', `${d}: bounds`),
      width: int32(boundsWire, 'width', `${d}: bounds`), height: int32(boundsWire, 'height', `${d}: bounds`),
    },
    states: strList(wire, 'states', d),
    valueTruncated: optional(wire, 'valueTruncated', bool, d) === true,
    ...(description === undefined ? {} : { description }),
    ...(actions === undefined ? {} : { actions }),
    ...(value === undefined ? {} : { value }),
  }
}

/**
 * Maps accessibility node trees with an explicit stack (each node's fields are
 * read when it is reached, its children attached to it), so no step recurses
 * whatever the response's depth. A node more than `maxDepth` levels below its
 * root is the malformed-response error.
 */
function mapA11yNodes(roots: readonly unknown[], maxDepth: number, d: string): ComputerUseAccessibilityNode[] {
  const results: ComputerUseAccessibilityNode[] = []
  const stack: { node: unknown; depth: number; into: ComputerUseAccessibilityNode[] }[] = []
  for (let index = roots.length - 1; index >= 0; index--) stack.push({ node: roots[index], depth: 0, into: results })
  for (let entry = stack.pop(); entry !== undefined; entry = stack.pop()) {
    if (entry.depth > maxDepth) throw malformedResponse(`${d}: a node is nested deeper than ${maxDepth} levels`)
    const wire = record(entry.node, 'computer-use accessibility node')
    const mapped = mapA11yNodeFields(wire)
    entry.into.push(mapped)
    if (wire.children === undefined || wire.children === null) continue
    const children = arrayField(wire, 'children', 'computer-use accessibility node')
    const built: ComputerUseAccessibilityNode[] = []
    mapped.children = built
    for (let index = children.length - 1; index >= 0; index--) stack.push({ node: children[index], depth: entry.depth + 1, into: built })
  }
  return results
}

export function mapComputerUseA11yTree(response: unknown, maxDepth: number = A11Y_MAX_DEPTH): ComputerUseA11yTreeResponse {
  const d = 'computer-use accessibility tree response'
  const wire = record(response, d)
  if (wire.root === undefined || wire.root === null) throw malformedResponse(`${d}: root is required`)
  const truncated = bool(wire, 'truncated', d)
  return deepFrozen({ root: mapA11yNodes([wire.root], maxDepth, d)[0]!, truncated })
}

export function mapComputerUseA11yFind(response: unknown): ComputerUseA11yFindResponse {
  const d = 'computer-use accessibility find response'
  const wire = record(response, d)
  const matches = arrayField(wire, 'matches', d)
  const truncated = bool(wire, 'truncated', d)
  return deepFrozen({ matches: mapA11yNodes(matches, A11Y_MAX_DEPTH, d), truncated })
}

export function mapComputerUseScreenshot(response: unknown): ComputerUseScreenshotResponse {
  const d = 'computer-use screenshot response'
  const wire = record(response, d)
  const cursor = wire.cursorPosition
  return deepFrozen({
    screenshot: str(wire, 'screenshot', d),
    sizeBytes: nonNegativeInt(wire, 'sizeBytes', d),
    ...(cursor === undefined || cursor === null ? {} : { cursorPosition: mapComputerUsePoint(cursor, `${d}: cursorPosition`) }),
  })
}

export function mapComputerUseRecording(recording: unknown): ComputerUseRecording {
  const d = 'computer-use recording'
  const wire = record(recording, d)
  const endTime = optional(wire, 'endTime', timestamp, d)
  const durationSeconds = optional(wire, 'durationSeconds', num, d)
  const sizeBytes = optional(wire, 'sizeBytes', int, d)
  return deepFrozen({
    id: str(wire, 'id', d),
    fileName: str(wire, 'fileName', d),
    filePath: str(wire, 'filePath', d),
    startTime: timestamp(wire, 'startTime', d),
    status: str(wire, 'status', d),
    ...(endTime === undefined ? {} : { endTime }),
    ...(durationSeconds === undefined ? {} : { durationSeconds }),
    ...(sizeBytes === undefined ? {} : { sizeBytes }),
  })
}

export function mapComputerUseRecordings(response: unknown): ComputerUseRecordingsResponse {
  const d = 'computer-use recordings response'
  return deepFrozen({ recordings: arrayField(record(response, d), 'recordings', d).map(mapComputerUseRecording) })
}
