/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  ComputerUseLifecycleResponse,
  ComputerUseRestartResponse,
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

  public async start(): Promise<ComputerUseLifecycleResponse> {
    return this.lifecycle.start()
  }

  public async stop(): Promise<ComputerUseLifecycleResponse> {
    return this.lifecycle.stop()
  }

  public async restart(): Promise<ComputerUseRestartResponse> {
    return this.lifecycle.restart()
  }
}

export type { ComputerUseTransport }
