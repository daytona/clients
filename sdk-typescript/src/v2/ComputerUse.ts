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

export class ComputerUseService {
  public readonly service: AddonProcessesService
  public readonly mouse: MouseService
  public readonly keyboard: KeyboardService
  public readonly screen: ScreenService
  public readonly display: DisplayService
  public readonly a11y: A11yService
  public readonly recording: RecordingService

  private readonly lifecycle: ComputerUseLifecycleService

  constructor(transport: ComputerUseTransport) {
    this.lifecycle = new ComputerUseLifecycleService(transport)
    this.service = new AddonProcessesService(transport)
    this.mouse = new MouseService(transport)
    this.keyboard = new KeyboardService(transport)
    this.screen = new ScreenService(transport)
    this.display = new DisplayService(transport)
    this.a11y = new A11yService(transport)
    this.recording = new RecordingService(transport)
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
