/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { InternalServerError } from './errors/DaytonaError.js'
import { COMPUTER_USE_DESKTOP_FAILURE_CODES } from './types.js'
import type {
  ComputerUseAccessibilityNode,
  ComputerUseDesktopFailure,
  ComputerUseLifecycleResponse,
  ComputerUseProcessStatus,
  ComputerUseRestartResponse,
  ComputerUseStatusResponse,
} from './ComputerUseTypes.js'
import type * as toolbox from './gen/toolbox-types.js'
import { knownOrUnknown } from './knownValue.js'

const INVALID_STATUS_RESPONSE =
  'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'
const INVALID_RESTART_RESPONSE =
  'Invalid computer-use restart response: expected a plain object with string message, status="active", and desktop="ready"'

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
  const mapped: ComputerUseDesktopFailure = {
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
    throw new InternalServerError(INVALID_STATUS_RESPONSE, { statusCode: 500, source: 'SDK' })
  }
  return mapComputerUseDesktopFailure(cause)
}

export function mapComputerUseStatusResponse(
  response: unknown,
): ComputerUseStatusResponse {
  if (!isPlainObject(response)) {
    throw new InternalServerError(INVALID_STATUS_RESPONSE, { statusCode: 500, source: 'SDK' })
  }

  const { status, desktop, cause, ...extraFields } = response

  if (typeof status !== 'string' || typeof desktop !== 'string') {
    throw new InternalServerError(INVALID_STATUS_RESPONSE, { statusCode: 500, source: 'SDK' })
  }

  const mappedCause = mapWireCause(cause)
  const extras: { extraFields?: Record<string, unknown> } =
    Object.keys(extraFields).length > 0 ? { extraFields } : {}

  if (status === 'inactive' && (desktop === 'not_started' || desktop === 'starting')) {
    if (cause === undefined) {
      return { status, desktop, ...extras }
    }
  } else if (status === 'active' && desktop === 'ready') {
    if (cause === undefined) {
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

  throw new InternalServerError(INVALID_STATUS_RESPONSE, { statusCode: 500, source: 'SDK' })
}

export function mapComputerUseRestartResponse(
  response: unknown,
): ComputerUseRestartResponse {
  if (!isPlainObject(response)) {
    throw new InternalServerError(INVALID_RESTART_RESPONSE, { statusCode: 500, source: 'SDK' })
  }

  const { message, status, desktop, ...extraFields } = response

  if (typeof message !== 'string' || status !== 'active' || desktop !== 'ready') {
    throw new InternalServerError(INVALID_RESTART_RESPONSE, { statusCode: 500, source: 'SDK' })
  }

  const mapped: ComputerUseRestartResponse = { message, status, desktop }
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return mapped
}

function wireKeyToSdkKey(key: string): string {
  return key.replace(/^[A-Z]+(?=[A-Z][a-z]|$)|^[A-Z]/, (match) => match.toLowerCase())
}

export function mapComputerUseProcessStatus(
  status: toolbox.ComputerUseProcessStatus,
): ComputerUseProcessStatus {
  const extraFields: Record<string, unknown> = {}

  for (const [key, value] of Object.entries(status)) {
    switch (key) {
      case 'Running':
      case 'Priority':
      case 'AutoRestart':
      case 'Pid':
      case 'Failed':
      case 'RestartAttempts':
      case 'RestartBackoffMs':
      case 'Status':
        break
      default:
        extraFields[wireKeyToSdkKey(key)] = value
        break
    }
  }

  const mapped: ComputerUseProcessStatus = {
    running: status.Running,
    priority: status.Priority,
    autoRestart: status.AutoRestart,
    pid: status.Pid,
    failed: status.Failed,
    restartAttempts: status.RestartAttempts,
    restartBackoffMs: status.RestartBackoffMs,
    status: status.Status,
  }
  // Unknown wire fields go in the named bag ONLY. Spreading them at the top
  // level as well duplicated every value and needed an index signature on the
  // public type, which erased type safety on the known fields. Python does the
  // same (extra_fields), per the SDK parity contract.
  if (Object.keys(extraFields).length > 0) {
    mapped.extraFields = extraFields
  }
  return mapped
}

export function mapComputerUseProcessStatusMap(
  statusMap: toolbox.ComputerUseProcessStatusMap | undefined,
): Record<string, ComputerUseProcessStatus> {
  if (statusMap === undefined) {
    return {}
  }

  return Object.fromEntries(
    Object.entries(statusMap).map(([name, status]) => [name, mapComputerUseProcessStatus(status)]),
  )
}

export function mapComputerUseLifecycleResponse(
  response: toolbox.ComputerUseLifecycleResponse,
): ComputerUseLifecycleResponse {
  return {
    message: response.message,
    status: mapComputerUseProcessStatusMap(response.status),
  }
}

// An omitted `valueTruncated` means false. Both SDKs expose a required boolean.
export function mapComputerUseAccessibilityNode(
  node: toolbox.ComputerUseAccessibilityNode,
): ComputerUseAccessibilityNode {
  const { children, valueTruncated, ...fields } = node
  const mapped: ComputerUseAccessibilityNode = { ...fields, valueTruncated: valueTruncated ?? false }
  if (children !== undefined) {
    mapped.children = children.map(mapComputerUseAccessibilityNode)
  }
  return mapped
}
