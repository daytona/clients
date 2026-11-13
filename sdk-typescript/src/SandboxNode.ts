/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  Configuration,
  PortPreviewUrl,
  SandboxApi,
  SandboxNode as SandboxNodeDto,
  SshAccessDto,
} from '@daytona/api-client'
import { FileSystemApi, ProcessApi } from '@daytona/toolbox-api-client'
import { Daytona } from './Daytona'
import { FileSystem } from './FileSystem'
import { Process } from './Process'
import { DaytonaError, DaytonaInvalidArgumentError } from './errors/DaytonaError'

/** Lightweight data-plane handle for one node in a multi-node Sandbox. */
export class SandboxNode {
  private readonly clientConfig: Configuration
  private readonly axiosInstance: ReturnType<typeof Daytona.createAxiosInstance>
  public readonly rank: number
  public readonly id: string
  public cpu!: number
  public gpu!: number
  public memory!: number
  public disk!: number
  public state!: SandboxNodeDto['state']
  public incarnation!: number
  public failure?: SandboxNodeDto['failure']
  public readonly fs: FileSystem
  public readonly process: Process
  /** Advisory RDMA and session-cleanup warnings for this node. */
  public readonly warnings: string[] = []
  public fabricAddress?: string
  public fabricPrefixLength?: number
  public fabricHealthy?: boolean
  public fabricUnhealthySince?: string

  /**
   * @param node - Node data from the API response (rank, id, state and resources), not an SDK node handle.
   */
  constructor(
    node: SandboxNodeDto,
    toolboxProxyUrl: string,
    clientConfig: Configuration,
    private readonly sandboxApi: SandboxApi,
    language?: string,
    requestTimeoutMs?: number,
  ) {
    if (!node.id || node.id === '.' || node.id === '..') {
      throw new DaytonaInvalidArgumentError('Sandbox node id must be a non-empty path segment other than . or ..')
    }
    this.rank = node.rank
    this.id = node.id
    this.update(node)

    const nodeConfig = structuredClone(clientConfig)
    const nodeAxios = Daytona.createAxiosInstance(requestTimeoutMs)
    this.clientConfig = nodeConfig
    this.axiosInstance = nodeAxios
    this.updateToolboxProxyUrl(toolboxProxyUrl)
    const getPreviewToken = async () => (await this.getPreviewLink(1)).token

    this.fs = new FileSystem(nodeConfig, new FileSystemApi(nodeConfig, '', nodeAxios))
    this.process = new Process(
      nodeConfig,
      new ProcessApi(nodeConfig, '', nodeAxios),
      getPreviewToken,
      language,
      requestTimeoutMs,
    )
  }

  /**
   * Refresh health and address metadata without replacing the data-plane clients.
   * @param node - Node data from the API response, not an SDK node handle.
   * @throws {DaytonaError} If the node rank or id differs from this handle.
   */
  public update(node: SandboxNodeDto): void {
    if (node.rank !== this.rank || node.id !== this.id) {
      throw new DaytonaError(
        `Sandbox node identity changed from rank ${this.rank}/${this.id} to ${node.rank}/${node.id}`,
      )
    }
    this.cpu = node.cpu
    this.gpu = node.gpu
    this.memory = node.memory
    this.disk = node.disk
    this.state = node.state
    this.incarnation = node.incarnation
    this.failure = node.failure
    this.fabricAddress = node.fabricAddress
    this.fabricPrefixLength = node.fabricPrefixLength
    this.fabricHealthy = node.fabricHealthy
    this.fabricUnhealthySince = node.fabricUnhealthySince
  }

  /** @internal Refresh routing while preserving existing process and filesystem handles. */
  public updateToolboxProxyUrl(toolboxProxyUrl: string): void {
    const basePath = `${toolboxProxyUrl.replace(/\/+$/, '')}/${encodeURIComponent(this.id)}`
    this.clientConfig.basePath = basePath
    this.axiosInstance.defaults.baseURL = basePath
  }

  /** Get a preview URL for a port on this node. */
  public async getPreviewLink(port: number): Promise<PortPreviewUrl> {
    return (await this.sandboxApi.getPortPreviewUrl(this.id, port)).data
  }

  /** Create an SSH access token for this node. */
  public async createSshAccess(expiresInMinutes?: number): Promise<SshAccessDto> {
    return (await this.sandboxApi.createSshAccess(this.id, undefined, expiresInMinutes)).data
  }

  /** Revoke an SSH access token for this node. */
  public async revokeSshAccess(token: string): Promise<void> {
    await this.sandboxApi.revokeSshAccess(this.id, undefined, token)
  }
}
