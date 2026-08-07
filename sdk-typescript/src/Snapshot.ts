/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ObjectStorageApi, SnapshotsApi, SnapshotState, SandboxClass, Configuration } from '@daytona/api-client'
import type { SnapshotDto, CreateSnapshot, PaginatedSnapshots as PaginatedSnapshotsDto } from '@daytona/api-client'
import { DaytonaError, DaytonaNotFoundError } from './errors/DaytonaError'
import { Image } from './Image'
import type { Resources } from './Daytona'
import { processStreamingResponse } from './utils/Stream'
import { dynamicImport } from './utils/Import'
import { WithInstrumentation } from './utils/otel.decorator'

/**
 * Represents a Daytona Snapshot which is a pre-configured sandbox.
 *
 * @property {string} id - Unique identifier for the Snapshot.
 * @property {string} organizationId - Organization ID that owns the Snapshot.
 * @property {boolean} general - Whether the Snapshot is general.
 * @property {string} name - Name of the Snapshot.
 * @property {string} imageName - Name of the Image of the Snapshot.
 * @property {SnapshotState} state - Current state of the Snapshot.
 * @property {number} size - Size of the Snapshot.
 * @property {string[]} entrypoint - Entrypoint of the Snapshot.
 * @property {number} cpu - CPU of the Snapshot.
 * @property {number} gpu - GPU of the Snapshot.
 * @property {number} mem - Memory of the Snapshot in GiB.
 * @property {number} disk - Disk of the Snapshot in GiB.
 * @property {string} errorReason - Error reason of the Snapshot.
 * @property {Date} createdAt - Timestamp when the Snapshot was created.
 * @property {Date} updatedAt - Timestamp when the Snapshot was last updated.
 * @property {Date} lastUsedAt - Timestamp when the Snapshot was last used.
 */
export type Snapshot = SnapshotDto & { __brand: 'Snapshot' }

/**
 * Represents a paginated list of Daytona Snapshots.
 *
 * @property {Snapshot[]} items - List of Snapshot instances in the current page.
 * @property {number} total - Total number of Snapshots across all pages.
 * @property {number} page - Current page number.
 * @property {number} totalPages - Total number of pages available.
 */
export interface PaginatedSnapshots extends Omit<PaginatedSnapshotsDto, 'items'> {
  items: Snapshot[]
}

/**
 * Parameters for creating a new snapshot.
 *
 * @property {string} name - Name of the snapshot.
 * @property {string | Image} image - Image of the snapshot. If a string is provided, it should be available on some registry.
 * If an Image instance is provided, it will be used to create a new image in Daytona.
 * @property {Resources} resources - Resources of the snapshot.
 * @property {string[]} entrypoint - Entrypoint of the snapshot.
 * @property {string} regionId - ID of the region where the snapshot will be available. Defaults to organization default region if not specified.
 * @property {SandboxClass} sandboxClass - Target sandbox class. Determines which runners can host sandboxes created from this snapshot.
 */
export type CreateSnapshotParams = {
  name: string
  image: string | Image
  resources?: Resources
  entrypoint?: string[]
  regionId?: string
  sandboxClass?: SandboxClass
}

/**
 * Matches RFC 4122 UUIDs (versions 1-5) and the nil UUID — the same set the
 * Daytona API recognizes as snapshot IDs. Anything else is treated as a name.
 */
const UUID_REGEX =
  /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000)$/i

/**
 * Service for managing Daytona Snapshots. Can be used to list, get, create and delete Snapshots.
 *
 * @class
 */
export class SnapshotService {
  constructor(
    private clientConfig: Configuration,
    private snapshotsApi: SnapshotsApi,
    private objectStorageApi: ObjectStorageApi,
    private defaultRegionId?: string,
  ) {}

  /**
   * List paginated list of Snapshots.
   *
   * @param {number} [page] - Page number for pagination (starting from 1)
   * @param {number} [limit] - Maximum number of items per page
   * @returns {Promise<PaginatedSnapshots>} Paginated list of Snapshots
   *
   * @example
   * const daytona = new Daytona();
   * const { items, total, page: currentPage, totalPages } = await daytona.snapshot.list(2, 10);
   * console.log(`Page ${currentPage} of ${totalPages} (${total} snapshots total)`);
   * items.forEach(snapshot => console.log(`${snapshot.name} (${snapshot.imageName})`));
   */
  @WithInstrumentation()
  async list(page?: number, limit?: number): Promise<PaginatedSnapshots> {
    const response = await this.snapshotsApi.getAllSnapshots(undefined, page, limit)
    return {
      items: response.data.items.map((snapshot) => snapshot as Snapshot),
      total: response.data.total,
      page: response.data.page,
      totalPages: response.data.totalPages,
    }
  }

  /**
   * Gets a Snapshot by its ID or name.
   *
   * @param {string} idOrName - ID or name of the Snapshot to retrieve
   * @returns {Promise<Snapshot>} The requested Snapshot
   * @throws {Error} If the Snapshot does not exist or cannot be accessed
   *
   * @example
   * const daytona = new Daytona();
   * const snapshot = await daytona.snapshot.get("snapshot-name");
   * console.log(`Snapshot ${snapshot.name} is in state ${snapshot.state}`);
   */
  @WithInstrumentation()
  async get(idOrName: string): Promise<Snapshot> {
    const response = await this.snapshotsApi.getSnapshot(idOrName)
    return response.data as Snapshot
  }

  /**
   * Deletes a Snapshot.
   *
   * @param {Snapshot | string} snapshot - Snapshot to delete, or its ID or name
   * @returns {Promise<void>}
   * @throws {Error} If the Snapshot does not exist or cannot be deleted
   *
   * @example
   * const daytona = new Daytona();
   * await daytona.snapshot.delete("snapshot-name");
   * console.log("Snapshot deleted successfully");
   */
  @WithInstrumentation()
  async delete(snapshot: Snapshot | string): Promise<void> {
    await this.callWithResolvedId(snapshot, async (id) => this.snapshotsApi.removeSnapshot(id))
  }

  /**
   * Creates and registers a new snapshot from the given Image definition.
   *
   * @param {CreateSnapshotParams} params - Parameters for snapshot creation.
   * @param {object} options - Options for the create operation.
   * @param {boolean} options.onLogs - This callback function handles snapshot creation logs.
   * @param {number} options.timeout - Default is no timeout. Timeout in seconds (0 means no timeout).
   * @returns {Promise<void>}
   *
   * @example
   * const image = Image.debianSlim('3.12').pipInstall('numpy');
   * await daytona.snapshot.create({ name: 'my-snapshot', image: image }, { onLogs: console.log });
   */
  @WithInstrumentation()
  public async create(
    params: CreateSnapshotParams,
    options: { onLogs?: (chunk: string) => void; timeout?: number } = {},
  ): Promise<Snapshot> {
    const createSnapshotReq: CreateSnapshot = {
      name: params.name,
    }

    if (typeof params.image === 'string') {
      createSnapshotReq.imageName = params.image
      createSnapshotReq.entrypoint = params.entrypoint
    } else {
      const contextHashes = await SnapshotService.processImageContext(this.objectStorageApi, params.image)
      createSnapshotReq.buildInfo = {
        contextHashes,
        dockerfileContent: params.entrypoint
          ? params.image.entrypoint(params.entrypoint).dockerfile
          : params.image.dockerfile,
      }
    }

    if (params.resources) {
      createSnapshotReq.cpu = params.resources.cpu
      createSnapshotReq.gpu = params.resources.gpu
      if (params.resources.gpuType !== undefined) {
        createSnapshotReq.gpuType = Array.isArray(params.resources.gpuType)
          ? params.resources.gpuType
          : [params.resources.gpuType]
      }
      createSnapshotReq.memory = params.resources.memory
      createSnapshotReq.disk = params.resources.disk
    }

    createSnapshotReq.regionId = params.regionId || this.defaultRegionId
    createSnapshotReq.sandboxClass = params.sandboxClass

    let createdSnapshot = (
      await this.snapshotsApi.createSnapshot(createSnapshotReq, undefined, {
        timeout: (options.timeout || 0) * 1000,
      })
    ).data

    if (!createdSnapshot) {
      throw new DaytonaError("Failed to create snapshot. Didn't receive a snapshot from the server API.")
    }

    const terminalStates: SnapshotState[] = [SnapshotState.ACTIVE, SnapshotState.ERROR, SnapshotState.BUILD_FAILED]
    const snapshotRef = { createdSnapshot: createdSnapshot }
    let streamPromise: Promise<void> | undefined
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    const startLogStreaming = async (onChunk: (chunk: string) => void = () => {}) => {
      if (!streamPromise) {
        const response = await this.snapshotsApi.getSnapshotBuildLogsUrl(createdSnapshot.id)

        const url = `${response.data.url}?follow=true`

        streamPromise = processStreamingResponse(
          () => fetch(url, { method: 'GET', headers: this.clientConfig.baseOptions.headers }),
          (chunk) => onChunk(chunk.trimEnd()),
          async () => terminalStates.includes(snapshotRef.createdSnapshot.state),
        )
      }
    }

    if (options.onLogs) {
      options.onLogs(`Creating snapshot ${createdSnapshot.name} (${createdSnapshot.state})`)

      if (
        createSnapshotReq.buildInfo &&
        createdSnapshot.state !== SnapshotState.PENDING &&
        !terminalStates.includes(createdSnapshot.state)
      ) {
        await startLogStreaming(options.onLogs)
      }
    }

    let previousState = createdSnapshot.state
    while (!terminalStates.includes(createdSnapshot.state)) {
      if (options.onLogs && previousState !== createdSnapshot.state) {
        if (createSnapshotReq.buildInfo && createdSnapshot.state !== SnapshotState.PENDING && !streamPromise) {
          await startLogStreaming(options.onLogs)
        }
        options.onLogs(`Creating snapshot ${createdSnapshot.name} (${createdSnapshot.state})`)
        previousState = createdSnapshot.state
      }
      await new Promise((resolve) => setTimeout(resolve, 1000))
      createdSnapshot = await this.get(createdSnapshot.name)
      snapshotRef.createdSnapshot = createdSnapshot
    }

    if (options.onLogs) {
      if (streamPromise) {
        await streamPromise
      }
      if (createdSnapshot.state === SnapshotState.ACTIVE) {
        options.onLogs(`Created snapshot ${createdSnapshot.name} (${createdSnapshot.state})`)
      }
    }

    if (createdSnapshot.state === SnapshotState.ERROR || createdSnapshot.state === SnapshotState.BUILD_FAILED) {
      const errMsg = `Failed to create snapshot. Name: ${createdSnapshot.name} Reason: ${createdSnapshot.errorReason}`
      throw new DaytonaError(errMsg)
    }

    return createdSnapshot as Snapshot
  }

  /**
   * Activates a snapshot.
   *
   * @param {Snapshot | string} snapshot - Snapshot to activate, or its ID or name
   * @returns {Promise<Snapshot>} The activated Snapshot instance
   */
  @WithInstrumentation()
  async activate(snapshot: Snapshot | string): Promise<Snapshot> {
    return await this.callWithResolvedId(
      snapshot,
      async (id) => (await this.snapshotsApi.activateSnapshot(id)).data as Snapshot,
    )
  }

  /**
   * Invokes an ID-based Snapshot operation, resolving the given identifier with as few
   * API calls as possible.
   *
   * Snapshot names may themselves be UUID-formatted, so a UUID-shaped string is first
   * tried directly as an ID (single call) and only resolved through the API on a miss.
   * Everything else is a name and requires one resolution call.
   *
   * @param {Snapshot | string} snapshot - Snapshot instance, ID or name
   * @param {(id: string) => Promise<T>} operation - ID-based operation to invoke
   */
  private async callWithResolvedId<T>(snapshot: Snapshot | string, operation: (id: string) => Promise<T>): Promise<T> {
    if (typeof snapshot !== 'string') {
      return await operation(snapshot.id)
    }

    if (UUID_REGEX.test(snapshot)) {
      try {
        return await operation(snapshot)
      } catch (error) {
        if (!(error instanceof DaytonaNotFoundError)) {
          throw error
        }
        // Not an existing ID — may still be a UUID-formatted name; fall through to resolution.
      }
    }

    const resolved = await this.snapshotsApi.getSnapshot(snapshot)
    return await operation(resolved.data.id)
  }

  /**
   * Processes the image contexts by uploading them to object storage
   *
   * @private
   * @param {Image} image - The Image instance.
   * @returns {Promise<string[]>} The list of context hashes stored in object storage.
   */
  @WithInstrumentation()
  static async processImageContext(objectStorageApi: ObjectStorageApi, image: Image): Promise<string[]> {
    if (!image.contextList || !image.contextList.length) {
      return []
    }

    const ObjectStorageModule = await dynamicImport('ObjectStorage', '"processImageContext" is not supported: ')
    const pushAccessCreds = (await objectStorageApi.getPushAccess()).data
    const objectStorage = new ObjectStorageModule.ObjectStorage({
      endpointUrl: pushAccessCreds.storageUrl,
      accessKeyId: pushAccessCreds.accessKey,
      secretAccessKey: pushAccessCreds.secret,
      sessionToken: pushAccessCreds.sessionToken,
      bucketName: pushAccessCreds.bucket,
      region: pushAccessCreds.region,
    })

    const contextHashes = []
    for (const context of image.contextList) {
      const contextHash = await objectStorage.upload(
        context.sourcePath,
        pushAccessCreds.organizationId,
        context.archivePath,
      )
      contextHashes.push(contextHash)
    }

    return contextHashes
  }
}
