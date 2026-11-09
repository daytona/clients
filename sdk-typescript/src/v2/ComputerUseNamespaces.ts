/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  A11Y_MAX_DEPTH,
  mapComputerUseA11yFind,
  mapComputerUseA11yTree,
  mapComputerUseDisplayInfo,
  mapComputerUseDisplayWindows,
  mapComputerUseLifecycleResponse,
  mapComputerUsePoint,
  mapComputerUseProcessErrorsResponse,
  mapComputerUseProcessLogsResponse,
  mapComputerUseProcessRestartResponse,
  mapComputerUseProcessRunningStatus,
  mapComputerUseProcessStatusMap,
  mapComputerUseRecording,
  mapComputerUseRecordings,
  mapComputerUseRestartResponse,
  mapComputerUseScreenshot,
  mapComputerUseScrollResponse,
  mapComputerUseStatusResponse,
} from './ComputerUseMappers.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import { exactMs } from './ProcessWire.js'
import type * as toolbox from './gen/toolbox-types.js'
import { dropUndefined } from './http.js'
import type {
  ComputerUseA11yFindRequest,
  ComputerUseA11yFindResponse,
  ComputerUseA11yFocusRequest,
  ComputerUseA11yInvokeRequest,
  ComputerUseA11ySetValueRequest,
  ComputerUseA11yTreeOptions,
  ComputerUseA11yTreeResponse,
  ComputerUseDisplayInfoResponse,
  ComputerUseDisplayWindowsResponse,
  ComputerUseKeyboardHotkeyRequest,
  ComputerUseKeyboardKeyRequest,
  ComputerUseKeyboardTypeRequest,
  ComputerUseLifecycleResponse,
  ComputerUseMouseClickRequest,
  ComputerUseMouseDragRequest,
  ComputerUseMouseMoveRequest,
  ComputerUseMouseScrollRequest,
  ComputerUsePoint,
  ComputerUseProcessErrorsResponse,
  ComputerUseProcessLogsResponse,
  ComputerUseProcessRestartResponse,
  ComputerUseProcessRunningStatus,
  ComputerUseProcessStatus,
  ComputerUseRecording,
  ComputerUseRecordingsResponse,
  ComputerUseRecordingStartRequest,
  ComputerUseRecordingStopRequest,
  ComputerUseRegion,
  ComputerUseRestartResponse,
  ComputerUseScreenshotOptions,
  ComputerUseScreenshotResponse,
  ComputerUseScrollResponse,
  ComputerUseStatusResponse,
} from './ComputerUseTypes.js'
import { maxResponseBytesOption } from './bodyLimit.js'
import { toolboxPathId } from './toolboxPath.js'

/** Options of `recording.download()`; see its documentation. */
export interface RecordingDownloadOptions {
  readonly maxResponseBytes?: number | null
}

const RECORDING_DOWNLOAD_OPTION_KEYS = optionKeys<RecordingDownloadOptions>()({ maxResponseBytes: true })

/** Internal: the toolbox calls the computer-use services make. */
export interface ComputerUseTransport {
  get<T>(path: string, config?: { params?: Record<string, unknown> }): Promise<T>
  post<T>(path: string, body?: unknown): Promise<T>
  delete<T>(path: string): Promise<T>
  getBytes(path: string, options?: { readonly maxResponseBytes?: number }): Promise<ArrayBuffer>
}

const MOUSE_MOVE_KEYS = optionKeys<ComputerUseMouseMoveRequest>()({ x: true, y: true })
const MOUSE_CLICK_KEYS = optionKeys<ComputerUseMouseClickRequest>()({ x: true, y: true, button: true, double: true })
const MOUSE_DRAG_KEYS = optionKeys<ComputerUseMouseDragRequest>()({ startX: true, startY: true, endX: true, endY: true, button: true })
const MOUSE_SCROLL_KEYS = optionKeys<ComputerUseMouseScrollRequest>()({ x: true, y: true, direction: true, amount: true })
const KEYBOARD_TYPE_KEYS = optionKeys<ComputerUseKeyboardTypeRequest>()({ text: true, delaySeconds: true })
const KEYBOARD_KEY_KEYS = optionKeys<ComputerUseKeyboardKeyRequest>()({ key: true, modifiers: true })
const KEYBOARD_HOTKEY_KEYS = optionKeys<ComputerUseKeyboardHotkeyRequest>()({ keys: true })
const SCREENSHOT_KEYS = optionKeys<ComputerUseScreenshotOptions>()({ region: true, format: true, quality: true, scale: true, showCursor: true })
const REGION_KEYS = optionKeys<ComputerUseRegion>()({ x: true, y: true, width: true, height: true })
const A11Y_TREE_KEYS = optionKeys<ComputerUseA11yTreeOptions>()({ scope: true, pid: true, maxDepth: true })
const A11Y_FIND_KEYS = optionKeys<ComputerUseA11yFindRequest>()({
  scope: true, pid: true, role: true, name: true, nameMatch: true, states: true, limit: true,
})
const A11Y_FOCUS_KEYS = optionKeys<ComputerUseA11yFocusRequest>()({ id: true })
const A11Y_INVOKE_KEYS = optionKeys<ComputerUseA11yInvokeRequest>()({ id: true, action: true })
const A11Y_SET_VALUE_KEYS = optionKeys<ComputerUseA11ySetValueRequest>()({ id: true, value: true })
const RECORDING_START_KEYS = optionKeys<ComputerUseRecordingStartRequest>()({ label: true })
const RECORDING_STOP_KEYS = optionKeys<ComputerUseRecordingStopRequest>()({ id: true })

export class AddonProcessesService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'AddonProcessesService')
    this.transport = transport
  }

  public async status(): Promise<Readonly<Record<string, ComputerUseProcessStatus>>>
  public async status(name: string): Promise<ComputerUseProcessRunningStatus>
  public async status(name?: string): Promise<Readonly<Record<string, ComputerUseProcessStatus>> | ComputerUseProcessRunningStatus> {
    return name === undefined
      ? mapComputerUseProcessStatusMap(
          await this.transport.get<toolbox.ComputerUseProcessStatusMap>('/computeruse/process-status'),
        )
      : mapComputerUseProcessRunningStatus(
          await this.transport.get<toolbox.ComputerUseProcessRunningStatus>(`/computeruse/process/${toolboxPathId(name, 'process name')}/status`),
        )
  }

  public async restart(name: string): Promise<ComputerUseProcessRestartResponse> {
    return mapComputerUseProcessRestartResponse(await this.transport.post<toolbox.ComputerUseProcessRestartResponse>(`/computeruse/process/${toolboxPathId(name, 'process name')}/restart`))
  }

  public async logs(name: string): Promise<ComputerUseProcessLogsResponse> {
    return mapComputerUseProcessLogsResponse(await this.transport.get<toolbox.ComputerUseProcessLogsResponse>(`/computeruse/process/${toolboxPathId(name, 'process name')}/logs`))
  }

  public async errors(name: string): Promise<ComputerUseProcessErrorsResponse> {
    return mapComputerUseProcessErrorsResponse(await this.transport.get<toolbox.ComputerUseProcessErrorsResponse>(`/computeruse/process/${toolboxPathId(name, 'process name')}/errors`))
  }
}

export class MouseService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'MouseService')
    this.transport = transport
  }

  public async position(): Promise<ComputerUsePoint> {
    return mapComputerUsePoint(await this.transport.get<toolbox.ComputerUsePoint>('/computeruse/mouse/position'))
  }

  public async move(request: ComputerUseMouseMoveRequest): Promise<ComputerUsePoint> {
    assertKnownOptions('ComputerUseMouseMoveRequest', request, MOUSE_MOVE_KEYS)
    return mapComputerUsePoint(await this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/move', request satisfies toolbox.ComputerUseMouseMoveRequest))
  }

  public async click(request: ComputerUseMouseClickRequest): Promise<ComputerUsePoint> {
    assertKnownOptions('ComputerUseMouseClickRequest', request, MOUSE_CLICK_KEYS)
    return mapComputerUsePoint(await this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/click', request satisfies toolbox.ComputerUseMouseClickRequest))
  }

  public async drag(request: ComputerUseMouseDragRequest): Promise<ComputerUsePoint> {
    assertKnownOptions('ComputerUseMouseDragRequest', request, MOUSE_DRAG_KEYS)
    return mapComputerUsePoint(await this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/drag', request satisfies toolbox.ComputerUseMouseDragRequest))
  }

  public async scroll(request: ComputerUseMouseScrollRequest): Promise<ComputerUseScrollResponse> {
    assertKnownOptions('ComputerUseMouseScrollRequest', request, MOUSE_SCROLL_KEYS)
    return mapComputerUseScrollResponse(await this.transport.post<toolbox.ComputerUseScrollResponse>('/computeruse/mouse/scroll', request satisfies toolbox.ComputerUseMouseScrollRequest))
  }
}

export class KeyboardService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'KeyboardService')
    this.transport = transport
  }

  public async type(request: ComputerUseKeyboardTypeRequest): Promise<void> {
    assertKnownOptions('ComputerUseKeyboardTypeRequest', request, KEYBOARD_TYPE_KEYS)
    const delay = exactMs('delaySeconds', request.delaySeconds)
    const body: toolbox.ComputerUseKeyboardTypeRequest = delay === undefined ? { text: request.text } : { text: request.text, delay }
    await this.transport.post<void>('/computeruse/keyboard/type', body)
  }

  public async key(request: ComputerUseKeyboardKeyRequest): Promise<void> {
    assertKnownOptions('ComputerUseKeyboardKeyRequest', request, KEYBOARD_KEY_KEYS)
    await this.transport.post<void>('/computeruse/keyboard/key', request satisfies toolbox.ComputerUseKeyboardKeyRequest)
  }

  public async hotkey(request: ComputerUseKeyboardHotkeyRequest): Promise<void> {
    assertKnownOptions('ComputerUseKeyboardHotkeyRequest', request, KEYBOARD_HOTKEY_KEYS)
    await this.transport.post<void>('/computeruse/keyboard/hotkey', request satisfies toolbox.ComputerUseKeyboardHotkeyRequest)
  }
}

export class ScreenService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'ScreenService')
    this.transport = transport
  }

  /**
   * Capture the screen, or `options.region` of it. Setting any of `format`,
   * `quality` or `scale` returns a compressed image.
   */
  public async screenshot(options: ComputerUseScreenshotOptions = {}): Promise<ComputerUseScreenshotResponse> {
    assertKnownOptions('ComputerUseScreenshotOptions', options, SCREENSHOT_KEYS)
    const { region, format, quality, scale, showCursor } = options
    if (region != null) assertKnownOptions('ComputerUseRegion', region, REGION_KEYS)
    const compressed = format !== undefined || quality !== undefined || scale !== undefined
    const query = {
      x: region == null ? undefined : String(region.x),
      y: region == null ? undefined : String(region.y),
      width: region == null ? undefined : String(region.width),
      height: region == null ? undefined : String(region.height),
      format,
      quality: quality === undefined ? undefined : String(quality),
      scale: scale === undefined ? undefined : String(scale),
      show_cursor: showCursor === undefined ? undefined : String(showCursor),
    } satisfies toolbox.ComputerUseScreenshotQuery
    const route = region != null
      ? (compressed ? '/computeruse/screenshot/region/compressed' : '/computeruse/screenshot/region')
      : (compressed ? '/computeruse/screenshot/compressed' : '/computeruse/screenshot')
    return mapComputerUseScreenshot(await this.transport.get<toolbox.ComputerUseScreenshotResponse>(route, { params: dropUndefined(query) }))
  }
}

export class DisplayService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'DisplayService')
    this.transport = transport
  }

  public async info(): Promise<ComputerUseDisplayInfoResponse> {
    return mapComputerUseDisplayInfo(await this.transport.get<toolbox.ComputerUseDisplayInfoResponse>('/computeruse/display/info'))
  }

  public async windows(): Promise<ComputerUseDisplayWindowsResponse> {
    return mapComputerUseDisplayWindows(await this.transport.get<toolbox.ComputerUseDisplayWindowsResponse>('/computeruse/display/windows'))
  }
}

export class A11yService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'A11yService')
    this.transport = transport
  }

  public async tree(options: ComputerUseA11yTreeOptions): Promise<ComputerUseA11yTreeResponse> {
    assertKnownOptions('ComputerUseA11yTreeOptions', options, A11Y_TREE_KEYS)
    const maxDepth = options.maxDepth ?? A11Y_MAX_DEPTH
    if (!Number.isInteger(maxDepth) || maxDepth < 0 || maxDepth > A11Y_MAX_DEPTH) {
      throw badRequest(`maxDepth must be an integer between 0 and ${A11Y_MAX_DEPTH}`)
    }
    const query = {
      scope: options.scope,
      pid: options.pid,
      maxDepth,
    } satisfies toolbox.ComputerUseA11yTreeQuery
    const response = await this.transport.get<toolbox.ComputerUseA11yTreeResponse>('/computeruse/a11y/tree', { params: dropUndefined(query) })
    return mapComputerUseA11yTree(response, maxDepth)
  }

  public async find(request: ComputerUseA11yFindRequest): Promise<ComputerUseA11yFindResponse> {
    assertKnownOptions('ComputerUseA11yFindRequest', request, A11Y_FIND_KEYS)
    const response = await this.transport.post<toolbox.ComputerUseA11yFindResponse>('/computeruse/a11y/find', request satisfies toolbox.ComputerUseA11yFindRequest)
    return mapComputerUseA11yFind(response)
  }

  public async focus(request: ComputerUseA11yFocusRequest): Promise<void> {
    assertKnownOptions('ComputerUseA11yFocusRequest', request, A11Y_FOCUS_KEYS)
    await this.transport.post<void>('/computeruse/a11y/node/focus', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }

  public async invoke(request: ComputerUseA11yInvokeRequest): Promise<void> {
    assertKnownOptions('ComputerUseA11yInvokeRequest', request, A11Y_INVOKE_KEYS)
    await this.transport.post<void>('/computeruse/a11y/node/invoke', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }

  public async setValue(request: ComputerUseA11ySetValueRequest): Promise<void> {
    assertKnownOptions('ComputerUseA11ySetValueRequest', request, A11Y_SET_VALUE_KEYS)
    await this.transport.post<void>('/computeruse/a11y/node/value', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }
}

export class RecordingService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'RecordingService')
    this.transport = transport
  }

  public async start(request: ComputerUseRecordingStartRequest = {}): Promise<ComputerUseRecording> {
    assertKnownOptions('ComputerUseRecordingStartRequest', request, RECORDING_START_KEYS)
    return mapComputerUseRecording(
      await this.transport.post<toolbox.ComputerUseRecording>('/computeruse/recordings/start', request satisfies toolbox.ComputerUseRecordingStartRequest),
    )
  }

  public async stop(request: ComputerUseRecordingStopRequest): Promise<ComputerUseRecording> {
    assertKnownOptions('ComputerUseRecordingStopRequest', request, RECORDING_STOP_KEYS)
    return mapComputerUseRecording(
      await this.transport.post<toolbox.ComputerUseRecording>('/computeruse/recordings/stop', request satisfies toolbox.ComputerUseRecordingStopRequest),
    )
  }

  public async list(): Promise<ComputerUseRecordingsResponse> {
    return mapComputerUseRecordings(await this.transport.get<toolbox.ComputerUseRecordingsResponse>('/computeruse/recordings'))
  }

  public async get(id: string): Promise<ComputerUseRecording> {
    return mapComputerUseRecording(await this.transport.get<toolbox.ComputerUseRecording>(`/computeruse/recordings/${toolboxPathId(id, 'recording id')}`))
  }

  /**
   * Download a recording, holding the whole file in memory. A file transfer:
   * no total deadline, but it fails with `ClientTimeoutError` once no bytes
   * move for the request timeout (a request timeout of `0` leaves it
   * unbounded). `maxResponseBytes` replaces the client's cap on the body for
   * this call (`undefined` or `null` keep the client's cap, `0` means no
   * limit, and any other value (negative, fractional, boolean, string, above
   * `Number.MAX_SAFE_INTEGER`) is a `BadRequestError`); a larger recording is
   * refused with `PayloadTooLargeError`. For a large recording, or one from a
   * sandbox you do not trust, download its `filePath` with
   * `sandbox.fs.downloadFile()` (Node.js-only), which streams to disk with
   * progress and cancellation.
   */
  public async download(id: string, options?: RecordingDownloadOptions): Promise<Uint8Array> {
    assertKnownOptions('RecordingDownloadOptions', options, RECORDING_DOWNLOAD_OPTION_KEYS)
    const maxResponseBytes = maxResponseBytesOption('maxResponseBytes', options?.maxResponseBytes)
    const path = `/computeruse/recordings/${toolboxPathId(id, 'recording id')}/download`
    // The one-argument call unless this call overrides the client's cap.
    return new Uint8Array(await (maxResponseBytes === undefined ? this.transport.getBytes(path) : this.transport.getBytes(path, { maxResponseBytes })))
  }

  public async delete(id: string): Promise<void> {
    await this.transport.delete<void>(`/computeruse/recordings/${toolboxPathId(id, 'recording id')}`)
  }
}

export class ComputerUseLifecycleService {
  private readonly transport: ComputerUseTransport

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'ComputerUseLifecycleService')
    this.transport = transport
  }

  public async status(): Promise<ComputerUseStatusResponse> {
    return mapComputerUseStatusResponse(
      await this.transport.get<toolbox.ComputerUseStatusResponse>('/computeruse/status'),
    )
  }

  public async start(): Promise<ComputerUseLifecycleResponse> {
    return mapComputerUseLifecycleResponse(
      await this.transport.post<toolbox.ComputerUseLifecycleResponse>('/computeruse/start'),
    )
  }

  public async stop(): Promise<ComputerUseLifecycleResponse> {
    return mapComputerUseLifecycleResponse(
      await this.transport.post<toolbox.ComputerUseLifecycleResponse>('/computeruse/stop'),
    )
  }

  public async restart(): Promise<ComputerUseRestartResponse> {
    return mapComputerUseRestartResponse(
      await this.transport.post<toolbox.ComputerUseRestartResponse>('/computeruse/restart'),
    )
  }
}

export {
  AddonProcessesService as ComputerUseServiceNamespace,
  A11yService as ComputerUseA11yNamespace,
  ComputerUseLifecycleService as ComputerUseLifecycleNamespace,
  DisplayService as ComputerUseDisplayNamespace,
  KeyboardService as ComputerUseKeyboardNamespace,
  MouseService as ComputerUseMouseNamespace,
  RecordingService as ComputerUseRecordingNamespace,
  ScreenService as ComputerUseScreenNamespace,
}
