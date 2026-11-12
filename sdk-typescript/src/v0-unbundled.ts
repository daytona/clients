/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Under the private names the bundle gives v0's client classes. */
export type RootOnlyConfigV0 = {
  readonly apiKey?: string
  readonly jwtToken?: string
  readonly organizationId?: string
  readonly target?: string
  readonly apiUrl?: string
}

const V0_UNBUNDLED = 'The v0 namespace is not included in this build of @daytona/sdk'

/** Marks this stub when an unbundled build emits it as `./v0`: the root's
 * `d.v0` then throws its 501 `NotSupportedError` instead of constructing it. */
export const __daytonaV0Unbundled = true

function unavailable(): never {
  throw new Error(V0_UNBUNDLED)
}

// The shapes below mirror v0.231.1 closely enough for the root's
// types: `list()` is synchronous and returns an async iterator; the nested
// services' methods are async.
class V0SnapshotNamespace {
  public async list(): Promise<readonly unknown[]> {
    unavailable()
  }
}

class V0VolumeNamespace {
  public async get(_name: string): Promise<unknown> {
    unavailable()
  }
}

/** The fields of v0's Sandbox the stub exposes for type-checking. */
export type V0SandboxStub = { readonly id: string; readonly [field: string]: unknown }

export class RootOnlyClientV0 {
  public readonly snapshot = new V0SnapshotNamespace()
  public readonly volume = new V0VolumeNamespace()

  constructor(_config: RootOnlyConfigV0 = {}) {
    unavailable()
  }

  // Items carry `id` like v0's Sandbox, so examples that
  // read `s.id` type-check in a source checkout too.
  public list(_query?: { readonly labels?: Record<string, string> }): AsyncIterableIterator<V0SandboxStub> {
    unavailable()
  }
}
