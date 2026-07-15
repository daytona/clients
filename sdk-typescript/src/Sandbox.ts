/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { SandboxState, SandboxApi, SandboxBackupStateEnum, Configuration } from '@daytona/api-client'
import {
  TelemetryApi as AnalyticsTelemetryApi,
  Configuration as AnalyticsConfiguration,
} from '@daytona/analytics-api-client'
import type { ModelsMetricPoint } from '@daytona/analytics-api-client'
import type {
  Sandbox as SandboxDto,
  SandboxListItem as SandboxListItemDto,
  PortPreviewUrl,
  SandboxVolume,
  BuildInfo,
  SshAccessDto,
  SshAccessValidationDto,
  SignedPortPreviewUrl,
  ResizeSandbox,
  CreateSandboxSnapshot,
  UpdateSandboxNetworkSettings,
  SandboxListSortField,
  SandboxListSortDirection,
  MetricSeries,
} from '@daytona/api-client'
import { Daytona } from './Daytona'
import type { Resources } from './Daytona'
import {
  FileSystemApi,
  GitApi,
  ProcessApi,
  LspApi,
  InfoApi,
  SystemApi,
  ComputerUseApi,
  InterpreterApi,
  ServerApi,
} from '@daytona/toolbox-api-client'
import type { SystemMetrics } from '@daytona/toolbox-api-client'
import { FileSystem } from './FileSystem'
import { Git } from './Git'
import { Process } from './Process'
import { LspLanguageId, LspServer } from './LspServer'
import { DaytonaError, DaytonaNotFoundError, DaytonaTimeoutError, DaytonaValidationError } from './errors/DaytonaError'
import { CODE_TOOLBOX_LANGUAGE_LABEL } from './Daytona'
import { ComputerUse } from './ComputerUse'
import type { AxiosInstance } from 'axios'
import { CodeInterpreter } from './CodeInterpreter'
import { WithInstrumentation } from './utils/otel.decorator'
import { EventSubscriptionManager } from './utils/EventSubscriptionManager'

function withEvents<This, Args extends unknown[], Return>(
  target: (this: This, ...args: Args) => Return,
  _context: ClassMethodDecoratorContext<This, (this: This, ...args: Args) => Return>,
): (this: This, ...args: Args) => Return {
  return function (this: This, ...args: Args): Return {
    ;(this as { ensureSubscribed(): void }).ensureSubscribed()
    return target.apply(this, args)
  }
}

/**
 * Represents a Daytona Sandbox.
 *
 * @property {FileSystem} fs - File system operations interface
 * @property {Git} git - Git operations interface
 * @property {Process} process - Process execution interface
 * @property {CodeInterpreter} codeInterpreter - Stateful interpreter interface for executing code.
 *   Currently supports only Python. For other languages, use the `process.codeRun` method.
 * @property {ComputerUse} computerUse - Computer use operations interface for desktop automation
 * @property {string} id - Unique identifier for the Sandbox
 * @property {string} organizationId - Organization ID of the Sandbox
 * @property {string} [snapshot] - Daytona snapshot used to create the Sandbox
 * @property {string} user - OS user running in the Sandbox
 * @property {Record<string, string>} [env] - Environment variables set in the Sandbox
 * (not returned by list results; call `refreshData()` on each item to populate)
 * @property {Record<string, string>} labels - Custom labels attached to the Sandbox
 * @property {boolean} public - Whether the Sandbox is publicly accessible
 * @property {string} target - Target location of the runner where the Sandbox runs
 * @property {number} cpu - Number of CPUs allocated to the Sandbox
 * @property {number} gpu - Number of GPUs allocated to the Sandbox
 * @property {number} memory - Amount of memory allocated to the Sandbox in GiB
 * @property {number} disk - Amount of disk space allocated to the Sandbox in GiB
 * @property {SandboxState} state - Current state of the Sandbox (e.g., "started", "stopped")
 * @property {string} [errorReason] - Error message if Sandbox is in error state
 * @property {boolean} [recoverable] - Whether the Sandbox error is recoverable.
 * @property {SandboxBackupStateEnum} [backupState] - Current state of Sandbox backup
 * @property {string} [backupCreatedAt] - When the backup was created (not returned by list results;
 * call `refreshData()` on each item to populate)
 * @property {number} [autoStopInterval] - Auto-stop interval in minutes
 * @property {number} [autoPauseInterval] - Auto-pause interval in minutes
 * @property {number} [autoArchiveInterval] - Auto-archive interval in minutes
 * @property {number} [autoDeleteInterval] - Auto-delete interval in minutes
 * @property {string} [expiresAt] - When the Sandbox will expire and be destroyed (only set when a TTL is configured)
 * @property {Array<SandboxVolume>} [volumes] - Volumes attached to the Sandbox (not returned by
 * list results; call `refreshData()` on each item to populate)
 * @property {BuildInfo} [buildInfo] - Build information for the Sandbox if it was created from dynamic build
 * (not returned by list results; call `refreshData()` on each item to populate)
 * @property {string} [createdAt] - When the Sandbox was created
 * @property {string} [updatedAt] - When the Sandbox was last updated
 * @property {string} [lastActivityAt] - When the Sandbox last had activity
 * @property {boolean} [networkBlockAll] - Whether to block all network access for the Sandbox
 * (not returned by list results; call `refreshData()` on each item to populate)
 * @property {string} [networkAllowList] - Comma-separated list of allowed CIDR network addresses for the Sandbox
 * (not returned by list results; call `refreshData()` on each item to populate)
 * @property {string} [domainAllowList] - Comma-separated list of allowed domains for the Sandbox
 * (not returned by list results; call `refreshData()` on each item to populate)
 * @property {string} [linkedSandboxId] - ID of the Sandbox this Sandbox is linked to. When set, the Sandbox is co-located on the same runner as the linked Sandbox.
 * (not returned by list results; call `refreshData()` on each item to populate)
 *
 * @class
 */
export class Sandbox {
  public readonly fs: FileSystem
  public readonly git: Git
  public readonly process: Process
  public readonly computerUse: ComputerUse
  public readonly codeInterpreter: CodeInterpreter

  public id!: string
  public name!: string
  public organizationId!: string
  public snapshot?: string
  public user!: string
  public env?: Record<string, string>
  public labels!: Record<string, string>
  public public!: boolean
  public target!: string
  public cpu!: number
  public gpu!: number
  public memory!: number
  public disk!: number
  public state?: SandboxState
  public errorReason?: string
  public recoverable?: boolean
  public backupState?: SandboxBackupStateEnum
  public backupCreatedAt?: string
  public autoStopInterval?: number
  public autoPauseInterval?: number
  public autoArchiveInterval?: number
  public autoDeleteInterval?: number
  public expiresAt?: string
  public volumes?: Array<SandboxVolume>
  public buildInfo?: BuildInfo
  public createdAt?: string
  public updatedAt?: string
  public lastActivityAt?: string
  public networkBlockAll?: boolean
  public networkAllowList?: string
  public domainAllowList?: string
  public linkedSandboxId?: string
  public toolboxProxyUrl: string

  private infoApi: InfoApi
  private serverApi: ServerApi
  private systemApi: SystemApi
  private readonly stateWaiters: Array<{
    targetStates: Set<SandboxState>
    errorStates: Set<SandboxState>
    resolve: (state: SandboxState) => void
    reject: (err: Error) => void
  }> = []
  private readonly stateWaiterErrorMessageFns = new WeakMap<
    (typeof this.stateWaiters)[number],
    (state: string) => string
  >()
  private subId: string | undefined

  /**
   * Creates a new Sandbox instance.
   *
   * Internal: obtain sandboxes via {@link Daytona.create}, {@link Daytona.get}, or
   * {@link Daytona.list} rather than constructing directly.
   *
   * @param {SandboxDto} sandboxDto - The API Sandbox instance
   * @param {SandboxApi} sandboxApi - API client for Sandbox operations
   * @param {InfoApi} infoApi - API client for info operations
   * @param {EventSubscriptionManager} subscriptionManager - Event subscription manager for real-time updates
   */
  constructor(
    sandboxDto: SandboxDto | SandboxListItemDto,
    private readonly clientConfig: Configuration,
    private readonly axiosInstance: AxiosInstance,
    private readonly sandboxApi: SandboxApi,
    private readonly getAnalyticsApiUrl: () => Promise<string | undefined>,
    private readonly subscriptionManager: EventSubscriptionManager,
  ) {
    this.processSandboxDto(sandboxDto)

    const getPreviewToken = async () => (await this.getPreviewLink(1)).token

    this.fs = new FileSystem(this.clientConfig, new FileSystemApi(this.clientConfig, '', this.axiosInstance))
    this.git = new Git(new GitApi(this.clientConfig, '', this.axiosInstance))
    const language = sandboxDto.labels?.[CODE_TOOLBOX_LANGUAGE_LABEL]
    this.process = new Process(
      this.clientConfig,
      new ProcessApi(this.clientConfig, '', this.axiosInstance),
      getPreviewToken,
      language,
    )
    this.codeInterpreter = new CodeInterpreter(
      this.clientConfig,
      new InterpreterApi(this.clientConfig, '', this.axiosInstance),
      getPreviewToken,
    )
    this.computerUse = new ComputerUse(new ComputerUseApi(this.clientConfig, '', this.axiosInstance))
    this.infoApi = new InfoApi(this.clientConfig, '', this.axiosInstance)
    this.serverApi = new ServerApi(this.clientConfig, '', this.axiosInstance)
    this.systemApi = new SystemApi(this.clientConfig, '', this.axiosInstance)

    this.subscribeToEvents()
  }

  /**
   * Gets the user's home directory path for the logged in user inside the Sandbox.
   *
   * @returns {Promise<string | undefined>} The absolute path to the Sandbox user's home directory for the logged in user
   *
   * @example
   * const userHomeDir = await sandbox.getUserHomeDir();
   * console.log(`Sandbox user home: ${userHomeDir}`);
   */
  @WithInstrumentation()
  @withEvents
  public async getUserHomeDir(): Promise<string | undefined> {
    const response = await this.infoApi.getUserHomeDir()
    return response.data.dir
  }

  /**
   * Gets the most recent resource usage sample directly from the sandbox daemon.
   *
   * Unlike {@link getMetrics}, which returns aggregated historical samples, this returns the
   * single current reading without going through the telemetry backend.
   *
   * @returns The current resource usage sample for the sandbox.
   *
   * @example
   * const m = await sandbox.getMetricsLatest()
   * console.log(`CPU: ${m.cpuUsedPct}%, mem: ${m.memUsed}/${m.memTotal}`)
   */
  @WithInstrumentation()
  public async getMetricsLatest(): Promise<SandboxMetrics> {
    const response = await this.systemApi.getSystemMetrics()
    return sandboxMetricsFromSystemMetrics(response.data)
  }

  /**
   * Gets historical time-series resource usage metrics for the Sandbox.
   *
   * @param {Date} [start] - Start of the time range. Defaults to the Sandbox creation time.
   * @param {Date} [end] - End of the time range. Defaults to the current time.
   * @returns Time-ordered usage samples over the requested range.
   *
   * @example
   * const samples = await sandbox.getMetrics()
   * for (const s of samples) {
   *   console.log(`${s.timestamp.toISOString()} CPU: ${s.cpuUsedPct}% mem: ${s.memUsed}/${s.memTotal}`)
   * }
   */
  @WithInstrumentation()
  public async getMetrics(start?: Date, end?: Date): Promise<SandboxMetrics[]> {
    const to = end ?? new Date()
    const from = start ?? (this.createdAt ? new Date(this.createdAt) : to)

    const analyticsApiUrl = await this.getAnalyticsApiUrl()
    if (analyticsApiUrl) {
      const response = await this.buildAnalyticsTelemetryApi(
        analyticsApiUrl,
      ).organizationOrganizationIdSandboxSandboxIdTelemetryMetricsGet(
        this.organizationId,
        this.id,
        from.toISOString(),
        to.toISOString(),
        SANDBOX_METRIC_NAMES.join(','),
      )
      return pivotSandboxMetricPoints(response.data)
    }

    const response = await this.sandboxApi.getSandboxMetrics(this.id, from, to, undefined, SANDBOX_METRIC_NAMES)
    return pivotSandboxMetrics(response.data.series)
  }

  private buildAnalyticsTelemetryApi(analyticsApiUrl: string): AnalyticsTelemetryApi {
    const analyticsConfig = new AnalyticsConfiguration({
      basePath: analyticsApiUrl,
      apiKey: this.clientConfig.baseOptions?.headers?.Authorization,
    })
    return new AnalyticsTelemetryApi(analyticsConfig, undefined, Daytona.createAxiosInstance())
  }

  /**
   * @deprecated Use `getUserHomeDir` instead. This method will be removed in a future version.
   */
  @WithInstrumentation()
  @withEvents
  public async getUserRootDir(): Promise<string | undefined> {
    return this.getUserHomeDir()
  }

  /**
   * Gets the working directory path inside the Sandbox.
   *
   * @returns {Promise<string | undefined>} The absolute path to the Sandbox working directory. Uses the WORKDIR specified
   * in the Dockerfile if present, or falling back to the user's home directory if not.
   *
   * @example
   * const workDir = await sandbox.getWorkDir();
   * console.log(`Sandbox working directory: ${workDir}`);
   */
  @WithInstrumentation()
  @withEvents
  public async getWorkDir(): Promise<string | undefined> {
    const response = await this.infoApi.getWorkDir()
    return response.data.dir
  }

  /**
   * Creates a new Language Server Protocol (LSP) server instance.
   *
   * The LSP server provides language-specific features like code completion,
   * diagnostics, and more.
   *
   * @param {LspLanguageId} languageId - The language server type (e.g., "typescript")
   * @param {string} pathToProject - Path to the project root directory. Relative paths are resolved based on the sandbox working directory.
   * @returns {LspServer} A new LSP server instance configured for the specified language
   *
   * @example
   * const lsp = await sandbox.createLspServer('typescript', 'workspace/project');
   */
  @WithInstrumentation()
  @withEvents
  public async createLspServer(languageId: LspLanguageId | string, pathToProject: string): Promise<LspServer> {
    return new LspServer(
      languageId as LspLanguageId,
      pathToProject,
      new LspApi(this.clientConfig, '', this.axiosInstance),
    )
  }

  /**
   * Sets labels for the Sandbox.
   *
   * Labels are key-value pairs that can be used to organize and identify Sandboxes.
   *
   * @param {Record<string, string>} labels - Dictionary of key-value pairs representing Sandbox labels
   * @returns {Promise<void>}
   *
   * @example
   * // Set sandbox labels
   * await sandbox.setLabels({
   *   project: 'my-project',
   *   environment: 'development',
   *   team: 'backend'
   * });
   */
  @WithInstrumentation()
  @withEvents
  public async setLabels(labels: Record<string, string>): Promise<Record<string, string>> {
    this.labels = (await this.sandboxApi.replaceLabels(this.id, { labels })).data.labels
    return this.labels
  }

  /**
   * Start the Sandbox.
   *
   * This method starts the Sandbox and waits for it to be ready.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If Sandbox fails to start or times out
   *
   * @example
   * const sandbox = await daytona.getCurrentSandbox('my-sandbox');
   * await sandbox.start(40);  // Wait up to 40 seconds
   * console.log('Sandbox started successfully');
   */
  @WithInstrumentation()
  @withEvents
  public async start(timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    const response = await this.sandboxApi.startSandbox(this.id, undefined, { timeout: timeout * 1000 })
    this.processSandboxDto(response.data)
    const timeElapsed = Date.now() - startTime
    await this.waitUntilStarted(timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout)
  }

  /**
   * Recover the Sandbox from a recoverable error and wait for it to be ready.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If Sandbox fails to recover or times out
   *
   * @example
   * const sandbox = await daytona.get('my-sandbox-id');
   * await sandbox.recover();
   * console.log('Sandbox recovered successfully');
   */
  @withEvents
  public async recover(timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    const response = await this.sandboxApi.recoverSandbox(this.id, undefined, undefined, { timeout: timeout * 1000 })
    this.processSandboxDto(response.data)
    const timeElapsed = Date.now() - startTime
    await this.waitUntilStarted(timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout)
  }

  /**
   * Stops the Sandbox.
   *
   * This method stops the Sandbox and waits for it to be fully stopped.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @param {boolean} [force] - If true, uses SIGKILL instead of SIGTERM. Defaults to false.
   * @returns {Promise<void>}
   *
   * @example
   * const sandbox = await daytona.get('my-sandbox-id');
   * await sandbox.stop();
   * console.log('Sandbox stopped successfully');
   */
  @WithInstrumentation()
  @withEvents
  public async stop(timeout = 60, force = false): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }
    const startTime = Date.now()
    await this.sandboxApi.stopSandbox(this.id, undefined, force, { timeout: timeout * 1000 })
    await this.refreshDataSafe()
    const timeElapsed = Date.now() - startTime
    await this.waitUntilStopped(timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout)
  }

  /**
   * Forks the Sandbox, creating a new Sandbox with an identical filesystem.
   *
   * The forked Sandbox is a copy-on-write clone of the original. It starts
   * with the same disk contents but operates independently from that point on.
   *
   * @param {object} [params] - Fork parameters
   * @param {string} [params.name] - Optional name for the forked Sandbox. If not provided, a unique name will be generated.
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @returns {Promise<Sandbox>} The forked Sandbox.
   * @throws {DaytonaValidationError} - If timeout is a negative number
   * @throws {DaytonaError} - If the fork operation fails or times out
   *
   * @example
   * const sandbox = await daytona.get('my-sandbox');
   * const forked = await sandbox._experimental_fork({ name: 'my-fork' });
   * console.log(`Forked sandbox: ${forked.id}`);
   */
  @WithInstrumentation()
  @withEvents
  public async _experimental_fork(params?: { name?: string }, timeout = 60): Promise<Sandbox> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    const response = await this.sandboxApi.forkSandbox(this.id, { name: params?.name }, undefined, {
      timeout: timeout * 1000,
    })
    const sandboxDto = response.data

    const sandboxWithProxyUrl = sandboxDto.toolboxProxyUrl
      ? sandboxDto
      : {
          ...sandboxDto,
          toolboxProxyUrl: (await this.sandboxApi.getToolboxProxyUrl(sandboxDto.id)).data.url,
        }

    const forkedSandbox = new Sandbox(
      sandboxWithProxyUrl,
      structuredClone(this.clientConfig),
      Daytona.createAxiosInstance(),
      this.sandboxApi,
      this.getAnalyticsApiUrl,
      this.subscriptionManager,
    )

    const timeElapsed = Date.now() - startTime
    const remainingTimeout = timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout
    await forkedSandbox.waitUntilStarted(remainingTimeout)
    return forkedSandbox
  }

  /**
   * Creates a snapshot from the current state of the Sandbox.
   *
   * This captures the Sandbox's filesystem into a reusable snapshot that can be
   * used to create new Sandboxes. The Sandbox will temporarily enter a
   * 'snapshotting' state and return to its previous state when complete.
   *
   * @param {string} name - Name for the new snapshot
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @returns {Promise<void>}
   * @throws {DaytonaValidationError} - If timeout is a negative number
   * @throws {DaytonaError} - If the snapshot operation fails or times out
   *
   * @example
   * const sandbox = await daytona.get('my-sandbox');
   * await sandbox._experimental_createSnapshot('my-snapshot');
   * console.log('Snapshot created successfully');
   */
  @WithInstrumentation()
  @withEvents
  public async _experimental_createSnapshot(name: string, timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    const req: CreateSandboxSnapshot = { name }
    const response = await this.sandboxApi.createSandboxSnapshot(this.id, req, undefined, {
      timeout: timeout * 1000,
    })

    this.processSandboxDto(response.data)

    const timeElapsed = Date.now() - startTime
    const remainingTimeout = timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout
    await this.waitForSnapshotComplete(remainingTimeout)
  }

  private async waitForSnapshotComplete(timeout: number) {
    const errorStates = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
    const excludeStates = new Set<string>([SandboxState.SNAPSHOTTING, ...errorStates])
    const targetStates = Object.values(SandboxState).filter((s) => !excludeStates.has(s))

    return this.waitForState(
      targetStates,
      errorStates,
      timeout,
      'Sandbox snapshot did not complete within the timeout period',
      (state) => `Sandbox ${this.id} snapshot failed with state: ${state}, error reason: ${this.errorReason}`,
    )
  }

  /**
   * Pauses the Sandbox, freezing all running processes.
   *
   * The Sandbox will enter a 'pausing' state and transition to 'paused' when
   * complete. While paused, the Sandbox retains its state in memory but does
   * not consume CPU cycles.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @returns {Promise<void>}
   * @throws {DaytonaValidationError} - If timeout is a negative number
   * @throws {DaytonaError} - If the pause operation fails or times out
   *
   * @example
   * const sandbox = await daytona.get('my-sandbox');
   * await sandbox.pause();
   * console.log('Sandbox paused successfully');
   */
  @WithInstrumentation()
  @withEvents
  public async pause(timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    await this.sandboxApi.pauseSandbox(this.id, undefined, {
      timeout: timeout * 1000,
    })

    await this.refreshData()

    const timeElapsed = Date.now() - startTime
    const remainingTimeout = timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout
    // Main's contract: pause completes when the sandbox has *left* PAUSING
    // (paused, stopped, archived, ...), not only on exactly PAUSED.
    const errorStates = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
    const excludeStates = new Set<string>([SandboxState.PAUSING, ...errorStates])
    const targetStates = Object.values(SandboxState).filter((s) => !excludeStates.has(s))
    await this.waitForState(
      targetStates,
      errorStates,
      remainingTimeout,
      'Sandbox failed to become paused within the timeout period',
      (state) => `Sandbox ${this.id} pause failed with state: ${state}, error reason: ${this.errorReason}`,
    )
  }

  /**
   * Deletes the Sandbox.
   *
   * By default this returns as soon as the deletion request is accepted (matching
   * historical behavior). Pass `wait = true` to block until the Sandbox reaches
   * the 'destroyed' state.
   *
   * @param {number} [timeout] - Timeout in seconds for the request — and, when
   *                            `wait` is true, for reaching 'destroyed'. 0 means no timeout.
   *                            Defaults to 60-second timeout.
   * @param {boolean} [wait] - If true, wait until the Sandbox is destroyed. Defaults to false.
   * @returns {Promise<void>}
   */
  @WithInstrumentation()
  @withEvents
  public async delete(timeout = 60, wait = false): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    const startTime = Date.now()
    const response = await this.sandboxApi.deleteSandbox(this.id, undefined, { timeout: timeout * 1000 })
    if (response.data) {
      this.processSandboxDto(response.data)
    }

    try {
      if (wait && this.state !== SandboxState.DESTROYED) {
        const timeElapsed = Date.now() - startTime
        await this.waitForState(
          [SandboxState.DESTROYED],
          [SandboxState.ERROR, SandboxState.BUILD_FAILED],
          timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout,
          'Sandbox failed to be destroyed within the timeout period',
          (state) => `Sandbox ${this.id} failed to delete with state: ${state}, error reason: ${this.errorReason}`,
          true,
        )
      }
    } finally {
      if (this.subId) {
        this.subscriptionManager.unsubscribe(this.subId)
        this.subId = undefined
      }
    }
  }

  /**
   * Waits for the Sandbox to reach the 'started' state.
   *
   * This method polls the Sandbox status until it reaches the 'started' state
   * or encounters an error.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                               Defaults to 60 seconds.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If the sandbox ends up in an error state or fails to start within the timeout period.
   */
  @WithInstrumentation()
  @withEvents
  public async waitUntilStarted(timeout = 60) {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    if (this.state === SandboxState.STARTED) {
      return
    }

    return this.waitForState(
      [SandboxState.STARTED],
      [SandboxState.ERROR, SandboxState.BUILD_FAILED],
      timeout,
      'Sandbox failed to become ready within the timeout period',
      (state) => `Sandbox ${this.id} failed to start with status: ${state}, error reason: ${this.errorReason}`,
    )
  }

  /**
   * Wait for Sandbox to reach 'stopped' state.
   *
   * This method polls the Sandbox status until it reaches the 'stopped' state
   * or encounters an error.
   *
   * @param {number} [timeout] - Maximum time to wait in seconds. 0 means no timeout.
   *                               Defaults to 60 seconds.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If the sandbox fails to stop within the timeout period.
   */
  @WithInstrumentation()
  @withEvents
  public async waitUntilStopped(timeout = 60) {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    // Treat destroyed as stopped to cover ephemeral sandboxes that are automatically deleted after stopping
    if (this.state === SandboxState.STOPPED || this.state === SandboxState.DESTROYED) {
      return
    }

    return this.waitForState(
      [SandboxState.STOPPED, SandboxState.DESTROYED],
      [SandboxState.ERROR, SandboxState.BUILD_FAILED],
      timeout,
      'Sandbox failed to become stopped within the timeout period',
      (state) => `Sandbox failed to stop with status: ${state}, error reason: ${this.errorReason}`,
      true,
    )
  }

  /**
   * Refreshes the Sandbox data from the API.
   *
   * @returns {Promise<void>}
   *
   * @example
   * await sandbox.refreshData();
   * console.log(`Sandbox ${sandbox.id}:`);
   * console.log(`State: ${sandbox.state}`);
   * console.log(`Resources: ${sandbox.cpu} CPU, ${sandbox.memory} GiB RAM`);
   */
  @WithInstrumentation()
  @withEvents
  public async refreshData(): Promise<void> {
    const response = await this.sandboxApi.getSandbox(this.id)
    this.processSandboxDto(response.data)
  }

  /**
   * Refreshes the sandbox activity to reset the timer for automated lifecycle management actions.
   *
   * This method updates the sandbox's last activity timestamp without changing its state.
   * It is useful for keeping long-running sessions alive while there is still user activity.
   *
   * @returns {Promise<void>}
   *
   * @example
   * // Keep sandbox activity alive
   * await sandbox.refreshActivity();
   */
  @withEvents
  public async refreshActivity(): Promise<void> {
    await this.sandboxApi.updateLastActivity(this.id)
  }

  /**
   * Set the auto-stop interval for the Sandbox.
   *
   * The Sandbox will automatically stop after being idle (no new events) for the specified interval.
   * Events include any state changes or interactions with the Sandbox through the sdk.
   * Interactions using Sandbox Previews are not included.
   *
   * @param {number} interval - Number of minutes of inactivity before auto-stopping.
   *                           Set to 0 to disable auto-stop. Default is 15 minutes.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If interval is not a non-negative integer
   *
   * @example
   * // Auto-stop after 1 hour
   * await sandbox.setAutostopInterval(60);
   * // Or disable auto-stop
   * await sandbox.setAutostopInterval(0);
   */
  @WithInstrumentation()
  @withEvents
  public async setAutostopInterval(interval: number): Promise<void> {
    if (!Number.isInteger(interval) || interval < 0) {
      throw new DaytonaValidationError('autoStopInterval must be a non-negative integer')
    }

    await this.sandboxApi.setAutostopInterval(this.id, interval)
    this.autoStopInterval = interval
  }

  /**
   * Set the auto-pause interval for the Sandbox.
   *
   * The Sandbox will automatically pause after being idle (no new events) for the specified interval.
   * Events include any state changes or interactions with the Sandbox through the sdk.
   * Interactions using Sandbox Previews are not included.
   *
   * Only supported for sandbox classes that support pausing. At most one of the auto-stop
   * and auto-pause intervals may be non-zero, so disable auto-stop first by setting its
   * interval to 0.
   *
   * @param {number} interval - Number of minutes of inactivity before auto-pausing.
   *                           Set to 0 to disable auto-pause. For pause-supporting sandbox
   *                           classes, creation defaults to 60 minutes when neither interval is provided.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If interval is not a non-negative integer
   *
   * @example
   * // Auto-pause after 1 hour
   * await sandbox.setAutoPauseInterval(60);
   * // Or disable auto-pause
   * await sandbox.setAutoPauseInterval(0);
   */
  @WithInstrumentation()
  public async setAutoPauseInterval(interval: number): Promise<void> {
    if (!Number.isInteger(interval) || interval < 0) {
      throw new DaytonaValidationError('autoPauseInterval must be a non-negative integer')
    }

    await this.sandboxApi.setAutoPauseInterval(this.id, interval)
    this.autoPauseInterval = interval
  }

  /**
   * Set the TTL (maximum time to live) for the Sandbox.
   *
   * The Sandbox will be destroyed once the TTL elapses, counted as wall-clock time regardless of the
   * Sandbox state - even if it is stopped, paused, or archived. Calling this method re-anchors the
   * deadline from the current time. Call `refreshData()` afterwards to read the updated `expiresAt`.
   *
   * @param {number} ttlMinutes - Number of minutes from now after which the Sandbox will be destroyed.
   *                           Set to 0 to disable the TTL.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If ttlMinutes is not a non-negative integer
   *
   * @example
   * // Destroy the Sandbox 1 hour from now
   * await sandbox.setTtl(60);
   * // Or disable the TTL
   * await sandbox.setTtl(0);
   */
  @WithInstrumentation()
  public async setTtl(ttlMinutes: number): Promise<void> {
    if (!Number.isInteger(ttlMinutes) || ttlMinutes < 0) {
      throw new DaytonaValidationError('ttlMinutes must be a non-negative integer')
    }

    await this.sandboxApi.setTtl(this.id, ttlMinutes)
  }

  /**
   * Set the auto-archive interval for the Sandbox.
   *
   * The Sandbox will automatically archive after being continuously stopped for the specified interval.
   *
   * @param {number} interval - Number of minutes after which a continuously stopped Sandbox will be auto-archived.
   *                           Set to 0 for the maximum interval. Default is 7 days.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - `DaytonaError` - If interval is not a non-negative integer
   *
   * @example
   * // Auto-archive after 1 hour
   * await sandbox.setAutoArchiveInterval(60);
   * // Or use the maximum interval
   * await sandbox.setAutoArchiveInterval(0);
   */
  @WithInstrumentation()
  @withEvents
  public async setAutoArchiveInterval(interval: number): Promise<void> {
    if (!Number.isInteger(interval) || interval < 0) {
      throw new DaytonaValidationError('autoArchiveInterval must be a non-negative integer')
    }
    await this.sandboxApi.setAutoArchiveInterval(this.id, interval)
    this.autoArchiveInterval = interval
  }

  /**
   * Set the auto-delete interval for the Sandbox.
   *
   * The Sandbox will automatically delete after being continuously stopped for the specified interval.
   *
   * @param {number} interval - Number of minutes after which a continuously stopped Sandbox will be auto-deleted.
   *                           Set to negative value to disable auto-delete. Set to 0 to delete immediately upon stopping.
   *                           By default, auto-delete is disabled.
   * @returns {Promise<void>}
   *
   * @example
   * // Auto-delete after 1 hour
   * await sandbox.setAutoDeleteInterval(60);
   * // Or delete immediately upon stopping
   * await sandbox.setAutoDeleteInterval(0);
   * // Or disable auto-delete
   * await sandbox.setAutoDeleteInterval(-1);
   */
  @WithInstrumentation()
  @withEvents
  public async setAutoDeleteInterval(interval: number): Promise<void> {
    await this.sandboxApi.setAutoDeleteInterval(this.id, interval)
    this.autoDeleteInterval = interval
  }

  /**
   * Updates outbound network policy for this sandbox on the runner (for example block all traffic,
   * restore general internet access, or apply a CIDR allow list) without stopping the sandbox.
   *
   * This maps to the same mechanism as creating a sandbox with `networkBlockAll` / `networkAllowList` /
   * `domainAllowList`: the runner applies iptables rules to the sandbox container.
   *
   * @param {UpdateSandboxNetworkSettings} settings - At least one of `networkBlockAll`, `networkAllowList` or
   *   `domainAllowList` must be set.
   *   Set `networkBlockAll` to `false` to restore outbound access after a block (and clear a stored allow list).
   *
   * @example
   * // Pause internet (outbound blocked)
   * await sandbox.updateNetworkSettings({ networkBlockAll: true });
   * // Resume internet
   * await sandbox.updateNetworkSettings({ networkBlockAll: false });
   * // Allow only specific domains
   * await sandbox.updateNetworkSettings({ domainAllowList: 'example.com,*.daytona.io' });
   */
  @WithInstrumentation()
  public async updateNetworkSettings(settings: UpdateSandboxNetworkSettings): Promise<void> {
    if (
      settings.networkBlockAll === undefined &&
      settings.networkAllowList === undefined &&
      settings.domainAllowList === undefined
    ) {
      throw new DaytonaValidationError(
        'At least one of networkBlockAll, networkAllowList or domainAllowList must be set',
      )
    }
    const response = await this.sandboxApi.updateNetworkSettings(this.id, settings)
    this.processSandboxDto(response.data)
  }

  /**
   * Replaces the set of vault secrets mounted in the Sandbox.
   *
   * Each key is an environment variable name and each value is the name of an existing
   * organization Secret to mount under that name. The provided map replaces the previously
   * mounted set — pass an empty object to detach all secrets.
   *
   * Attached, detached, or rotated secrets take effect for outbound requests within seconds.
   * However, newly attached env vars only become visible to processes spawned after the update;
   * already-running processes keep their environment. A Sandbox created without any secrets
   * must be restarted for newly attached secrets to work.
   *
   * @param {Record<string, string>} secrets - Map of environment variable name to the name of an
   *   existing organization Secret. Every referenced Secret name must already exist in the organization.
   * @returns {Promise<void>}
   *
   * @example
   * // Mount two secrets
   * await sandbox.updateSecrets({ API_KEY: 'my-api-key', DB_PASSWORD: 'prod-db-password' });
   * // Detach all secrets
   * await sandbox.updateSecrets({});
   */
  @WithInstrumentation()
  public async updateSecrets(secrets: Record<string, string>): Promise<void> {
    const response = await this.sandboxApi.updateSandboxSecrets(this.id, {
      secrets: Object.entries(secrets).map(([envVar, secretName]) => ({ [envVar]: secretName })),
    })
    this.processSandboxDto(response.data)
  }

  /**
   * Updates the Sandbox daemon's process environment.
   *
   * Variables in `env` are set (added or overwritten) and variables listed in `options.unset`
   * are removed. Newly spawned processes, sessions and PTYs inherit the change; already-running
   * processes keep their environment.
   *
   * @param {Record<string, string>} env - Map of environment variable names to values to set.
   * @param {object} [options] - Optional settings.
   * @param {string[]} [options.unset] - Names of environment variables to remove before `env` is applied.
   * @returns {Promise<void>}
   *
   * @example
   * // Set a variable and remove another
   * await sandbox.updateEnv({ NODE_ENV: 'production' }, { unset: ['DEBUG'] });
   */
  @WithInstrumentation()
  public async updateEnv(env: Record<string, string>, options?: { unset?: string[] }): Promise<void> {
    await this.serverApi.updateEnv({
      set: env,
      unset: options?.unset,
    })
  }

  /**
   * Retrieves the preview link for the sandbox at the specified port. If the port is closed,
   * it will be opened automatically. For private sandboxes, a token is included to grant access
   * to the URL.
   *
   * @param {number} port - The port to open the preview link on.
   * @returns {PortPreviewUrl} The response object for the preview link, which includes the `url`
   * and the `token` (to access private sandboxes).
   *
   * @example
   * const previewLink = await sandbox.getPreviewLink(3000);
   * console.log(`Preview URL: ${previewLink.url}`);
   * console.log(`Token: ${previewLink.token}`);
   */
  @WithInstrumentation()
  @withEvents
  public async getPreviewLink(port: number): Promise<PortPreviewUrl> {
    return (await this.sandboxApi.getPortPreviewUrl(this.id, port)).data
  }

  /**
   * Retrieves a signed preview url for the sandbox at the specified port.
   *
   * @param {number} port - The port to open the preview link on.
   * @param {number} [expiresInSeconds] - The number of seconds the signed preview url will be valid for. Defaults to 60 seconds.
   * @returns {Promise<SignedPortPreviewUrl>} The response object for the signed preview url.
   */
  @withEvents
  public async getSignedPreviewUrl(port: number, expiresInSeconds?: number): Promise<SignedPortPreviewUrl> {
    return (await this.sandboxApi.getSignedPortPreviewUrl(this.id, port, undefined, expiresInSeconds)).data
  }

  /**
   * Expires a signed preview url for the sandbox at the specified port.
   *
   * @param {number} port - The port to expire the signed preview url on.
   * @param {string} token - The token to expire the signed preview url on.
   * @returns {Promise<void>}
   */
  @withEvents
  public async expireSignedPreviewUrl(port: number, token: string): Promise<void> {
    await this.sandboxApi.expireSignedPortPreviewUrl(this.id, port, token)
  }

  /**
   * Archives the sandbox, making it inactive and preserving its state. When sandboxes are archived, the entire filesystem
   * state is moved to cost-effective object storage, making it possible to keep sandboxes available for an extended period.
   * The tradeoff between archived and stopped states is that starting an archived sandbox takes more time, depending on its size.
   * Sandbox must be stopped before archiving.
   */
  @WithInstrumentation()
  @withEvents
  public async archive(): Promise<void> {
    await this.sandboxApi.archiveSandbox(this.id)
    await this.refreshData()
  }

  /**
   * Resizes the Sandbox resources.
   *
   * Changes the CPU, memory, or disk allocation. Hot resize (on a running Sandbox) accepts
   * only CPU and memory increases. Disk resize requires a stopped Sandbox; disk can only
   * grow. GPU is not resizable — to change GPU, create a new Sandbox.
   *
   * @param resources - New resource configuration (cpu, memory, disk only). Only specified fields are updated.
   * @param [timeout=60] - Timeout in seconds for the resize operation. 0 means no timeout.
   * @throws {DaytonaError} If hot-resize constraints are violated, disk resize is attempted on
   *   a running Sandbox, disk decrease is attempted, no fields are provided, or the operation times out.
   *
   * @example
   * await sandbox.resize({ cpu: 4, memory: 8 })
   *
   * @example
   * await sandbox.stop()
   * await sandbox.resize({ cpu: 2, memory: 4, disk: 30 })
   */
  @WithInstrumentation()
  @withEvents
  public async resize(resources: Pick<Resources, 'cpu' | 'memory' | 'disk'>, timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }
    if ('gpu' in resources || 'gpuType' in resources) {
      throw new DaytonaValidationError(
        'Resize does not support changes to gpu or gpuType — to change GPU, create a new Sandbox',
      )
    }

    const startTime = Date.now()
    const resizeRequest: ResizeSandbox = {
      cpu: resources.cpu,
      memory: resources.memory,
      disk: resources.disk,
    }
    const response = await this.sandboxApi.resizeSandbox(this.id, resizeRequest, this.organizationId, {
      timeout: timeout * 1000,
    })
    this.processSandboxDto(response.data)
    const timeElapsed = Date.now() - startTime
    await this.waitForResizeComplete(timeout ? Math.max(0.001, timeout - timeElapsed / 1000) : timeout)
  }

  /**
   * Waits for the Sandbox resize operation to complete.
   *
   * This method polls the Sandbox status until the state is no longer 'resizing'.
   *
   * @param {number} [timeout=60] - Maximum time to wait in seconds. 0 means no timeout.
   * @returns {Promise<void>}
   * @throws {DaytonaError} - If the sandbox ends up in an error state or resize times out.
   */
  @WithInstrumentation()
  @withEvents
  public async waitForResizeComplete(timeout = 60): Promise<void> {
    if (timeout < 0) {
      throw new DaytonaValidationError('Timeout must be a non-negative number')
    }

    if (this.state !== SandboxState.RESIZING) {
      return
    }

    const errorStates = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
    const excludeStates = new Set<string>([SandboxState.RESIZING, ...errorStates])
    const targetStates = Object.values(SandboxState).filter((s) => !excludeStates.has(s))

    return this.waitForState(
      targetStates,
      errorStates,
      timeout,
      'Sandbox resize did not complete within the timeout period',
      (state) => `Sandbox ${this.id} resize failed with state: ${state}, error reason: ${this.errorReason}`,
    )
  }

  /**
   * Creates an SSH access token for the sandbox.
   *
   * @param {number} expiresInMinutes - The number of minutes the SSH access token will be valid for.
   * @returns {Promise<SshAccessDto>} The SSH access token.
   */
  @WithInstrumentation()
  @withEvents
  public async createSshAccess(expiresInMinutes?: number): Promise<SshAccessDto> {
    return (await this.sandboxApi.createSshAccess(this.id, undefined, expiresInMinutes)).data
  }

  /**
   * Revokes an SSH access token for the sandbox.
   *
   * @param {string} token - The token to revoke.
   * @returns {Promise<void>}
   */
  @WithInstrumentation()
  @withEvents
  public async revokeSshAccess(token: string): Promise<void> {
    await this.sandboxApi.revokeSshAccess(this.id, undefined, token)
  }

  /**
   * Validates an SSH access token for the sandbox.
   *
   * @param {string} token - The token to validate.
   * @returns {Promise<SshAccessValidationDto>} The SSH access validation result.
   */
  @WithInstrumentation()
  @withEvents
  public async validateSshAccess(token: string): Promise<SshAccessValidationDto> {
    return (await this.sandboxApi.validateSshAccess(token)).data
  }

  /**
   * Subscribes to real-time events for this sandbox.
   * Auto-updates sandbox metadata on every event.
   */
  private subscribeToEvents(): void {
    if (this.subId) {
      return
    }

    this.subId = this.subscriptionManager.subscribe(this.id, this.handleEvent.bind(this), [
      'sandbox.state.updated',
      'sandbox.created',
    ])
  }

  private ensureSubscribed(): void {
    if (this.subId) {
      if (this.subscriptionManager.refresh(this.subId)) {
        return
      }

      this.subId = undefined
    }

    this.subscribeToEvents()
  }

  private handleEvent(eventName: string, data: any): void {
    if (!data || typeof data !== 'object') return
    const raw = data.sandbox ?? data
    if (!raw || typeof raw !== 'object') return

    if (eventName === 'sandbox.created') {
      this.processSandboxDto(raw as SandboxDto)
      return
    }

    const newState = raw.state ?? data.newState
    if (newState) {
      this.applyState(newState)
    }
  }

  /**
   * Waits for the sandbox to reach one of the target states.
   * Throws on error states or timeout.
   *
   * @param targetStates - States that indicate success.
   * @param errorStates - States that indicate failure.
   * @param timeout - Maximum time to wait in seconds. 0 means no timeout.
   * @param timeoutMessage - Error message when timeout is reached.
   * @param errorMessageFn - Function that produces an error message from the current state.
   * @param safeRefresh - If true, use refreshDataSafe() for polling (for delete operations where 404 is expected).
   */
  private waitForState(
    targetStates: SandboxState[],
    errorStates: SandboxState[],
    timeout: number,
    timeoutMessage: string,
    errorMessageFn: (state: string) => string,
    safeRefresh = false,
  ): Promise<void> {
    this.ensureSubscribed()

    return new Promise<void>((resolve, reject) => {
      let timeoutTimer: ReturnType<typeof setTimeout> | null = null
      let pollTimer: ReturnType<typeof setTimeout> | null = null
      let settled = false

      const waiter = {
        targetStates: new Set(targetStates),
        errorStates: new Set(errorStates),
        resolve: (_: SandboxState) => {
          if (settled) return
          cleanup()
          resolve()
        },
        reject: (err: Error) => {
          if (settled) return
          cleanup()
          reject(err)
        },
      }

      this.stateWaiters.push(waiter)
      this.stateWaiterErrorMessageFns.set(waiter, errorMessageFn)

      const cleanup = () => {
        if (settled) return
        settled = true
        if (timeoutTimer) clearTimeout(timeoutTimer)
        if (pollTimer) clearTimeout(pollTimer)
        this.removeStateWaiter(waiter)
      }

      // Fast-path only on cached *target* states (parity with main's pre-check).
      // Cached error states are deliberately NOT evaluated here — main always
      // refreshed before failing, so a stale ERROR must survive one refresh.
      if (this.state && waiter.targetStates.has(this.state)) {
        waiter.resolve(this.state)
        return
      }

      if (timeout !== 0) {
        timeoutTimer = setTimeout(() => {
          void (async () => {
            if (settled) return
            // Parity with main: complete one final refresh-then-evaluate before
            // rejecting, so a clamped/short timeout still observes the latest state.
            let refreshed = false
            try {
              if (safeRefresh) {
                refreshed = await this.refreshDataSafe()
              } else {
                await this.refreshData()
                refreshed = true
              }
            } catch {
              // fall through to the timeout rejection below
            }
            if (!settled) {
              // Explicit evaluation: applyState() no-ops on unchanged state, so a
              // persistent error state would otherwise time out generically here.
              // Only evaluate when the refresh succeeded — a failed refresh leaves
              // the cached state stale and must not be treated as authoritative.
              if (refreshed && this.checkStateWaiter(waiter, this.state)) {
                return
              }
              cleanup()
              reject(new DaytonaTimeoutError(timeoutMessage))
            }
          })()
        }, timeout * 1000)
      }

      // Poll as a safety net for missed state changes. With an active event subscription
      // a sparse 1s cadence suffices; without events (polling mode) replicate main's
      // cadence exactly: 100ms steady for the first 5s, then exponential backoff capped at 1s.
      const streaming = !!this.subId
      const pollStart = Date.now()
      let pollInterval = streaming ? 1000 : 100
      const doPoll = async () => {
        if (settled) return

        let refreshed = false
        try {
          if (safeRefresh) {
            refreshed = await this.refreshDataSafe()
          } else {
            await this.refreshData()
            refreshed = true
          }
        } catch (error) {
          waiter.reject(error instanceof Error ? error : new DaytonaError(String(error)))
          return
        }

        if (!settled) {
          // Evaluate the refreshed state explicitly: applyState() no-ops when the
          // state is unchanged, so a persistent error state would otherwise never
          // reject the waiter. Only evaluate when the refresh succeeded — a failed
          // safe refresh leaves the cached state stale, so keep polling instead.
          if (refreshed && this.checkStateWaiter(waiter, this.state)) {
            return
          }

          if (!streaming && Date.now() - pollStart > 5000) {
            pollInterval = Math.min(pollInterval * 1.1, 1000)
          }
          pollTimer = setTimeout(() => {
            void doPoll()
          }, pollInterval)
        }
      }

      // First poll runs immediately (main refreshed before any state evaluation).
      void doPoll()
    })
  }

  /**
   * Assigns the API sandbox data to the Sandbox object.
   *
   * @param {SandboxDto} sandboxDto - The API sandbox instance to assign data from
   * @returns {void}
   */
  private processSandboxDto(sandboxDto: SandboxDto | SandboxListItemDto) {
    const newState = sandboxDto.state

    this.id = sandboxDto.id
    this.name = sandboxDto.name
    this.organizationId = sandboxDto.organizationId
    this.snapshot = sandboxDto.snapshot
    this.user = sandboxDto.user
    this.labels = sandboxDto.labels
    this.public = sandboxDto.public
    this.target = sandboxDto.target
    this.cpu = sandboxDto.cpu
    this.gpu = sandboxDto.gpu
    this.memory = sandboxDto.memory
    this.disk = sandboxDto.disk
    this.errorReason = sandboxDto.errorReason
    this.recoverable = sandboxDto.recoverable
    this.backupState = sandboxDto.backupState
    this.autoStopInterval = sandboxDto.autoStopInterval
    this.autoPauseInterval = sandboxDto.autoPauseInterval
    this.autoArchiveInterval = sandboxDto.autoArchiveInterval
    this.autoDeleteInterval = sandboxDto.autoDeleteInterval
    this.expiresAt = sandboxDto.expiresAt
    this.createdAt = sandboxDto.createdAt
    this.updatedAt = sandboxDto.updatedAt
    this.lastActivityAt = sandboxDto.lastActivityAt
    // Fields only present in the full SandboxDto (not returned by list endpoint)
    if ('env' in sandboxDto) {
      this.env = sandboxDto.env
      this.networkBlockAll = sandboxDto.networkBlockAll
      this.networkAllowList = sandboxDto.networkAllowList
      this.domainAllowList = sandboxDto.domainAllowList
      this.linkedSandboxId = sandboxDto.linkedSandboxId
      this.volumes = sandboxDto.volumes
      this.buildInfo = sandboxDto.buildInfo
      this.backupCreatedAt = sandboxDto.backupCreatedAt
    }

    const newProxyUrl = sandboxDto.toolboxProxyUrl
    if (newProxyUrl && newProxyUrl !== this.toolboxProxyUrl && this.axiosInstance) {
      let baseUrl = newProxyUrl
      if (!baseUrl.endsWith('/')) {
        baseUrl += '/'
      }
      this.axiosInstance.defaults.baseURL = baseUrl + this.id
      this.clientConfig.basePath = this.axiosInstance.defaults.baseURL
    }
    if (newProxyUrl) {
      this.toolboxProxyUrl = newProxyUrl
    }

    if (newState) {
      this.applyState(newState)
    }
  }

  /**
   * Refreshes the Sandbox data from the API, but does not throw an error if the sandbox has been deleted.
   * Instead, it sets the state to destroyed.
   *
   * @returns {Promise<void>}
   */
  /**
   * @returns true when the refresh produced authoritative state (success, or 404
   *          mapped to DESTROYED); false when a transient error was swallowed and
   *          the cached state is stale.
   */
  private async refreshDataSafe(): Promise<boolean> {
    try {
      await this.refreshData()
      return true
    } catch (error) {
      if (error instanceof DaytonaNotFoundError) {
        this.applyState(SandboxState.DESTROYED)
        return true
      }
      // Other errors are deliberately swallowed (parity with main): transient
      // failures (e.g. 502) mid-poll must not abort stop()/delete() waits.
      return false
    }
  }

  private applyState(newState: string): void {
    if (newState === this.state) {
      return
    }

    this.state = newState as SandboxState

    for (const waiter of [...this.stateWaiters]) {
      this.checkStateWaiter(waiter, this.state)
    }
  }

  private checkStateWaiter(waiter: (typeof this.stateWaiters)[number], state?: SandboxState): boolean {
    if (!state) {
      return false
    }

    if (waiter.targetStates.has(state)) {
      waiter.resolve(state)
      return true
    }

    if (waiter.errorStates.has(state)) {
      const errorMessageFn = this.stateWaiterErrorMessageFns.get(waiter)
      waiter.reject(
        new DaytonaError(errorMessageFn ? errorMessageFn(state) : `Sandbox ${this.id} failed with state: ${state}`),
      )
      return true
    }

    return false
  }

  private removeStateWaiter(waiter: (typeof this.stateWaiters)[number]): void {
    const index = this.stateWaiters.indexOf(waiter)
    if (index !== -1) {
      this.stateWaiters.splice(index, 1)
    }
    this.stateWaiterErrorMessageFns.delete(waiter)
  }
}

export interface ListSandboxesQuery {
  /**
   * Per-page fetch size. Does NOT limit the total number of Sandboxes returned.
   * */
  limit?: number

  /**
   * Sort by field
   * */
  sort?: SandboxListSortField

  /**
   * Sort direction
   * */
  order?: SandboxListSortDirection

  /**
   * Filter by ID prefix (case-insensitive)
   * */
  id?: string

  /**
   * Filter by name prefix (case-insensitive)
   * */
  name?: string

  /**
   * Filter by labels
   * */
  labels?: Record<string, string>

  /**
   * Filter by states
   * */
  states?: SandboxState[]

  /**
   * Filter by snapshot names
   * */
  snapshots?: string[]

  /**
   * Filter by targets
   * */
  targets?: string[]

  /**
   * Filter by minimum CPU
   * */
  minCpu?: number

  /**
   * Filter by maximum CPU
   * */
  maxCpu?: number

  /**
   * Filter by minimum memory in GiB
   * */
  minMemoryGib?: number

  /**
   * Filter by maximum memory in GiB
   * */
  maxMemoryGib?: number

  /**
   * Filter by minimum disk space in GiB
   * */
  minDiskGib?: number

  /**
   * Filter by maximum disk space in GiB
   * */
  maxDiskGib?: number

  /**
   * Filter by public status
   * */
  isPublic?: boolean

  /**
   * Filter by recoverable status
   * */
  isRecoverable?: boolean

  /**
   * Include sandboxes created after this timestamp
   * */
  createdAtAfter?: Date

  /**
   * Include sandboxes created before this timestamp
   * */
  createdAtBefore?: Date

  /**
   * Include sandboxes with last activity after this timestamp
   * */
  lastActivityAfter?: Date

  /**
   * Include sandboxes with last activity before this timestamp
   * */
  lastActivityBefore?: Date
}

/**
 * A single point-in-time sample of historical Sandbox resource usage.
 *
 * @property {number} cpuCount - Number of CPU cores allocated to the Sandbox.
 * @property {number} cpuUsedPct - CPU utilization as a percentage of the allocated limit.
 * @property {number} diskTotal - Total disk space in bytes.
 * @property {number} diskUsed - Used disk space in bytes.
 * @property {number} memTotal - Total memory in bytes.
 * @property {number} memUsed - Used memory in bytes.
 * @property {number} memCache - Memory used by the page cache in bytes.
 * @property {Date} timestamp - Timestamp of this sample.
 */
export interface SandboxMetrics {
  cpuCount: number
  cpuUsedPct: number
  diskTotal: number
  diskUsed: number
  memTotal: number
  memUsed: number
  memCache: number
  timestamp: Date
}

const SANDBOX_METRIC_FIELD_BY_NAME: Record<string, keyof Omit<SandboxMetrics, 'timestamp'>> = {
  'daytona.sandbox.cpu.utilization': 'cpuUsedPct',
  'daytona.sandbox.cpu.limit': 'cpuCount',
  'daytona.sandbox.memory.usage': 'memUsed',
  'daytona.sandbox.memory.limit': 'memTotal',
  'daytona.sandbox.memory.cache': 'memCache',
  'daytona.sandbox.filesystem.usage': 'diskUsed',
  'daytona.sandbox.filesystem.total': 'diskTotal',
}

const SANDBOX_METRIC_NAMES: string[] = Object.keys(SANDBOX_METRIC_FIELD_BY_NAME)

function sandboxMetricsFromSystemMetrics(m: SystemMetrics): SandboxMetrics {
  return {
    cpuCount: m.cpuCount ?? 0,
    cpuUsedPct: m.cpuUsedPct ?? 0,
    diskTotal: m.diskTotal ?? 0,
    diskUsed: m.diskUsed ?? 0,
    memTotal: m.memTotal ?? 0,
    memUsed: m.memUsed ?? 0,
    memCache: m.memCache ?? 0,
    timestamp: m.timestamp ? new Date(m.timestamp) : new Date(),
  }
}

type SandboxMetricBuckets = Map<string, Partial<Record<keyof SandboxMetrics, number>>>

function buildSandboxMetrics(buckets: SandboxMetricBuckets): SandboxMetrics[] {
  return [...buckets.keys()].sort().map((ts) => {
    const v = buckets.get(ts)!
    return {
      cpuCount: v.cpuCount ?? 0,
      cpuUsedPct: v.cpuUsedPct ?? 0,
      diskTotal: v.diskTotal ?? 0,
      diskUsed: v.diskUsed ?? 0,
      memTotal: v.memTotal ?? 0,
      memUsed: v.memUsed ?? 0,
      memCache: v.memCache ?? 0,
      timestamp: new Date(ts),
    }
  })
}

type MetricTriple = [name: string | undefined, timestamp: string | undefined, value: number | undefined]

function pivotMetricTriples(triples: Iterable<MetricTriple>): SandboxMetrics[] {
  const buckets: SandboxMetricBuckets = new Map()
  for (const [name, timestamp, value] of triples) {
    const field = name ? SANDBOX_METRIC_FIELD_BY_NAME[name] : undefined
    if (!field || timestamp === undefined || value === undefined) continue
    const bucket = buckets.get(timestamp) ?? {}
    bucket[field] = value
    buckets.set(timestamp, bucket)
  }
  return buildSandboxMetrics(buckets)
}

function pivotSandboxMetrics(series: Array<MetricSeries> | undefined): SandboxMetrics[] {
  return pivotMetricTriples(
    (series ?? []).flatMap((s) => (s.dataPoints ?? []).map((p): MetricTriple => [s.metricName, p.timestamp, p.value])),
  )
}

function pivotSandboxMetricPoints(points: Array<ModelsMetricPoint> | undefined): SandboxMetrics[] {
  return pivotMetricTriples((points ?? []).map((p): MetricTriple => [p.metricName, p.timestamp, p.value]))
}
