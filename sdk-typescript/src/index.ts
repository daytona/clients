/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/// <reference lib="esnext.disposable" preserve="true" />
// The clients implement [Symbol.asyncDispose]: carry the lib with the published
// declarations so strict consumers (skipLibCheck: false, lib es2022) resolve it.
export { DEFAULT_V2_API_URL, Daytona, V0_API_URL } from './Daytona.js'
export type { DaytonaV0Client, DaytonaV2Client } from './Daytona.js'
export type { DaytonaConfig } from './types.js'
export {
  DaytonaError,
  DaytonaBadRequestError,
  DaytonaAuthenticationError,
  DaytonaForbiddenError,
  DaytonaNotFoundError,
  DaytonaTimeoutError,
  DaytonaConflictError,
  DaytonaGoneError,
  DaytonaUnprocessableEntityError,
  DaytonaRateLimitError,
  DaytonaInternalServerError,
  DaytonaServiceUnavailableError,
  DaytonaConnectionError,
} from './errors.js'
