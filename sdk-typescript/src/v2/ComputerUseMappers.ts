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
import { bool, int, malformedResponse, nonNegativeInt, nonNegativeSafeInt, num, optional, record, str, strList, timestamp } from './toolboxModels.js'
import type { WireRecord } from './toolboxModels.js'

// Every computer-use result is checked against the contract at this boundary:
// a missing or mistyped required field is the malformed-response
// BadRequestError (400, BAD_REQUEST, source SDK), unknown fields are dropped
// (except the named `extraFields` bags), and optional fields read `null` as
// absent. The Python SDK's generated models enforce the same.

const INVALID_STATUS_RESPONSE =
  'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'
const INVALID_RESTART_RESPONSE =
  'Invalid computer-use restart response: expected a plain object with string message, status="active", and desktop="ready"'

type Mutable<T> = { -readonly [K in keyof T]: T[K] }

function invalid(message: string): BadRequestError {
  return new BadRequestError(message, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
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
  if (!isComputerUseDesktopFailureWire(cause)) {
    throw invalid(INVALID_STATUS_RESPONSE)
  }
  return mapComputerUseDesktopFailure(cause)
}

export function mapComputerUseStatusResponse(
  response: unknown,
): ComputerUseStatusResponse {
  if (!isPlainObject(response)) {
    throw invalid(INVALID_STATUS_RESPONSE)
  }

  const { status, desktop, cause, ...extraFields } = response

  if (typeof status !== 'string' || typeof desktop !== 'string') {
    throw invalid(INVALID_STATUS_RESPONSE)
  }

  const mappedCause = mapWireCause(cause)
  const extras: { extraFields?: Record<string, unknown> } =
    Object.keys(extraFields).length > 0 ? { extraFields } : {}

  // Outside the failed state the contract allows `cause` to be omitted or null.
  if (status === 'inactive' && (desktop === 'not_started' || desktop === 'starting')) {
    if (mappedCause === undefined) {
      return { status, desktop, ...extras }
    }
  } else if (status === 'active' && desktop === 'ready') {
    if (mappedCause === undefined) {
      return { status, desktop, ...extras }
    }
  } else if (status === 'inactive' && desktop === 'failed') {
    if (mappedCause !== undefined) {
      return { status, desktop, cause: mappedCause, ...extras }
    }
  } else {
    // Preserve unrecognized pairs as raw values so callers can inspect them.
    return {
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: status,
      rawDesktop: desktop,
      ...(mappedCause !== undefined ? { cause: mappedCause } : {}),
      ...extras,
    }
  }

  throw invalid(INVALID_STATUS_RESPONSE)
}

export function mapComputerUseRestartResponse(
  response: unknown,
): ComputerUseRestartResponse {
  if (!isPlainObject(response)) {
    throw invalid(INVALID_RESTART_RESPONSE)
  }

  const { message, status, desktop, ...extraFields } = response

  if (typeof message !== 'string' || status !== 'active' || desktop !== 'ready') {
    throw invalid(INVALID_RESTART_RESPONSE)
  }

  const mapped: Mutable<ComputerUseRestartResponse> = { message, status, desktop }
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return mapped
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
    if (!PROCESS_STATUS_FIELDS.has(key)) extraFields[wireKeyToSdkKey(key)] = value
  }
  const pid = optional(wire, 'Pid', int, d)
  const mapped: Mutable<ComputerUseProcessStatus> = {
    running: bool(wire, 'Running', d),
    priority: int(wire, 'Priority', d),
    autoRestart: bool(wire, 'AutoRestart', d),
    ...(pid === undefined ? {} : { pid }),
    failed: bool(wire, 'Failed', d),
    restartAttempts: nonNegativeInt(wire, 'RestartAttempts', d),
    restartBackoffSeconds: nonNegativeSafeInt(wire, 'RestartBackoffMs', d) / 1000,
    status: str(wire, 'Status', d),
  }
  // Unknown wire fields go in the named bag only, as in the Python SDK.
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return mapped
}

export function mapComputerUseProcessStatusMap(statusMap: unknown): Record<string, ComputerUseProcessStatus> {
  const map = record(statusMap, 'computer-use process status map')
  return Object.fromEntries(
    Object.entries(map).map(([name, status]) => [name, mapComputerUseProcessStatus(status as toolbox.ComputerUseProcessStatus)]),
  )
}

export function mapComputerUseLifecycleResponse(response: unknown): ComputerUseLifecycleResponse {
  const d = 'computer-use lifecycle response'
  const wire = record(response, d)
  if (wire.status === undefined || wire.status === null) throw malformedResponse(`${d}: status is required`)
  return { message: str(wire, 'message', d), status: mapComputerUseProcessStatusMap(wire.status) }
}

export function mapComputerUseProcessRunningStatus(response: unknown): ComputerUseProcessRunningStatus {
  const d = 'computer-use process running status'
  const wire = record(response, d)
  return { processName: str(wire, 'processName', d), running: bool(wire, 'running', d) }
}

export function mapComputerUseProcessRestartResponse(response: unknown): ComputerUseProcessRestartResponse {
  const d = 'computer-use process restart response'
  const wire = record(response, d)
  return { message: str(wire, 'message', d), processName: str(wire, 'processName', d) }
}

export function mapComputerUseProcessLogsResponse(response: unknown): ComputerUseProcessLogsResponse {
  const d = 'computer-use process logs response'
  const wire = record(response, d)
  return { processName: str(wire, 'processName', d), logs: str(wire, 'logs', d) }
}

export function mapComputerUseProcessErrorsResponse(response: unknown): ComputerUseProcessErrorsResponse {
  const d = 'computer-use process errors response'
  const wire = record(response, d)
  return { processName: str(wire, 'processName', d), errors: str(wire, 'errors', d) }
}

export function mapComputerUsePoint(response: unknown, d = 'computer-use point'): ComputerUsePoint {
  const wire = record(response, d)
  return { x: int(wire, 'x', d), y: int(wire, 'y', d) }
}

export function mapComputerUseScrollResponse(response: unknown): ComputerUseScrollResponse {
  return { success: bool(record(response, 'computer-use scroll response'), 'success', 'computer-use scroll response') }
}

function mapComputerUseDisplay(display: unknown): ComputerUseDisplay {
  const d = 'computer-use display'
  const wire = record(display, d)
  return {
    id: nonNegativeInt(wire, 'id', d), x: int(wire, 'x', d), y: int(wire, 'y', d),
    width: nonNegativeInt(wire, 'width', d), height: nonNegativeInt(wire, 'height', d), isActive: bool(wire, 'isActive', d),
  }
}

function mapComputerUseWindow(window: unknown): ComputerUseWindow {
  const d = 'computer-use window'
  const wire = record(window, d)
  return {
    id: nonNegativeSafeInt(wire, 'id', d), title: str(wire, 'title', d), x: int(wire, 'x', d), y: int(wire, 'y', d),
    width: nonNegativeInt(wire, 'width', d), height: nonNegativeInt(wire, 'height', d), isActive: bool(wire, 'isActive', d),
  }
}

function arrayField(wire: WireRecord, field: string, d: string): unknown[] {
  const value = wire[field]
  if (!Array.isArray(value)) throw malformedResponse(`${d}: ${field} is required`)
  return value
}

export function mapComputerUseDisplayInfo(response: unknown): ComputerUseDisplayInfoResponse {
  const d = 'computer-use display info response'
  return { displays: arrayField(record(response, d), 'displays', d).map(mapComputerUseDisplay) }
}

export function mapComputerUseDisplayWindows(response: unknown): ComputerUseDisplayWindowsResponse {
  const d = 'computer-use display windows response'
  return { windows: arrayField(record(response, d), 'windows', d).map(mapComputerUseWindow) }
}

// An omitted `valueTruncated` means false. Both SDKs expose a required boolean.
export function mapComputerUseAccessibilityNode(node: unknown): ComputerUseAccessibilityNode {
  const d = 'computer-use accessibility node'
  const wire = record(node, d)
  const boundsWire = record(wire.bounds, `${d}: bounds`)
  const description = optional(wire, 'description', str, d)
  const actions = optional(wire, 'actions', strList, d)
  const value = optional(wire, 'value', str, d)
  const children = wire.children === undefined || wire.children === null ? undefined : arrayField(wire, 'children', d)
  return {
    id: str(wire, 'id', d),
    role: str(wire, 'role', d),
    name: str(wire, 'name', d),
    bounds: {
      x: int(boundsWire, 'x', `${d}: bounds`), y: int(boundsWire, 'y', `${d}: bounds`),
      width: int(boundsWire, 'width', `${d}: bounds`), height: int(boundsWire, 'height', `${d}: bounds`),
    },
    states: strList(wire, 'states', d),
    valueTruncated: optional(wire, 'valueTruncated', bool, d) === true,
    ...(description === undefined ? {} : { description }),
    ...(actions === undefined ? {} : { actions }),
    ...(value === undefined ? {} : { value }),
    ...(children === undefined ? {} : { children: children.map(mapComputerUseAccessibilityNode) }),
  }
}

export function mapComputerUseA11yTree(response: unknown): ComputerUseA11yTreeResponse {
  const d = 'computer-use accessibility tree response'
  const wire = record(response, d)
  if (wire.root === undefined || wire.root === null) throw malformedResponse(`${d}: root is required`)
  return { root: mapComputerUseAccessibilityNode(wire.root), truncated: bool(wire, 'truncated', d) }
}

export function mapComputerUseA11yFind(response: unknown): ComputerUseA11yFindResponse {
  const d = 'computer-use accessibility find response'
  const wire = record(response, d)
  return { matches: arrayField(wire, 'matches', d).map(mapComputerUseAccessibilityNode), truncated: bool(wire, 'truncated', d) }
}

export function mapComputerUseScreenshot(response: unknown): ComputerUseScreenshotResponse {
  const d = 'computer-use screenshot response'
  const wire = record(response, d)
  const cursor = wire.cursorPosition
  return {
    screenshot: str(wire, 'screenshot', d),
    sizeBytes: nonNegativeInt(wire, 'sizeBytes', d),
    ...(cursor === undefined || cursor === null ? {} : { cursorPosition: mapComputerUsePoint(cursor, `${d}: cursorPosition`) }),
  }
}

export function mapComputerUseRecording(recording: unknown): ComputerUseRecording {
  const d = 'computer-use recording'
  const wire = record(recording, d)
  const endTime = optional(wire, 'endTime', timestamp, d)
  const durationSeconds = optional(wire, 'durationSeconds', num, d)
  const sizeBytes = optional(wire, 'sizeBytes', int, d)
  return {
    id: str(wire, 'id', d),
    fileName: str(wire, 'fileName', d),
    filePath: str(wire, 'filePath', d),
    startTime: timestamp(wire, 'startTime', d),
    status: str(wire, 'status', d),
    ...(endTime === undefined ? {} : { endTime }),
    ...(durationSeconds === undefined ? {} : { durationSeconds }),
    ...(sizeBytes === undefined ? {} : { sizeBytes }),
  }
}

export function mapComputerUseRecordings(response: unknown): ComputerUseRecordingsResponse {
  const d = 'computer-use recordings response'
  return { recordings: arrayField(record(response, d), 'recordings', d).map(mapComputerUseRecording) }
}
