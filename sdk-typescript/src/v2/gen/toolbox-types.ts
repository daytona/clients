/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { components } from "./toolbox.js"

type Schema<K extends keyof components["schemas"]> = components["schemas"][K]
type Simplify<T> = { [K in keyof T]: T[K] } & {}
// Optional toolbox fields are absent on the wire rather than null. Map the
// nullable generated fields to `field?: T` for the SDK boundary.
type OptionalizeNullable<T> = Simplify<
  { [K in keyof T as null extends T[K] ? K : never]?: Exclude<T[K], null> } &
  { [K in keyof T as null extends T[K] ? never : K]: T[K] }
>

export type AddRequest = OptionalizeNullable<Schema<"AddRequest">>
export type BranchList = OptionalizeNullable<Schema<"BranchList">>
export type BranchRequest = OptionalizeNullable<Schema<"BranchRequest">>
export type CheckoutRequest = OptionalizeNullable<Schema<"CheckoutRequest">>
export type CloneRequest = OptionalizeNullable<Schema<"CloneRequest">>
export type CommitInfo = OptionalizeNullable<Schema<"CommitInfo">>
export type CommitRequest = OptionalizeNullable<Schema<"CommitRequest">>
export type CommitResponse = OptionalizeNullable<Schema<"CommitResponse">>
export type ComputerUseA11yFindRequest = OptionalizeNullable<Schema<"ComputerUseA11yFindRequest">>
export type ComputerUseA11yFindResponse = Omit<OptionalizeNullable<Schema<"ComputerUseA11yFindResponse">>, 'matches'> & {
  matches: ComputerUseAccessibilityNode[]
}
export type ComputerUseA11yNodeRequest = OptionalizeNullable<Schema<"ComputerUseA11yNodeRequest">>
export type ComputerUseA11yTreeQuery = OptionalizeNullable<Schema<"ComputerUseA11yTreeQuery">>
export type ComputerUseA11yTreeResponse = Omit<OptionalizeNullable<Schema<"ComputerUseA11yTreeResponse">>, 'root'> & {
  root: ComputerUseAccessibilityNode
}
export type ComputerUseAccessibilityBounds = OptionalizeNullable<Schema<"ComputerUseAccessibilityBounds">>
export type ComputerUseAccessibilityNode = Omit<OptionalizeNullable<Schema<"ComputerUseAccessibilityNode">>, 'bounds' | 'children'> & {
  bounds: ComputerUseAccessibilityBounds
  children?: ComputerUseAccessibilityNode[]
}
export type ComputerUseDisplayInfoResponse = OptionalizeNullable<Schema<"ComputerUseDisplayInfoResponse">>
export type ComputerUseDisplay = OptionalizeNullable<Schema<"ComputerUseDisplay">>
export type ComputerUseDisplayWindowsResponse = OptionalizeNullable<Schema<"ComputerUseDisplayWindowsResponse">>
export type ComputerUseKeyboardHotkeyRequest = OptionalizeNullable<Schema<"ComputerUseKeyboardHotkeyRequest">>
export type ComputerUseKeyboardKeyRequest = OptionalizeNullable<Schema<"ComputerUseKeyboardKeyRequest">>
export type ComputerUseKeyboardTypeRequest = OptionalizeNullable<Schema<"ComputerUseKeyboardTypeRequest">>
export type ComputerUseLifecycleResponse = Omit<OptionalizeNullable<Schema<"ComputerUseLifecycleResponse">>, 'status'> & {
  status: ComputerUseProcessStatusMap
}
export type ComputerUseMouseClickRequest = OptionalizeNullable<Schema<"ComputerUseMouseClickRequest">>
export type ComputerUseMouseDragRequest = OptionalizeNullable<Schema<"ComputerUseMouseDragRequest">>
export type ComputerUseMouseMoveRequest = OptionalizeNullable<Schema<"ComputerUseMouseMoveRequest">>
export type ComputerUseMouseScrollRequest = OptionalizeNullable<Schema<"ComputerUseMouseScrollRequest">>
export type ComputerUsePoint = OptionalizeNullable<Schema<"ComputerUsePoint">>
export type ComputerUseProcessErrorsResponse = OptionalizeNullable<Schema<"ComputerUseProcessErrorsResponse">>
export type ComputerUseProcessLogsResponse = OptionalizeNullable<Schema<"ComputerUseProcessLogsResponse">>
export type ComputerUseProcessRestartResponse = OptionalizeNullable<Schema<"ComputerUseProcessRestartResponse">>
export type ComputerUseProcessRunningStatus = OptionalizeNullable<Schema<"ComputerUseProcessRunningStatus">>
export type ComputerUseProcessStatus = OptionalizeNullable<Schema<"ComputerUseProcessStatus">> & Record<string, unknown>
export type ComputerUseProcessStatusMap = Record<string, ComputerUseProcessStatus>
export type ComputerUseRecordingStartRequest = OptionalizeNullable<Schema<"ComputerUseRecordingStartRequest">>
export type ComputerUseRecordingStopRequest = OptionalizeNullable<Schema<"ComputerUseRecordingStopRequest">>
export type ComputerUseRecording = OptionalizeNullable<Schema<"ComputerUseRecording">>
export type ComputerUseRecordingsResponse = Omit<OptionalizeNullable<Schema<"ComputerUseRecordingsResponse">>, 'recordings'> & {
  recordings: ComputerUseRecording[]
}
export type ComputerUseScreenshotQuery = OptionalizeNullable<Schema<"ComputerUseScreenshotQuery">>
export type ComputerUseScreenshotResponse = Omit<OptionalizeNullable<Schema<"ComputerUseScreenshotResponse">>, 'cursorPosition'> & {
  cursorPosition?: ComputerUsePoint
}
export type ComputerUseScrollResponse = OptionalizeNullable<Schema<"ComputerUseScrollResponse">>
export type ComputerUseActivityStatus = Schema<"ComputerUseActivityStatus">
export type ComputerUseDesktopState = Schema<"ComputerUseDesktopState">
export type ComputerUseDesktopFailureCode = Schema<"ComputerUseDesktopFailureCode">
export type ComputerUseDesktopFailure = OptionalizeNullable<Schema<"ComputerUseDesktopFailure">>
export type ComputerUseRestartResponse = OptionalizeNullable<Schema<"ComputerUseRestartResponse">>
export type ComputerUseStatusResponse = Omit<OptionalizeNullable<Schema<"ComputerUseStatusResponse">>, 'cause'> & {
  cause?: ComputerUseDesktopFailure
}
export type ComputerUseWindow = OptionalizeNullable<Schema<"ComputerUseWindow">>
export type CompletionContext = OptionalizeNullable<Schema<"CompletionContext">>
export type CompletionItem = OptionalizeNullable<Schema<"CompletionItem">>
export type CompletionList = Omit<OptionalizeNullable<Schema<"CompletionList">>, 'items'> & {
  items: CompletionItem[]
}
export type CompletionRequest = OptionalizeNullable<Schema<"CompletionRequest">>
export type CreateProcessRequest = OptionalizeNullable<Schema<"CreateProcessRequest">>
export type CreateSessionRequest = OptionalizeNullable<Schema<"CreateSessionRequest">>
export type DocumentRequest = OptionalizeNullable<Schema<"DocumentRequest">>
export type DockerOwner = Schema<"DockerOwner">
export type DockerState = Schema<"DockerState">
export type DockerStatus = OptionalizeNullable<Schema<"DockerStatus">>
export type EnvRequest = OptionalizeNullable<Schema<"EnvRequest">>
export type ExitResult = OptionalizeNullable<Schema<"ExitResult">>
export type FileInfo = OptionalizeNullable<Schema<"FileInfo">>
export type GitStatus = OptionalizeNullable<Schema<"StatusResponse">>
export type LogFrameDto = OptionalizeNullable<Schema<"LogFrameDto">>
export type GitRemote = OptionalizeNullable<Schema<"GitRemote">>
export type ListRemotesResponse = OptionalizeNullable<Schema<"ListRemotesResponse">>
export type MarkupContent = OptionalizeNullable<Schema<"MarkupContent">>
export type Match = OptionalizeNullable<Schema<"Match">>
export type Metrics = OptionalizeNullable<Schema<"Metrics">>
export type BulkUploadResponse = OptionalizeNullable<Schema<"BulkUploadResponse">>
export type PortInUseResponse = OptionalizeNullable<Schema<"IsPortInUseResponse">>
export type PortList = OptionalizeNullable<Schema<"PortList">>
type ProcessRecord = Simplify<
  Omit<OptionalizeNullable<Schema<"ProcessRecord">>, 'keepLogs' | 'kind' | 'reason' | 'state' | 'stdin' | 'terminal'> & {
    keepLogs: Schema<'KeepLogs'>
    kind: Schema<'Kind'>
    reason?: Schema<'Reason'>
    state: Schema<'State'>
    stdin: Schema<'StdinMode'>
    terminal?: TerminalSize
  }
>
export type ProcessDto = Simplify<ProcessRecord & {
  firstAvailableCursor?: string
  lastCursor?: string
  truncatedHead: boolean
  system: boolean
  /** Only on a `POST /processes?waitMs=…&logs=` response. */
  logs?: ProcessLogsResponse
}>
export type ProcessListResponse = Omit<OptionalizeNullable<Schema<"ProcessListResponse">>, 'items'> & {
  items: ProcessDto[]
}
export type ProcessLogsResponse = Omit<OptionalizeNullable<Schema<"ProcessLogsResponse">>, 'frames'> & {
  frames: LogFrameDto[]
}
export type ReplaceRequest = OptionalizeNullable<Schema<"ReplaceRequest">>
export type ReplaceResult = OptionalizeNullable<Schema<"ReplaceResult">>
export type RepoRequest = OptionalizeNullable<Schema<"RepoRequest">>
export type ResizeRequest = OptionalizeNullable<Schema<"ResizeRequest">>
export type SearchResponse = OptionalizeNullable<Schema<"SearchResponse">>
export type ServerRequest = OptionalizeNullable<Schema<"ServerRequest">>
export type SessionDto = OptionalizeNullable<Schema<"SessionDto">>
export type SessionListResponse = OptionalizeNullable<Schema<"SessionListResponse">>
export type SignalRequest = OptionalizeNullable<Schema<"SignalRequest">>
export type SymbolInfo = OptionalizeNullable<Schema<"SymbolInfo">>
export type SymbolLocation = OptionalizeNullable<Schema<"SymbolLocation">>
export type SymbolPosition = OptionalizeNullable<Schema<"SymbolPosition">>
export type SymbolRange = OptionalizeNullable<Schema<"SymbolRange">>
export type StdinRequest = OptionalizeNullable<Schema<"StdinRequest">>
export type TerminalSize = OptionalizeNullable<Schema<"TerminalSize">>
export type PullVolumeRequest = Omit<Schema<"PullVolumeRequest">, "waitMs"> & { readonly waitMs?: Schema<"PullVolumeRequest">["waitMs"] }
export type PullVolumeResponse = Schema<"PullVolumeResponse">
