/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Construction of the SDK's handles and services is internal: their
 * constructors take {@link INTERNAL} first. This module is not reachable
 * through the package `exports`, so callers cannot obtain the token and get
 * these objects from the client (`daytona.v2.images`, `daytona.v2.create()`,
 * ...) or the sandbox (`sandbox.process`, `sandbox.process.connect(...)`, ...)
 * instead.
 */
export const INTERNAL: unique symbol = Symbol('@daytona/sdk/internal-construction')

/** The type of {@link INTERNAL}. */
export type Internal = typeof INTERNAL

/** Refuse a construction without the token (JavaScript callers, casts). */
export function assertInternal(token: unknown, className: string): void {
  if (token !== INTERNAL) {
    throw new TypeError(`${className} is created by the SDK; get it from the client or a sandbox instead of constructing it`)
  }
}
