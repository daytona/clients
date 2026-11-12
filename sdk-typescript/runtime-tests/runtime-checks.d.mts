/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

export type RuntimeCheckResult = {
  listOk: boolean
  notFoundOk: boolean
  toolboxListOk: boolean
  toolboxWatchOk: boolean
  capOk: boolean
  /** `bundled`, `unbundled`, or what went wrong. */
  v0: string
  [detail: string]: unknown
}

/**
 * The parts of the `@daytona/sdk` module the checks use. Structural, so both
 * the ES module and the CommonJS declarations satisfy it.
 */
export type SdkModule = {
  readonly Daytona: new (config: { readonly apiKey?: string; readonly maxResponseBytes?: number; readonly requestTimeoutSeconds?: number }) => {
    readonly v2: { list(): Promise<{ readonly items: readonly object[] }>; get(id: string): Promise<object> }
    readonly v0: object
    close(): Promise<void>
  }
  readonly DaytonaNotFoundError: abstract new (...args: never[]) => Error
}

export declare function runChecks(sdk: SdkModule, options: { readonly apiKey: string | undefined }): Promise<RuntimeCheckResult>
