/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked by a Node-only consumer (ESM; node-only.cts is the CommonJS
// half): lib es2022 with no DOM, the Node types, skipLibCheck: false. No
// published declaration may name a DOM-only type (AddEventListenerOptions,
// BodyInit, DOMException, ...).
import { Daytona, DaytonaNotFoundError } from '@daytona/sdk'
import * as v2 from '@daytona/sdk/v2'

export const client = new Daytona({ apiKey: 'smoke' })
export async function sandbox(): Promise<v2.Sandbox> {
  return client.v2.create('python:3.12')
}
export const errors = [DaytonaNotFoundError, v2.NotFoundError, v2.ConnectionError]
