/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  Daytona as DaytonaV1,
  type CreateSandboxFromImageParams,
  type CreateSandboxFromSnapshotParams,
  type DaytonaConfig,
} from './Daytona'
import { warnRootNamespaceDeprecation } from './deprecation'

/**
 * The SDK 1.x root client.
 *
 * It owns one v1 client, returned by `v1` (the same instance on every access); use
 * `daytona.v1.<member>`, the form SDK 2.x keeps. `close()` and `await using` are not deprecated
 * (the SDK 2.x root has both).
 * Every other member forwards to `v1` and is deprecated: the first use in a process emits one
 * `DeprecationWarning` (code `DAYTONA_ROOT_NAMESPACE`).
 *
 * @example
 * import { Daytona } from '@daytona/sdk'
 *
 * const daytona = new Daytona()
 * const sandbox = await daytona.v1.create()
 */
export class Daytona implements AsyncDisposable {
  private readonly client: DaytonaV1

  /** Creates the root client and its v1 client from `config` (or the environment). */
  constructor(config?: DaytonaConfig) {
    this.client = new DaytonaV1(config)
  }

  /** The v1 client (`Daytona` from `@daytona/sdk/v1`); the same instance on every access. */
  public get v1(): DaytonaV1 {
    return this.client
  }

  /** Closes the v1 client. Not deprecated. */
  public close(): Promise<void> {
    return this.client.close()
  }

  /** Closes the v1 client, for `await using`. Not deprecated. */
  public [Symbol.asyncDispose](): Promise<void> {
    return this.close()
  }

  /** @deprecated Use `daytona.v1.volume` instead. The root form is removed in SDK 2.x. */
  public get volume(): DaytonaV1['volume'] {
    warnRootNamespaceDeprecation('volume')
    return this.client.volume
  }

  /** @deprecated Use `daytona.v1.snapshot` instead. The root form is removed in SDK 2.x. */
  public get snapshot(): DaytonaV1['snapshot'] {
    warnRootNamespaceDeprecation('snapshot')
    return this.client.snapshot
  }

  /** @deprecated Use `daytona.v1.secret` instead. The root form is removed in SDK 2.x. */
  public get secret(): DaytonaV1['secret'] {
    warnRootNamespaceDeprecation('secret')
    return this.client.secret
  }

  /** @deprecated Use `daytona.v1.warmPool` instead. The root form is removed in SDK 2.x. */
  public get warmPool(): DaytonaV1['warmPool'] {
    warnRootNamespaceDeprecation('warmPool')
    return this.client.warmPool
  }

  /**
   * @deprecated Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.
   */
  public create(
    params?: CreateSandboxFromSnapshotParams,
    options?: { timeout?: number },
  ): ReturnType<DaytonaV1['create']>
  /**
   * @deprecated Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.
   */
  public create(
    params?: CreateSandboxFromImageParams,
    options?: { onSnapshotCreateLogs?: (chunk: string) => void; timeout?: number },
  ): ReturnType<DaytonaV1['create']>
  public create(
    params?: CreateSandboxFromSnapshotParams | CreateSandboxFromImageParams,
    options?: { onSnapshotCreateLogs?: (chunk: string) => void; timeout?: number },
  ): ReturnType<DaytonaV1['create']> {
    warnRootNamespaceDeprecation('create')
    if (params && 'image' in params) {
      return this.client.create(params, options)
    }

    return this.client.create(params, options)
  }

  /** @deprecated Use `daytona.v1.get` instead. The root form is removed in SDK 2.x. */
  public get(...args: Parameters<DaytonaV1['get']>): ReturnType<DaytonaV1['get']> {
    warnRootNamespaceDeprecation('get')
    return this.client.get(...args)
  }

  /** @deprecated Use `daytona.v1.list` instead. The root form is removed in SDK 2.x. */
  public list(...args: Parameters<DaytonaV1['list']>): ReturnType<DaytonaV1['list']> {
    warnRootNamespaceDeprecation('list')
    return this.client.list(...args)
  }

  /** @deprecated Use `daytona.v1.start` instead. The root form is removed in SDK 2.x. */
  public start(...args: Parameters<DaytonaV1['start']>): ReturnType<DaytonaV1['start']> {
    warnRootNamespaceDeprecation('start')
    return this.client.start(...args)
  }

  /** @deprecated Use `daytona.v1.stop` instead. The root form is removed in SDK 2.x. */
  public stop(...args: Parameters<DaytonaV1['stop']>): ReturnType<DaytonaV1['stop']> {
    warnRootNamespaceDeprecation('stop')
    return this.client.stop(...args)
  }

  /** @deprecated Use `daytona.v1.fork` instead. The root form is removed in SDK 2.x. */
  public fork(...args: Parameters<DaytonaV1['fork']>): ReturnType<DaytonaV1['fork']> {
    warnRootNamespaceDeprecation('fork')
    return this.client.fork(...args)
  }

  /**
   * @deprecated Use `daytona.v1.fork` instead. The root form is removed in SDK 2.x.
   */
  public _experimental_fork(
    ...args: Parameters<DaytonaV1['_experimental_fork']>
  ): ReturnType<DaytonaV1['_experimental_fork']> {
    warnRootNamespaceDeprecation('_experimental_fork', 'daytona.v1.fork')
    return this.client._experimental_fork(...args)
  }

  /** @deprecated Use `daytona.v1.delete` instead. The root form is removed in SDK 2.x. */
  public delete(...args: Parameters<DaytonaV1['delete']>): ReturnType<DaytonaV1['delete']> {
    warnRootNamespaceDeprecation('delete')
    return this.client.delete(...args)
  }

  /**
   * @deprecated Use `Daytona.createAxiosInstance` from `@daytona/sdk/v1` instead. The root form is removed in SDK 2.x.
   */
  public static createAxiosInstance(
    ...args: Parameters<typeof DaytonaV1.createAxiosInstance>
  ): ReturnType<typeof DaytonaV1.createAxiosInstance> {
    warnRootNamespaceDeprecation('createAxiosInstance', '@daytona/sdk/v1 Daytona.createAxiosInstance')
    return DaytonaV1.createAxiosInstance(...args)
  }
}
