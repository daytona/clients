/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { Sandbox } from '../../src/v2/Sandbox'
import type { SandboxConnectOptionsFromCreate } from '../../src/v2/Sandbox'
import type { WaitHub } from '../../src/v2/events'
import type { HttpClientOptions } from '../../src/v2/http'
import type { SandboxDto, SandboxInfo } from '../../src/v2/types'
import { INTERNAL } from '../../src/v2/internal'

/** Legacy positional fixture adapter; production construction uses one init object. */
export function createSandbox(
  sandbox: SandboxDto | SandboxInfo | string,
  api?: AxiosInstance,
  _toolboxBaseUrl?: string,
  apiKey?: string,
  _target?: string,
  options?: SandboxConnectOptionsFromCreate,
  requestTimeout?: number,
  httpOptions?: HttpClientOptions,
  hub?: WaitHub,
): Sandbox {
  return new Sandbox(INTERNAL, { sandbox, api, apiKey, options, requestTimeout, httpOptions, hub })
}
