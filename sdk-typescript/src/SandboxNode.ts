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

const RDMA_USERSPACE_CHECK =
  'sh -lc \'ldconfig -p 2>/dev/null | grep -q libibverbs.so.1 && find /usr/lib /lib -name "libmlx5-rdmav*.so" -print -quit 2>/dev/null | grep -q .\''
const RDMA_USERSPACE_WARNING =
  'WARNING: rdma-core userspace (libibverbs.so.1 and an mlx5 provider) is missing; NCCL may fall back to sockets.'

const probes = new WeakMap<SandboxNode, Promise<void>>()
const probeFailedWarning = 'WARNING: Could not check RDMA userspace; continuing without verification.'

/** @internal */
export function checkNodeRdma(node: SandboxNode): Promise<void> {
  const cached = probes.get(node)
  if (cached) return cached
  const incarnation = node.incarnation
  const probe = (async () => {
    let warning: string
    try {
      const check = await node.process.executeCommand(RDMA_USERSPACE_CHECK, undefined, undefined, 5)
      warning = check.exitCode === 0 ? '' : RDMA_USERSPACE_WARNING
    } catch {
      warning = probeFailedWarning
    }
    if (node.incarnation === incarnation && warning && !node.warnings.includes(warning)) node.warnings.push(warning)
  })()
  probes.set(node, probe)
  return probe
}

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
  /** Advisory RDMA warnings for this node. */
  public readonly warnings: string[] = []
  public fabricAddress?: string
  public fabricPrefixLength?: number
  public fabricHealthy?: boolean
  public fabricUnhealthySince?: string

  /**
   * @param node - Rank, ID, state and resources returned by the API.
   * @throws {DaytonaInvalidArgumentError} If the node ID is empty, "." or "..".
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
   * Refresh resources, state, incarnation, failure details and fabric metadata.
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
    if (this.incarnation !== node.incarnation && probes.delete(this)) {
      for (const warning of [RDMA_USERSPACE_WARNING, probeFailedWarning]) {
        const index = this.warnings.indexOf(warning)
        if (index >= 0) this.warnings.splice(index, 1)
      }
    }
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
