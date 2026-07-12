/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { VolumesApi, VolumeType } from '@daytona/api-client'
import type { VolumeDto, VolumeMountTokenDto, HotmountRegion, Region } from '@daytona/api-client'
import { DaytonaNotFoundError } from './errors/DaytonaError'
import { WithInstrumentation } from './utils/otel.decorator'

export { VolumeType }
export type { VolumeMountTokenDto, HotmountRegion, Region }

/**
 * Options for creating a Volume.
 *
 * @property {VolumeType} [type] - The type of the Volume. Defaults to `VolumeType.LEGACY`.
 * @property {string} [region] - The region to create the Volume in. For `VolumeType.BLOCKMOUNT` volumes it
 * selects the region-local store the Volume's data lives in — a performance/placement knob, not an attach
 * restriction, so Sandboxes in any region can attach the Volume (colocation is just faster). Optional for
 * blockmount: when omitted it defaults to the organization's default region (or the first region offering
 * blockmount). For `VolumeType.HOTMOUNT` volumes it selects the hotmount deployment region (defaults to an
 * active region). Not allowed for legacy volumes. The Volume's region is fixed for its lifetime.
 */
export interface CreateVolumeOptions {
  type?: VolumeType
  region?: string
}

/**
 * Represents a Daytona Volume which is a shared storage volume for Sandboxes.
 *
 * @property {string} id - Unique identifier for the Volume
 * @property {string} name - Name of the Volume
 * @property {string} organizationId - Organization ID that owns the Volume
 * @property {string} state - Current state of the Volume
 * @property {string} createdAt - Date and time when the Volume was created
 * @property {string} updatedAt - Date and time when the Volume was last updated
 * @property {string} lastUsedAt - Date and time when the Volume was last used
 */
export type Volume = VolumeDto & { __brand: 'Volume' }

/**
 * Result of an explicit blockmount Volume pull into a running Sandbox.
 *
 * @property {string} volumeId - The Volume that was pulled
 * @property {string} [manifestId] - The merged manifest the Sandbox's scratch was advanced to
 * @property {boolean} upToDate - True when the Sandbox already reflected the latest merged state
 * @property {number} filesWritten - Files and symlinks written into the Sandbox by the pull
 * @property {number} deleted - Paths removed because they were deleted in the merged state
 * @property {number} skippedLocalNewer - Paths left untouched because the Sandbox has a strictly
 * newer local modification (the next commit's last-change-wins merge resolves them)
 * @property {number} bytesFetched - Content bytes downloaded from the store
 */
export interface VolumePullResult {
  volumeId: string
  manifestId?: string
  upToDate: boolean
  filesWritten: number
  deleted: number
  skippedLocalNewer: number
  bytesFetched: number
}

/**
 * Service for managing Daytona Volumes.
 *
 * This service provides methods to list, get, create, and delete Volumes.
 *
 * Volumes can be mounted to Sandboxes with an optional subpath parameter to mount
 * only a specific S3 prefix within the volume. When no subpath is specified,
 * the entire volume is mounted.
 *
 * @class
 */
export class VolumeService {
  constructor(private volumesApi: VolumesApi) {}

  /**
   * Lists all available Volumes.
   *
   * @returns {Promise<Volume[]>} List of all Volumes accessible to the user
   *
   * @example
   * const daytona = new Daytona();
   * const volumes = await daytona.volume.list();
   * console.log(`Found ${volumes.length} volumes`);
   * volumes.forEach(vol => console.log(`${vol.name} (${vol.id})`));
   */
  async list(): Promise<Volume[]> {
    const response = await this.volumesApi.listVolumes()
    return response.data as Volume[]
  }

  /**
   * Gets a Volume by its name.
   *
   * @param {string} name - Name of the Volume to retrieve
   * @param {boolean} create - Whether to create the Volume if it does not exist
   * @param {CreateVolumeOptions} [options] - Options used when creating the Volume (only applied if `create` is true)
   * @returns {Promise<Volume>} The requested Volume
   * @throws {Error} If the Volume does not exist or cannot be accessed
   *
   * @example
   * const daytona = new Daytona();
   * const volume = await daytona.volume.get("volume-name", true);
   * console.log(`Volume ${volume.name} is in state ${volume.state}`);
   */
  @WithInstrumentation()
  async get(name: string, create = false, options: CreateVolumeOptions = {}): Promise<Volume> {
    try {
      const response = await this.volumesApi.getVolumeByName(name)
      return response.data as Volume
    } catch (error) {
      if (error instanceof DaytonaNotFoundError && create) {
        return await this.create(name, options)
      }
      throw error
    }
  }

  /**
   * Creates a new Volume with the specified name.
   *
   * @param {string} name - Name for the new Volume
   * @param {CreateVolumeOptions} [options] - Options for the new Volume, such as its type and size
   * @returns {Promise<Volume>} The newly created Volume
   * @throws {Error} If the Volume cannot be created
   *
   * @example
   * const daytona = new Daytona();
   * const volume = await daytona.volume.create("my-data-volume");
   * console.log(`Created volume ${volume.name} with ID ${volume.id}`);
   *
   * @example
   * // Create a shared high-performance volume (local-first, reconciled through S3)
   * const volume = await daytona.volume.create("shared-cache", { type: VolumeType.BLOCKMOUNT });
   */
  @WithInstrumentation()
  async create(name: string, options: CreateVolumeOptions = {}): Promise<Volume> {
    const response = await this.volumesApi.createVolume({
      name,
      type: options.type,
      region: options.region,
    })
    return response.data as Volume
  }

  /**
   * Lists the hotmount regions available for volume creation.
   *
   * @returns {Promise<HotmountRegion[]>} The active hotmount regions (id, label, geo)
   *
   * @example
   * const daytona = new Daytona();
   * const regions = await daytona.volume.listHotmountRegions();
   * regions.forEach(r => console.log(`${r.region} - ${r.label}`));
   */
  @WithInstrumentation()
  async listHotmountRegions(): Promise<HotmountRegion[]> {
    const response = await this.volumesApi.listHotmountRegions()
    return response.data
  }

  /**
   * Lists the regions where blockmount Volumes can be created.
   *
   * A blockmount Volume's data lives in the region it is created in (a performance/placement knob —
   * Sandboxes in any region can attach it, colocation is just faster). Only regions a superadmin has
   * enabled for blockmount are returned.
   *
   * @returns {Promise<Region[]>} The regions that support blockmount volumes
   *
   * @example
   * const daytona = new Daytona();
   * const regions = await daytona.volume.listBlockmountRegions();
   * regions.forEach(r => console.log(`${r.id} - ${r.name}`));
   */
  @WithInstrumentation()
  async listBlockmountRegions(): Promise<Region[]> {
    const response = await this.volumesApi.listBlockmountRegions()
    return response.data
  }

  /**
   * Creates a short-lived mount token for a hotmount Volume.
   *
   * The token, together with the returned region gateway/binaries endpoints, is used to bootstrap
   * the hotmount agent (inside a Sandbox or on customer infrastructure) and mount the Volume on the
   * fly. Only `VolumeType.HOTMOUNT` volumes support this.
   *
   * @param {Volume} volume - The hotmount Volume to obtain a mount token for
   * @returns {Promise<VolumeMountTokenDto>} The mount token, region endpoints, and expiration
   * @throws {Error} If the Volume is not a hotmount volume, is not ready, or cannot be accessed
   *
   * @example
   * const daytona = new Daytona();
   * const volume = await daytona.volume.get("shared-fs");
   * const token = await daytona.volume.getMountToken(volume);
   */
  @WithInstrumentation()
  async getMountToken(volume: Volume): Promise<VolumeMountTokenDto> {
    const response = await this.volumesApi.createVolumeMountToken(volume.id)
    return response.data
  }

  /**
   * Deletes a Volume.
   *
   * @param {Volume} volume - Volume to delete
   * @returns {Promise<void>}
   * @throws {Error} If the Volume does not exist or cannot be deleted
   *
   * @example
   * const daytona = new Daytona();
   * const volume = await daytona.volume.get("volume-name");
   * await daytona.volume.delete(volume);
   * console.log("Volume deleted successfully");
   */
  @WithInstrumentation()
  async delete(volume: Volume): Promise<void> {
    await this.volumesApi.deleteVolume(volume.id)
  }
}
