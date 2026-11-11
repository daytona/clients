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

/** Lightweight data-plane handle for one node in a multi-node Sandbox. */
export class SandboxNode {
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
  public readonly warnings: string[] = []
  public fabricAddress?: string
  public fabricPrefixLength?: number
  public fabricHealthy?: boolean
  public fabricUnhealthySince?: string

  constructor(
    node: SandboxNodeDto,
    toolboxProxyUrl: string,
    clientConfig: Configuration,
    private readonly sandboxApi: SandboxApi,
    language?: string,
    requestTimeoutMs?: number,
  ) {
    this.rank = node.rank
    this.id = node.id
    this.update(node)

    const nodeConfig = structuredClone(clientConfig)
    const nodeAxios = Daytona.createAxiosInstance(requestTimeoutMs)
    const basePath = `${toolboxProxyUrl.replace(/\/+$/, '')}/${this.id}`
    nodeConfig.basePath = basePath
    nodeAxios.defaults.baseURL = basePath
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

  /** Refresh health and address metadata without replacing the data-plane clients. */
  public update(node: SandboxNodeDto): void {
    if (node.rank !== this.rank || node.id !== this.id) {
      throw new Error(`Sandbox node identity changed from rank ${this.rank}/${this.id} to ${node.rank}/${node.id}`)
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
