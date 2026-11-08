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
  readonly code: ComputerUseDesktopFailureCode
  readonly rawCode: string
  readonly message: string
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseNotStartedStatus {
  readonly status: 'inactive'
  readonly desktop: 'not_started'
  readonly cause?: never
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseStartingStatus {
  readonly status: 'inactive'
  readonly desktop: 'starting'
  readonly cause?: never
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseReadyStatus {
  readonly status: 'active'
  readonly desktop: 'ready'
  readonly cause?: never
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseFailedStatus {
  readonly status: 'inactive'
  readonly desktop: 'failed'
  readonly cause: ComputerUseDesktopFailure
  readonly extraFields?: Record<string, unknown>
}

/**
 * A `(status, desktop)` pair this SDK release does not recognize.
 * `rawStatus` / `rawDesktop` carry the response values; recognized variants need no raw sibling, since
 * their typed values are the wire values.
 */
export interface ComputerUseUnknownStatus {
  readonly status: 'unknown'
  readonly desktop: 'unknown'
  readonly rawStatus: string
  readonly rawDesktop: string
  /** The failure attached to the unrecognized state, when present. */
  readonly cause?: ComputerUseDesktopFailure
  readonly extraFields?: Record<string, unknown>
}

export type ComputerUseStatusResponse =
  | ComputerUseNotStartedStatus
  | ComputerUseStartingStatus
  | ComputerUseReadyStatus
  | ComputerUseFailedStatus
  | ComputerUseUnknownStatus

export interface ComputerUseRestartResponse {
  readonly message: string
  readonly status: 'active'
  readonly desktop: 'ready'
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseProcessStatus {
  readonly running: boolean
  readonly priority: number
  readonly autoRestart: boolean
  readonly pid?: number
  readonly failed: boolean
  readonly restartAttempts: number
  /** The current restart backoff, in seconds. */
  readonly restartBackoffSeconds: number
  readonly status: string
  readonly extraFields?: Record<string, unknown>
}

export interface ComputerUseLifecycleResponse {
  readonly message: string
  readonly status: Record<string, ComputerUseProcessStatus>
}

export interface ComputerUseProcessRunningStatus {
  readonly processName: string
  readonly running: boolean
}

export interface ComputerUseProcessRestartResponse {
  readonly message: string
  readonly processName: string
}

export interface ComputerUseProcessLogsResponse {
  readonly processName: string
  readonly logs: string
}

export interface ComputerUseProcessErrorsResponse {
  readonly processName: string
  readonly errors: string
}

export interface ComputerUsePoint {
  readonly x: number
  readonly y: number
}

export interface ComputerUseScreenshotResponse {
  /** The image, base64-encoded. */
  readonly screenshot: string
  readonly sizeBytes: number
  readonly cursorPosition?: ComputerUsePoint
}

export interface ComputerUseMouseMoveRequest extends ComputerUsePoint {}

export interface ComputerUseMouseClickRequest extends ComputerUsePoint {
  readonly button?: ComputerUseMouseButton
  readonly double?: boolean
}

export interface ComputerUseMouseDragRequest {
  readonly startX: number
  readonly startY: number
  readonly endX: number
  readonly endY: number
  /** Default `'left'`. */
  readonly button?: ComputerUseMouseButton
}

export interface ComputerUseMouseScrollRequest extends ComputerUsePoint {
  readonly direction: ComputerUseScrollDirection
  readonly amount?: number
}

export interface ComputerUseScrollResponse {
  readonly success: boolean
}

export interface ComputerUseKeyboardTypeRequest {
  readonly text: string
  /** Pause between key presses, in seconds. */
  readonly delaySeconds?: number
}

export interface ComputerUseKeyboardKeyRequest {
  readonly key: string
  readonly modifiers?: ComputerUseKeyboardModifier[]
}

export interface ComputerUseKeyboardHotkeyRequest {
  readonly keys: string
}

export interface ComputerUseDisplay {
  readonly id: number
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
  readonly isActive: boolean
}

export interface ComputerUseDisplayInfoResponse {
  readonly displays: readonly ComputerUseDisplay[]
}

export interface ComputerUseWindow {
  readonly id: number
  readonly title: string
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
  readonly isActive: boolean
}

export interface ComputerUseDisplayWindowsResponse {
  readonly windows: readonly ComputerUseWindow[]
}

export interface ComputerUseAccessibilityBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface ComputerUseAccessibilityNode {
  readonly id: string
  readonly role: string
  readonly name: string
  readonly description?: string
  /** A control's current value (macOS). Never set for password fields. */
  readonly value?: string
  /** `value` was cut at the platform's cap. `false` when uncut or not reported; the SDK always fills it. */
  readonly valueTruncated: boolean
  readonly bounds: ComputerUseAccessibilityBounds
  readonly states: readonly string[]
  readonly actions?: readonly string[]
  readonly children?: readonly ComputerUseAccessibilityNode[]
}

export interface ComputerUseA11yTreeOptions {
  readonly scope: ComputerUseA11yScope
  readonly pid?: number
  readonly maxDepth?: number
}

export interface ComputerUseA11yTreeResponse {
  readonly root: ComputerUseAccessibilityNode
  readonly truncated: boolean
}

export interface ComputerUseA11yFindRequest {
  readonly scope: ComputerUseA11yScope
  readonly pid?: number
  readonly role?: string
  readonly name?: string
  readonly nameMatch?: ComputerUseA11yNameMatch
  readonly states?: string[]
  readonly limit?: number
}

export interface ComputerUseA11yFindResponse {
  readonly matches: readonly ComputerUseAccessibilityNode[]
  readonly truncated: boolean
}

export interface ComputerUseA11yFocusRequest {
  readonly id: string
}

export interface ComputerUseA11yInvokeRequest {
  readonly id: string
  readonly action?: string
}

export interface ComputerUseA11ySetValueRequest {
  readonly id: string
  readonly value: string
}

export interface ComputerUseRecording {
  readonly id: string
  readonly fileName: string
  readonly filePath: string
  readonly startTime: string
  readonly endTime?: string
  readonly status: string
  readonly durationSeconds?: number
  readonly sizeBytes?: number
}

export interface ComputerUseRecordingStartRequest {
  readonly label?: string
}

export interface ComputerUseRecordingStopRequest {
  readonly id: string
}

export interface ComputerUseRecordingsResponse {
  readonly recordings: readonly ComputerUseRecording[]
}

export interface ComputerUseRegion {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

/**
 * Options for `screen.screenshot()`. `region` captures only that rectangle;
 * any of `format` / `quality` / `scale` returns a compressed image.
 */
export interface ComputerUseScreenshotOptions {
  readonly region?: ComputerUseRegion
  readonly format?: ComputerUseScreenshotFormat
  readonly quality?: number
  readonly scale?: number
  readonly showCursor?: boolean
}
