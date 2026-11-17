/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  ComputerUseLifecycleResponse,
  ComputerUseRestartResponse,
  ComputerUseStartOptions,
  ComputerUseStatusResponse,
} from './ComputerUseTypes.js'
import {
  AddonProcessesService,
  A11yService,
  ComputerUseLifecycleService,
  DisplayService,
  KeyboardService,
  MouseService,
  RecordingService,
  ScreenService,
  type ComputerUseTransport,
} from './ComputerUseNamespaces.js'
import { assertInternal, INTERNAL } from './internal.js'
import type { Internal } from './internal.js'

/** Sandbox computer-use namespace (`sandbox.computerUse`): the desktop and its input devices. */
export class ComputerUseService {
  public readonly service: AddonProcessesService
  public readonly mouse: MouseService
  public readonly keyboard: KeyboardService
  public readonly screen: ScreenService
  public readonly display: DisplayService
  public readonly a11y: A11yService
  public readonly recording: RecordingService

  private readonly lifecycle: ComputerUseLifecycleService

  /** @internal */
  constructor(token: Internal, transport: ComputerUseTransport)
  /** Built by the SDK: get it from `sandbox.computerUse`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: ComputerUseTransport) {
    assertInternal(token, 'ComputerUseService')
    this.lifecycle = new ComputerUseLifecycleService(INTERNAL, transport)
    this.service = new AddonProcessesService(INTERNAL, transport)
    this.mouse = new MouseService(INTERNAL, transport)
    this.keyboard = new KeyboardService(INTERNAL, transport)
    this.screen = new ScreenService(INTERNAL, transport)
    this.display = new DisplayService(INTERNAL, transport)
    this.a11y = new A11yService(INTERNAL, transport)
    this.recording = new RecordingService(INTERNAL, transport)
  }

  /**
   * Read the desktop state, resolution and environment. Linux reports the
   * running environment, otherwise the next start's: last selected, or xfce.
   * Settings are absent on Windows and macOS and may be absent on older guests.
   * Unknown environments retain their rawEnvironment.
   */
  public async status(): Promise<ComputerUseStatusResponse> {
    return this.lifecycle.status()
  }

  /**
   * Start the desktop. `resolution` selects its size (1024x768, 1280x720 or
   * 1920x1080) and is kept for later starts and restarts; omitted, the desktop
   * uses the resolution it last started at, or 1280x720. `environment` selects
   * xfce or gnome, kept for later starts; omitted, the last selected, or xfce.
   * GNOME needs a system layer with GNOME, a successful preflight, at least
   * 4 vCPUs and 8192 MiB. Unsupported settings or insufficient resources throw
   * `BadRequestError`. Starting a running desktop with different settings
   * throws `ConflictError`; use restart instead. Windows and macOS refuse
   * either setting with `UnsupportedOperationError`, as does a system layer
   * without GNOME when it is requested. Environment alone adds no read-back
   * request. With a `resolution`, the
   * SDK reads back the size the desktop runs at and throws
   * `UnsupportedOperationError` when it differs: a guest that predates
   * resolution selection ignores it, and the desktop keeps running at its own.
   * If that read itself fails, its error is thrown although the start took
   * effect; starting again at the same resolution is a no-op.
   */
  public async start(options: ComputerUseStartOptions = {}): Promise<ComputerUseLifecycleResponse> {
    return this.lifecycle.start(options)
  }

  public async stop(): Promise<ComputerUseLifecycleResponse> {
    return this.lifecycle.stop()
  }

  /**
   * Restart with optional `resolution` and `environment` (xfce or gnome),
   * kept for later starts. Omitted fields reuse the last selection, defaulting
   * to 1280x720 and xfce. Use this instead of start to change a running
   * desktop's settings. GNOME needs a system layer with GNOME, a successful
   * preflight, at least 4 vCPUs and 8192 MiB; insufficient resources or
   * unsupported settings throw `BadRequestError`. Windows and macOS refuse
   * either setting with `UnsupportedOperationError`, as does a system layer
   * without GNOME when it is requested. A concurrent restart with different
   * settings throws `ConflictError`. With a `resolution`, the SDK reads it
   * back as `start()` does; environment alone adds no read-back request.
   */
  public async restart(options: ComputerUseStartOptions = {}): Promise<ComputerUseRestartResponse> {
    return this.lifecycle.restart(options)
  }
}

export type { ComputerUseTransport }
