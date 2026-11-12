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

  public async status(): Promise<ComputerUseStatusResponse> {
    return this.lifecycle.status()
  }

  /**
   * Start the desktop. `resolution` selects its size (1024x768, 1280x720 or
   * 1920x1080) and is kept for later starts and restarts; omitted, the desktop
   * uses the resolution it last started at, or 1280x720. Starting a running
   * desktop at a different resolution throws `ConflictError` (restart with it
   * instead). Linux sandboxes only: other platforms throw
   * `UnsupportedOperationError` for a `resolution`. With a `resolution`, the
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
   * Restart the desktop, at `resolution` when given (kept for later starts);
   * omitted, at its current resolution. Linux sandboxes only: other platforms
   * throw `UnsupportedOperationError` for a `resolution`. With a `resolution`,
   * the SDK reads it back as `start()` does.
   */
  public async restart(options: ComputerUseStartOptions = {}): Promise<ComputerUseRestartResponse> {
    return this.lifecycle.restart(options)
  }
}

export type { ComputerUseTransport }
