/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  mapComputerUseAccessibilityNode,
  mapComputerUseLifecycleResponse,
  mapComputerUseProcessStatusMap,
  mapComputerUseRestartResponse,
  mapComputerUseStatusResponse,
} from './ComputerUseMappers.js'
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
  ComputerUseRestartResponse,
  ComputerUseScreenshotOptions,
  ComputerUseScreenshotResponse,
  ComputerUseScrollResponse,
  ComputerUseStatusResponse,
} from './ComputerUseTypes.js'
import { strictUriEncode } from './http.js'
import { maxResponseBytesOption } from './bodyLimit.js'
import { assertKnownOptions, optionKeys } from './options.js'

/** Options of `recording.download()`; see its documentation. */
export interface RecordingDownloadOptions {
  readonly maxResponseBytes?: number | null
}

const RECORDING_DOWNLOAD_OPTION_KEYS = optionKeys<RecordingDownloadOptions>()({ maxResponseBytes: true })

export interface ComputerUseTransport {
  get<T>(path: string, config?: { params?: Record<string, unknown> }): Promise<T>
  post<T>(path: string, body?: unknown): Promise<T>
  delete<T>(path: string): Promise<T>
  getBytes(path: string, options?: { readonly maxResponseBytes?: number }): Promise<ArrayBuffer>
}

export class AddonProcessesService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async status(): Promise<Record<string, ComputerUseProcessStatus>>
  public async status(name: string): Promise<ComputerUseProcessRunningStatus>
  public async status(name?: string): Promise<Record<string, ComputerUseProcessStatus> | ComputerUseProcessRunningStatus> {
    return name === undefined
      ? mapComputerUseProcessStatusMap(
          await this.transport.get<toolbox.ComputerUseProcessStatusMap>('/computeruse/process-status'),
        )
      : this.transport.get<toolbox.ComputerUseProcessRunningStatus>(`/computeruse/process/${strictUriEncode(name)}/status`)
  }

  public async restart(name: string): Promise<ComputerUseProcessRestartResponse> {
    return this.transport.post<toolbox.ComputerUseProcessRestartResponse>(`/computeruse/process/${strictUriEncode(name)}/restart`)
  }

  public async logs(name: string): Promise<ComputerUseProcessLogsResponse> {
    return this.transport.get<toolbox.ComputerUseProcessLogsResponse>(`/computeruse/process/${strictUriEncode(name)}/logs`)
  }

  public async errors(name: string): Promise<ComputerUseProcessErrorsResponse> {
    return this.transport.get<toolbox.ComputerUseProcessErrorsResponse>(`/computeruse/process/${strictUriEncode(name)}/errors`)
  }
}

export class MouseService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async position(): Promise<ComputerUsePoint> {
    return this.transport.get<toolbox.ComputerUsePoint>('/computeruse/mouse/position')
  }

  public async move(request: ComputerUseMouseMoveRequest): Promise<ComputerUsePoint> {
    return this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/move', request satisfies toolbox.ComputerUseMouseMoveRequest)
  }

  public async click(request: ComputerUseMouseClickRequest): Promise<ComputerUsePoint> {
    return this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/click', request satisfies toolbox.ComputerUseMouseClickRequest)
  }

  public async drag(request: ComputerUseMouseDragRequest): Promise<ComputerUsePoint> {
    return this.transport.post<toolbox.ComputerUsePoint>('/computeruse/mouse/drag', request satisfies toolbox.ComputerUseMouseDragRequest)
  }

  public async scroll(request: ComputerUseMouseScrollRequest): Promise<ComputerUseScrollResponse> {
    return this.transport.post<toolbox.ComputerUseScrollResponse>('/computeruse/mouse/scroll', request satisfies toolbox.ComputerUseMouseScrollRequest)
  }
}

export class KeyboardService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async type(request: ComputerUseKeyboardTypeRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/keyboard/type', request satisfies toolbox.ComputerUseKeyboardTypeRequest)
  }

  public async key(request: ComputerUseKeyboardKeyRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/keyboard/key', request satisfies toolbox.ComputerUseKeyboardKeyRequest)
  }

  public async hotkey(request: ComputerUseKeyboardHotkeyRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/keyboard/hotkey', request satisfies toolbox.ComputerUseKeyboardHotkeyRequest)
  }
}

export class ScreenService {
  constructor(private readonly transport: ComputerUseTransport) {}

  /**
   * Capture the screen, or `options.region` of it. Setting any of `format`,
   * `quality` or `scale` returns a compressed image.
   */
  public async screenshot(options: ComputerUseScreenshotOptions = {}): Promise<ComputerUseScreenshotResponse> {
    const { region, format, quality, scale, showCursor } = options
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
    return this.transport.get<toolbox.ComputerUseScreenshotResponse>(route, { params: dropUndefined(query) })
  }
}

export class DisplayService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async info(): Promise<ComputerUseDisplayInfoResponse> {
    return this.transport.get<toolbox.ComputerUseDisplayInfoResponse>('/computeruse/display/info')
  }

  public async windows(): Promise<ComputerUseDisplayWindowsResponse> {
    return this.transport.get<toolbox.ComputerUseDisplayWindowsResponse>('/computeruse/display/windows')
  }
}

export class A11yService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async tree(options: ComputerUseA11yTreeOptions): Promise<ComputerUseA11yTreeResponse> {
    const query = {
      scope: options.scope,
      pid: options.pid,
      maxDepth: options.maxDepth,
    } satisfies toolbox.ComputerUseA11yTreeQuery
    const response = await this.transport.get<toolbox.ComputerUseA11yTreeResponse>('/computeruse/a11y/tree', { params: dropUndefined(query) })
    return { ...response, root: mapComputerUseAccessibilityNode(response.root) }
  }

  public async find(request: ComputerUseA11yFindRequest): Promise<ComputerUseA11yFindResponse> {
    const response = await this.transport.post<toolbox.ComputerUseA11yFindResponse>('/computeruse/a11y/find', request satisfies toolbox.ComputerUseA11yFindRequest)
    return { ...response, matches: response.matches.map(mapComputerUseAccessibilityNode) }
  }

  public async focus(request: ComputerUseA11yFocusRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/a11y/node/focus', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }

  public async invoke(request: ComputerUseA11yInvokeRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/a11y/node/invoke', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }

  public async setValue(request: ComputerUseA11ySetValueRequest): Promise<void> {
    await this.transport.post<void>('/computeruse/a11y/node/value', request satisfies toolbox.ComputerUseA11yNodeRequest)
  }
}

export class RecordingService {
  constructor(private readonly transport: ComputerUseTransport) {}

  public async start(request: ComputerUseRecordingStartRequest = {}): Promise<ComputerUseRecording> {
    return this.transport.post<toolbox.ComputerUseRecording>('/computeruse/recordings/start', request satisfies toolbox.ComputerUseRecordingStartRequest)
  }

  public async stop(request: ComputerUseRecordingStopRequest): Promise<ComputerUseRecording> {
    return this.transport.post<toolbox.ComputerUseRecording>('/computeruse/recordings/stop', request satisfies toolbox.ComputerUseRecordingStopRequest)
  }

  public async list(): Promise<ComputerUseRecordingsResponse> {
    return this.transport.get<toolbox.ComputerUseRecordingsResponse>('/computeruse/recordings')
  }

  public async get(id: string): Promise<ComputerUseRecording> {
    return this.transport.get<toolbox.ComputerUseRecording>(`/computeruse/recordings/${strictUriEncode(id)}`)
  }

  /**
   * Download a recording into memory. A file transfer: no total deadline, but
   * it fails with `ClientTimeoutError` once no bytes move for the request
   * timeout (a request timeout of `0` leaves it unbounded). `maxResponseBytes` replaces the client's cap on the body for this
   * call (`undefined` or `null` keep the client's cap, `0` means no limit, and any other value (negative, fractional, boolean, string, above `Number.MAX_SAFE_INTEGER`) is a `BadRequestError`);
   * a larger recording is refused with `PayloadTooLargeError`.
   */
  public async download(id: string, options?: RecordingDownloadOptions): Promise<Uint8Array> {
    assertKnownOptions('RecordingDownloadOptions', options, RECORDING_DOWNLOAD_OPTION_KEYS)
    const maxResponseBytes = maxResponseBytesOption('maxResponseBytes', options?.maxResponseBytes)
    const path = `/computeruse/recordings/${strictUriEncode(id)}/download`
    return new Uint8Array(await this.transport.getBytes(path, maxResponseBytes === undefined ? undefined : { maxResponseBytes }))
  }

  public async delete(id: string): Promise<void> {
    await this.transport.delete<void>(`/computeruse/recordings/${strictUriEncode(id)}`)
  }
}

export class ComputerUseLifecycleService {
  constructor(private readonly transport: ComputerUseTransport) {}

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
