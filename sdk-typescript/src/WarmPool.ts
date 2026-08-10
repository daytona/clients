/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { WarmPoolsApi } from '@daytona/api-client'
import type { CreateWarmPool, UpdateWarmPool, WarmPool as WarmPoolDto } from '@daytona/api-client'
import { WithInstrumentation } from './utils/otel.decorator'

/**
 * Represents a Daytona Warm Pool which keeps ready-to-use Sandboxes for a snapshot.
 *
 * `currentSize` versus `pool` is the pool's status: `currentSize` is the number of ready
 * warm sandboxes, `pool` is the desired number. `errorReason` is set when the pool cannot
 * be filled.
 *
 * @property {string} id - Unique identifier for the Warm Pool
 * @property {string} organizationId - Organization ID that owns the Warm Pool
 * @property {string} snapshot - Snapshot the pool keeps warm sandboxes for
 * @property {string} target - Target region of the pool
 * @property {number} pool - Desired number of warm sandboxes
 * @property {number} currentSize - Current number of ready warm sandboxes in the pool
 * @property {number} cpu - CPU cores per sandbox
 * @property {number} mem - Memory per sandbox in GiB
 * @property {number} disk - Disk per sandbox in GiB
 * @property {string} osUser - OS user of the warm sandboxes
 * @property {Record<string, string>} env - Environment variables of the warm sandboxes
 * @property {string | null} [errorReason] - Reason the pool cannot be filled, if any
 * @property {string} createdAt - Date and time when the Warm Pool was created
 * @property {string} updatedAt - Date and time when the Warm Pool was last updated
 */
export type WarmPool = WarmPoolDto & { __brand: 'WarmPool' }

/**
 * Service for managing Daytona Warm Pools.
 *
 * This service provides methods to list, create, update, and delete Warm Pools.
 *
 * @class
 */
export class WarmPoolService {
  constructor(private warmPoolsApi: WarmPoolsApi) {}

  /**
   * Lists all Warm Pools in the organization.
   *
   * @returns {Promise<WarmPool[]>} List of all Warm Pools
   *
   * @example
   * const daytona = new Daytona();
   * const pools = await daytona.warmPool.list();
   * pools.forEach(pool => console.log(`${pool.snapshot}: ${pool.currentSize}/${pool.pool} ready`));
   */
  @WithInstrumentation()
  async list(): Promise<WarmPool[]> {
    const response = await this.warmPoolsApi.listWarmPools()
    return response.data as WarmPool[]
  }

  /**
   * Creates a new Warm Pool.
   *
   * @param {CreateWarmPool} params - Parameters for the new Warm Pool
   * @returns {Promise<WarmPool>} The newly created Warm Pool
   * @throws {DaytonaConflictError} If a Warm Pool for the same snapshot and region already exists
   *
   * @example
   * const daytona = new Daytona();
   * const pool = await daytona.warmPool.create({ snapshot: 'my-snapshot', pool: 5 });
   * console.log(`Created warm pool ${pool.id} in ${pool.target}`);
   */
  @WithInstrumentation()
  async create(params: CreateWarmPool): Promise<WarmPool> {
    const response = await this.warmPoolsApi.createWarmPool(params)
    return response.data as WarmPool
  }

  /**
   * Updates the desired size of a Warm Pool.
   *
   * @param {string} warmPoolId - ID of the Warm Pool to update
   * @param {UpdateWarmPool} params - Fields to update (`pool: 0` drains the pool)
   * @returns {Promise<WarmPool>} The updated Warm Pool
   * @throws {DaytonaNotFoundError} If the Warm Pool does not exist
   *
   * @example
   * const daytona = new Daytona();
   * const pool = await daytona.warmPool.update("warm-pool-id", { pool: 10 });
   */
  @WithInstrumentation()
  async update(warmPoolId: string, params: UpdateWarmPool): Promise<WarmPool> {
    const response = await this.warmPoolsApi.updateWarmPool(warmPoolId, params)
    return response.data as WarmPool
  }

  /**
   * Deletes a Warm Pool.
   *
   * @param {string} warmPoolId - ID of the Warm Pool to delete
   * @returns {Promise<void>}
   * @throws {DaytonaNotFoundError} If the Warm Pool does not exist
   *
   * @example
   * const daytona = new Daytona();
   * await daytona.warmPool.delete("warm-pool-id");
   */
  @WithInstrumentation()
  async delete(warmPoolId: string): Promise<void> {
    await this.warmPoolsApi.deleteWarmPool(warmPoolId)
  }
}
