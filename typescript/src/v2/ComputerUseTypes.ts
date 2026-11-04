/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ComputerUseDesktopFailureCode } from './types.js'

export type {
  ComputerUseActivityStatus,
  ComputerUseDesktopFailureCode,
  ComputerUseDesktopState,
} from './types.js'

export type ComputerUseMouseButton = 'left' | 'right' | 'middle'
export type ComputerUseScrollDirection = 'up' | 'down'
export type ComputerUseScreenshotFormat = 'png' | 'jpeg'
export type ComputerUseKeyboardModifier = 'ctrl' | 'alt' | 'shift' | 'cmd'
export type ComputerUseA11yScope = 'focused' | 'pid' | 'all'
export type ComputerUseA11yNameMatch = 'exact' | 'substring' | 'regex'

export interface ComputerUseDesktopFailure {
  code: ComputerUseDesktopFailureCode
  rawCode: string
  message: string
  extraFields?: Record<string, unknown>
}

export interface ComputerUseNotStartedStatus {
  status: 'inactive'
  desktop: 'not_started'
  cause?: never
  extraFields?: Record<string, unknown>
}

export interface ComputerUseStartingStatus {
  status: 'inactive'
  desktop: 'starting'
  cause?: never
  extraFields?: Record<string, unknown>
}

export interface ComputerUseReadyStatus {
  status: 'active'
  desktop: 'ready'
  cause?: never
  extraFields?: Record<string, unknown>
}

export interface ComputerUseFailedStatus {
  status: 'inactive'
  desktop: 'failed'
  cause: ComputerUseDesktopFailure
  extraFields?: Record<string, unknown>
}

/**
 * A `(status, desktop)` pair this SDK release does not recognize.
 * `rawStatus` / `rawDesktop` carry the response values; recognized variants need no raw sibling, since
 * their typed values are the wire values.
 */
export interface ComputerUseUnknownStatus {
  status: 'unknown'
  desktop: 'unknown'
  rawStatus: string
  rawDesktop: string
  /** The failure attached to the unrecognized state, when present. */
  cause?: ComputerUseDesktopFailure
  extraFields?: Record<string, unknown>
}

export type ComputerUseStatusResponse =
  | ComputerUseNotStartedStatus
  | ComputerUseStartingStatus
  | ComputerUseReadyStatus
  | ComputerUseFailedStatus
  | ComputerUseUnknownStatus

export interface ComputerUseRestartResponse {
  message: string
  status: 'active'
  desktop: 'ready'
  extraFields?: Record<string, unknown>
}

export interface ComputerUseProcessStatus {
  running?: boolean
  priority?: number
  autoRestart?: boolean
  pid?: number
  failed?: boolean
  restartAttempts?: number
  restartBackoffMs?: number
  status?: string
  extraFields?: Record<string, unknown>
}

export interface ComputerUseLifecycleResponse {
  message: string
  status: Record<string, ComputerUseProcessStatus>
}

export interface ComputerUseProcessRunningStatus {
  processName: string
  running: boolean
}

export interface ComputerUseProcessRestartResponse {
  message: string
  processName: string
}

export interface ComputerUseProcessLogsResponse {
  processName: string
  logs: string
}

export interface ComputerUseProcessErrorsResponse {
  processName: string
  errors: string
}

export interface ComputerUsePoint {
  x: number
  y: number
}

export interface ComputerUseScreenshotResponse {
  screenshot: string
  cursorPosition?: ComputerUsePoint
  sizeBytes?: number
}

export interface ComputerUseMouseMoveRequest extends ComputerUsePoint {}

export interface ComputerUseMouseClickRequest extends ComputerUsePoint {
  button?: ComputerUseMouseButton
  double?: boolean
}

export interface ComputerUseMouseDragRequest {
  startX: number
  startY: number
  endX: number
  endY: number
  button: ComputerUseMouseButton
}

export interface ComputerUseMouseScrollRequest extends ComputerUsePoint {
  direction: ComputerUseScrollDirection
  amount?: number
}

export interface ComputerUseScrollResponse {
  success: boolean
}

export interface ComputerUseKeyboardTypeRequest {
  text: string
  delay?: number
}

export interface ComputerUseKeyboardKeyRequest {
  key: string
  modifiers?: ComputerUseKeyboardModifier[]
}

export interface ComputerUseKeyboardHotkeyRequest {
  keys: string
}

export interface ComputerUseDisplay {
  id: number
  x: number
  y: number
  width: number
  height: number
  isActive: boolean
}

export interface ComputerUseDisplayInfoResponse {
  displays: ComputerUseDisplay[]
}

export interface ComputerUseWindow {
  id: number
  title: string
  x: number
  y: number
  width: number
  height: number
  isActive: boolean
}

export interface ComputerUseDisplayWindowsResponse {
  windows: ComputerUseWindow[]
}

export interface ComputerUseAccessibilityBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface ComputerUseAccessibilityNode {
  id: string
  role: string
  name: string
  description?: string
  /** A control's current value (macOS). Never set for password fields. */
  value?: string
  /** `value` was cut at the platform's cap. `false` when uncut or not reported; the SDK always fills it. */
  valueTruncated: boolean
  bounds: ComputerUseAccessibilityBounds
  states: string[]
  actions?: string[]
  children?: ComputerUseAccessibilityNode[]
}

export interface ComputerUseA11yTreeOptions {
  scope: ComputerUseA11yScope
  pid?: number
  maxDepth?: number
}

export interface ComputerUseA11yTreeResponse {
  root: ComputerUseAccessibilityNode
  truncated: boolean
}

export interface ComputerUseA11yFindRequest {
  scope: ComputerUseA11yScope
  pid?: number
  role?: string
  name?: string
  nameMatch?: ComputerUseA11yNameMatch
  states?: string[]
  limit?: number
}

export interface ComputerUseA11yFindResponse {
  matches: ComputerUseAccessibilityNode[]
  truncated: boolean
}

export interface ComputerUseA11yFocusRequest {
  id: string
}

export interface ComputerUseA11yInvokeRequest {
  id: string
  action?: string
}

export interface ComputerUseA11ySetValueRequest {
  id: string
  value: string
}

export interface ComputerUseRecording {
  id: string
  fileName: string
  filePath: string
  startTime: string
  endTime?: string
  status: string
  durationSeconds?: number
  sizeBytes?: number
}

export interface ComputerUseRecordingStartRequest {
  label?: string
}

export interface ComputerUseRecordingStopRequest {
  id: string
}

export interface ComputerUseRecordingsResponse {
  recordings: ComputerUseRecording[]
}

export interface ComputerUseScreenshotRegionOptions {
  x: number
  y: number
  width: number
  height: number
  showCursor?: boolean
}

export interface ComputerUseScreenshotCompressedOptions {
  format?: ComputerUseScreenshotFormat
  quality?: number
  scale?: number
  showCursor?: boolean
}

export interface ComputerUseScreenshotRegionCompressedOptions extends ComputerUseScreenshotRegionOptions, ComputerUseScreenshotCompressedOptions {}
