# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from deprecated import deprecated
from pydantic import ConfigDict, PrivateAttr

from daytona_analytics_api_client_async import ApiClient as AnalyticsApiClient
from daytona_analytics_api_client_async import Configuration as AnalyticsConfiguration
from daytona_analytics_api_client_async import TelemetryApi
from daytona_api_client_async import (
    BuildInfo,
    ConfigApi,
    CreateSandboxSnapshot,
    ForkSandbox,
    PortPreviewUrl,
    ResizeSandbox,
    Sandbox as SandboxDto,
    SandboxApi,
    SandboxLabels,
    SandboxListItem,
    SandboxState,
    SandboxVolume,
    SignedPortPreviewUrl,
    SnapshotState,
    SnapshotsApi,
    SshAccessDto,
    SshAccessValidationDto,
    UpdateSandboxNetworkSettings,
    UpdateSandboxSecrets,
)
from daytona_toolbox_api_client_async import (
    ApiClient,
    ComputerUseApi,
    FileSystemApi,
    GitApi,
    InfoApi,
    InterpreterApi,
    LspApi,
    ProcessApi,
    ServerApi,
    SystemApi,
    UpdateEnvRequest,
)

from .._utils.errors import intercept_errors
from .._utils.otel_decorator import with_instrumentation
from .._utils.timeout import http_timeout, with_timeout
from ..common.daytona import CODE_TOOLBOX_LANGUAGE_LABEL
from ..common.errors import DaytonaError, DaytonaNotFoundError, DaytonaTimeoutError, DaytonaValidationError
from ..common.lsp_server import LspLanguageId, LspLanguageIdLiteral
from ..common.sandbox import (
    SANDBOX_METRIC_NAMES,
    Resources,
    SandboxMetrics,
    pivot_sandbox_metrics,
    sandbox_metrics_from_system_metrics,
)
from ..internal.pool_tracker import AsyncPoolSaturationTracker
from ..internal.toolbox_api_client_proxy import ToolboxApiClientProxy
from .code_interpreter import AsyncCodeInterpreter
from .computer_use import AsyncComputerUse
from .filesystem import AsyncFileSystem
from .git import AsyncGit
from .lsp_server import AsyncLspServer
from .process import AsyncProcess


class AsyncSandbox(SandboxDto):
    """Represents a Daytona Sandbox.

    Attributes:
        fs (AsyncFileSystem): File system operations interface.
        git (AsyncGit): Git operations interface.
        process (AsyncProcess): Process execution interface.
        computer_use (AsyncComputerUse): Computer use operations interface for desktop automation.
        code_interpreter (AsyncCodeInterpreter): Stateful interpreter interface for executing code.
            Currently supports only Python. For other languages, use the `process.code_run` interface.
        id (str): Unique identifier for the Sandbox.
        name (str): Name of the Sandbox.
        organization_id (str): Organization ID of the Sandbox.
        snapshot (str | None): Daytona snapshot used to create the Sandbox.
        user (str): OS user running in the Sandbox.
        env (dict[str, str] | None): Environment variables set in the Sandbox (not returned by list
            results; call `refresh_data()` on each item to populate).
        labels (dict[str, str]): Custom labels attached to the Sandbox.
        public (bool): Whether the Sandbox is publicly accessible.
        target (str): Target location of the runner where the Sandbox runs.
        cpu (int): Number of CPUs allocated to the Sandbox.
        gpu (int): Number of GPUs allocated to the Sandbox.
        memory (int): Amount of memory allocated to the Sandbox in GiB.
        disk (int): Amount of disk space allocated to the Sandbox in GiB.
        state (SandboxState | None): Current state of the Sandbox (e.g., "started", "stopped").
        error_reason (str | None): Error message if Sandbox is in error state.
        recoverable (bool | None): Whether the Sandbox error is recoverable.
        backup_state (str | None): Current state of Sandbox backup.
        backup_created_at (str | None): When the backup was created (not returned by list results;
            call `refresh_data()` on each item to populate).
        auto_stop_interval (int | None): Auto-stop interval in minutes.
        auto_pause_interval (int | None): Auto-pause interval in minutes (0 means disabled).
            Only supported for sandbox classes that support pausing.
            At most one of auto_stop_interval and auto_pause_interval may be non-zero.
        auto_archive_interval (int | None): Auto-archive interval in minutes.
        auto_delete_interval (int | None): Auto-delete interval in minutes.
        volumes (list[SandboxVolume] | None): Volumes attached to the Sandbox (not returned by list
            results; call `refresh_data()` on each item to populate).
        build_info (BuildInfo | None): Build information for the Sandbox if it was created from
            dynamic build (not returned by list results; call `refresh_data()` on each item to populate).
        created_at (str | None): When the Sandbox was created.
        updated_at (str | None): When the Sandbox was last updated.
        last_activity_at (str | None): When the Sandbox last had activity.
        network_block_all (bool | None): Whether to block all network access for the Sandbox
            (not returned by list results; call `refresh_data()` on each item to populate).
        network_allow_list (str | None): Comma-separated list of allowed CIDR network addresses for
            the Sandbox (not returned by list results; call `refresh_data()` on each item to populate).
        domain_allow_list (str | None): Comma-separated list of allowed domains for
            the Sandbox (not returned by list results; call `refresh_data()` on each item to populate).
        toolbox_proxy_url (str): The toolbox proxy URL for the Sandbox.
    """

    env: dict[str, str] | None = None  # pyright: ignore[reportRedeclaration]
    network_block_all: bool | None = None  # pyright: ignore[reportRedeclaration]

    _fs: AsyncFileSystem = PrivateAttr()
    _git: AsyncGit = PrivateAttr()
    _process: AsyncProcess = PrivateAttr()
    _computer_use: AsyncComputerUse = PrivateAttr()
    _code_interpreter: AsyncCodeInterpreter = PrivateAttr()

    # TODO: Remove model_config once everything is migrated to pydantic # pylint: disable=fixme
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        sandbox_dto: SandboxDto | SandboxListItem,
        toolbox_api: ApiClient,
        sandbox_api: SandboxApi,
        language: str,
        pool_tracker: AsyncPoolSaturationTracker | None = None,
        analytics_api_url_provider: Callable[[], Awaitable[str | None]] | None = None,
    ):
        super().__init__(**sandbox_dto.model_dump())
        self.__process_sandbox_dto(sandbox_dto)
        self._sandbox_api: SandboxApi = sandbox_api
        self._snapshots_api: SnapshotsApi = SnapshotsApi(self._sandbox_api.api_client)
        self._analytics_api_url_provider: Callable[[], Awaitable[str | None]] | None = analytics_api_url_provider
        # Wrap the toolbox API client to inject the sandbox ID into the resource path
        self._toolbox_api: ToolboxApiClientProxy[ApiClient] = ToolboxApiClientProxy(
            toolbox_api, self.id, self.toolbox_proxy_url, pool_tracker
        )

        self._fs = AsyncFileSystem(FileSystemApi(self._toolbox_api))
        self._git = AsyncGit(GitApi(self._toolbox_api))
        self._process = AsyncProcess(language, ProcessApi(self._toolbox_api))
        self._computer_use = AsyncComputerUse(ComputerUseApi(self._toolbox_api))
        self._code_interpreter = AsyncCodeInterpreter(InterpreterApi(self._toolbox_api))
        self._info_api: InfoApi = InfoApi(self._toolbox_api)
        self._server_api: ServerApi = ServerApi(self._toolbox_api)
        self._system_api: SystemApi = SystemApi(self._toolbox_api)

    @property
    def fs(self) -> AsyncFileSystem:
        return self._fs

    @property
    def git(self) -> AsyncGit:
        return self._git

    @property
    def process(self) -> AsyncProcess:
        return self._process

    @property
    def computer_use(self) -> AsyncComputerUse:
        return self._computer_use

    @property
    def code_interpreter(self) -> AsyncCodeInterpreter:
        return self._code_interpreter

    @intercept_errors(message_prefix="Failed to refresh sandbox data: ")
    @with_instrumentation()
    async def refresh_data(self, request_timeout: float | None = None) -> None:
        """Refreshes the Sandbox data from the API.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            await sandbox.refresh_data()
            print(f"Sandbox {sandbox.id}:")
            print(f"State: {sandbox.state}")
            print(f"Resources: {sandbox.cpu} CPU, {sandbox.memory} GiB RAM")
            ```
        """
        instance = await self._sandbox_api.get_sandbox(self.id, _request_timeout=http_timeout(request_timeout))
        self.__process_sandbox_dto(instance)

    @intercept_errors(message_prefix="Failed to get user home directory: ")
    @with_instrumentation()
    async def get_user_home_dir(self) -> str:
        """Gets the user's home directory path inside the Sandbox.

        Returns:
            str: The absolute path to the user's home directory inside the Sandbox.

        Example:
            ```python
            user_home_dir = await sandbox.get_user_home_dir()
            print(f"Sandbox user home: {user_home_dir}")
            ```
        """
        response = await self._info_api.get_user_home_dir()
        return response.dir

    @deprecated(
        reason=(
            "Method is deprecated. Use `get_user_home_dir` instead. This method will be removed in a future version."
        )
    )
    @with_instrumentation()
    async def get_user_root_dir(self) -> str:
        return await self.get_user_home_dir()

    @intercept_errors(message_prefix="Failed to get working directory path: ")
    @with_instrumentation()
    async def get_work_dir(self) -> str:
        """Gets the working directory path inside the Sandbox.

        Returns:
            str: The absolute path to the Sandbox working directory. Uses the WORKDIR specified in
            the Dockerfile if present, or falling back to the user's home directory if not.

        Example:
            ```python
            work_dir = await sandbox.get_work_dir()
            print(f"Sandbox working directory: {work_dir}")
            ```
        """
        response = await self._info_api.get_work_dir()
        return response.dir

    @intercept_errors(message_prefix="Failed to get sandbox metrics: ")
    @with_instrumentation()
    async def get_metrics_latest(self) -> SandboxMetrics:
        """Gets the most recent resource usage sample directly from the Sandbox daemon.

        Unlike :meth:`get_metrics`, which returns aggregated historical samples, this returns
        the single current reading without going through the telemetry backend.

        Returns:
            SandboxMetrics: The current CPU, memory, and disk usage sample for the Sandbox.
        """
        return sandbox_metrics_from_system_metrics(await self._system_api.get_system_metrics())

    @intercept_errors(message_prefix="Failed to get sandbox metrics: ")
    @with_instrumentation()
    async def get_metrics(self, start: datetime | None = None, end: datetime | None = None) -> list[SandboxMetrics]:
        """Gets historical time-series resource usage metrics for the Sandbox.

        When the deployment runs a dedicated Analytics API, metrics are fetched from it
        directly; otherwise they are fetched through the control-plane telemetry proxy.

        Args:
            start (datetime | None): Start of the time range. Defaults to the Sandbox
                creation time.
            end (datetime | None): End of the time range. Defaults to the current time.

        Returns:
            list[SandboxMetrics]: Time-ordered usage samples over the requested range.
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            start = datetime.fromisoformat(self.created_at.replace("Z", "+00:00")) if self.created_at else end

        analytics_api_url = await self._get_analytics_api_url()
        if analytics_api_url:
            telemetry_api = self._build_analytics_telemetry_api(analytics_api_url)
            try:
                points = await telemetry_api.organization_organization_id_sandbox_sandbox_id_telemetry_metrics_get(
                    self.organization_id,
                    self.id,
                    var_from=start.isoformat(),
                    to=end.isoformat(),
                    metric_names=",".join(SANDBOX_METRIC_NAMES),
                )
            finally:
                await telemetry_api.api_client.close()  # pyright: ignore[reportUnusedCallResult]
            return pivot_sandbox_metrics((p.metric_name, p.timestamp, p.value) for p in points)

        response = await self._sandbox_api.get_sandbox_metrics(
            self.id, var_from=start, to=end, metric_names=SANDBOX_METRIC_NAMES
        )
        return pivot_sandbox_metrics(
            (s.metric_name, dp.timestamp, dp.value) for s in response.series or [] for dp in s.data_points or []
        )

    async def _get_analytics_api_url(self) -> str | None:
        if self._analytics_api_url_provider is not None:
            return await self._analytics_api_url_provider()
        config = await ConfigApi(self._sandbox_api.api_client).config_controller_get_config()
        return config.analytics_api_url

    def _build_analytics_telemetry_api(self, analytics_api_url: str) -> TelemetryApi:
        client = AnalyticsApiClient(AnalyticsConfiguration(host=analytics_api_url))
        client.default_headers["Authorization"] = self._sandbox_api.api_client.default_headers["Authorization"]
        return TelemetryApi(client)

    @with_instrumentation()
    def create_lsp_server(
        self, language_id: LspLanguageId | LspLanguageIdLiteral, path_to_project: str
    ) -> AsyncLspServer:
        """Creates a new Language Server Protocol (LSP) server instance.

        The LSP server provides language-specific features like code completion,
        diagnostics, and more.

        Args:
            language_id (LspLanguageId | LspLanguageIdLiteral): The language server type (e.g., LspLanguageId.PYTHON).
            path_to_project (str): Path to the project root directory. Relative paths are resolved
            based on the sandbox working directory.

        Returns:
            LspServer: A new LSP server instance configured for the specified language.

        Example:
            ```python
            lsp = sandbox.create_lsp_server("python", "workspace/project")
            ```
        """
        return AsyncLspServer(
            language_id,
            path_to_project,
            LspApi(self._toolbox_api),
        )

    @intercept_errors(message_prefix="Failed to set labels: ")
    @with_instrumentation()
    async def set_labels(self, labels: dict[str, str], request_timeout: float | None = None) -> dict[str, str]:
        """Sets labels for the Sandbox.

        Labels are key-value pairs that can be used to organize and identify Sandboxes.

        Args:
            labels (dict[str, str]): Dictionary of key-value pairs representing Sandbox labels.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            dict[str, str]: Dictionary containing the updated Sandbox labels.

        Example:
            ```python
            new_labels = sandbox.set_labels({
                "project": "my-project",
                "environment": "development",
                "team": "backend"
            })
            print(f"Updated labels: {new_labels}")
            ```
        """
        self.labels = (
            await self._sandbox_api.replace_labels(
                self.id, SandboxLabels(labels=labels), _request_timeout=http_timeout(request_timeout)
            )
        ).labels
        return self.labels

    @intercept_errors(message_prefix="Failed to start sandbox: ")
    @with_timeout()
    @with_instrumentation()
    async def start(self, timeout: float | None = 60):
        """Starts the Sandbox and waits for it to be ready.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If sandbox fails to start or times out.

        Example:
            ```python
            sandbox = daytona.get("my-sandbox-id")
            sandbox.start(timeout=40)  # Wait up to 40 seconds
            print("Sandbox started successfully")
            ```
        """
        sandbox = await self._sandbox_api.start_sandbox(self.id, _request_timeout=http_timeout(timeout))
        self.__process_sandbox_dto(sandbox)
        # This method already handles a timeout, so we don't need to pass one to internal methods
        await self.wait_for_sandbox_start(timeout=0)

    @intercept_errors(message_prefix="Failed to recover sandbox: ")
    @with_timeout()
    async def recover(self, timeout: float | None = 60):
        """Recovers the Sandbox from a recoverable error and waits for it to be ready.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If sandbox fails to recover or times out.

        Example:
            ```python
            sandbox = daytona.get("my-sandbox-id")
            await sandbox.recover(timeout=40)  # Wait up to 40 seconds
            print("Sandbox recovered successfully")
            ```
        """
        sandbox = await self._sandbox_api.recover_sandbox(self.id, _request_timeout=http_timeout(timeout))
        self.__process_sandbox_dto(sandbox)
        # This method already handles a timeout, so we don't need to pass one to internal methods
        await self.wait_for_sandbox_start(timeout=0)

    @intercept_errors(message_prefix="Failed to stop sandbox: ")
    @with_timeout()
    @with_instrumentation()
    async def stop(self, timeout: float | None = 60, force: bool = False):
        """Stops the Sandbox and waits for it to be fully stopped.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.
            force (bool): If True, uses SIGKILL instead of SIGTERM to stop the sandbox. Default is False.

        Raises:
            DaytonaError: If timeout is negative; If sandbox fails to stop or times out

        Example:
            ```python
            sandbox = daytona.get("my-sandbox-id")
            await sandbox.stop()
            print("Sandbox stopped successfully")
            ```
        """
        _ = await self._sandbox_api.stop_sandbox(self.id, force=force, _request_timeout=http_timeout(timeout))
        await self.__refresh_data_safe()
        # This method already handles a timeout, so we don't need to pass one to internal methods
        await self.wait_for_sandbox_stop(timeout=0)

    @intercept_errors(message_prefix="Failed to remove sandbox: ")
    @with_timeout()
    @with_instrumentation()
    async def delete(self, timeout: float | None = 60) -> None:
        """Deletes the Sandbox.

        Args:
            timeout (float | None): Timeout (in seconds) for sandbox deletion. 0 means no timeout.
                Default is 60 seconds.
        """
        _ = await self._sandbox_api.delete_sandbox(self.id, _request_timeout=http_timeout(timeout))
        await self.__refresh_data_safe()

    @intercept_errors(message_prefix="Failure during waiting for sandbox to start: ")
    @with_timeout()
    @with_instrumentation()
    async def wait_for_sandbox_start(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox to reach the 'started' state. Polls the Sandbox status until it
        reaches the 'started' state, encounters an error or times out.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative; If Sandbox fails to start or times out
        """
        check_interval = 0.1
        start_time = asyncio.get_event_loop().time()

        while self.state != "started":
            await self.refresh_data()

            if self.state == "started":
                return

            if self.state in ["error", "build_failed"]:
                err_msg = (
                    f"Sandbox {self.id} failed to start with state: {self.state}, error reason: {self.error_reason}"
                )
                raise DaytonaError(err_msg)

            await asyncio.sleep(check_interval)
            if asyncio.get_event_loop().time() - start_time > 5:
                check_interval = min(check_interval * 1.1, 1.0)

    @intercept_errors(message_prefix="Failure during waiting for sandbox to stop: ")
    @with_timeout()
    @with_instrumentation()
    async def wait_for_sandbox_stop(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox to reach the 'stopped' state. Polls the Sandbox status until it
        reaches the 'stopped' state, encounters an error or times out. It will wait up to 60 seconds
        for the Sandbox to stop.
        Treats destroyed as stopped to cover ephemeral sandboxes that are automatically deleted after stopping.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If Sandbox fails to stop or times out.
        """
        check_interval = 0.1
        start_time = asyncio.get_event_loop().time()

        while self.state not in ["stopped", "destroyed"]:
            try:
                await self.__refresh_data_safe()

                if self.state in ["error", "build_failed"]:
                    err_msg = (
                        f"Sandbox {self.id} failed to stop with status: {self.state}, error reason: {self.error_reason}"
                    )
                    raise DaytonaError(err_msg)
            except Exception as e:
                # If there's a validation error, continue waiting
                if "validation error" not in str(e):
                    raise e

            await asyncio.sleep(check_interval)
            if asyncio.get_event_loop().time() - start_time > 5:
                check_interval = min(check_interval * 1.1, 1.0)

    @intercept_errors(message_prefix="Failed to set auto-stop interval: ")
    @with_instrumentation()
    async def set_autostop_interval(self, interval: int, request_timeout: float | None = None) -> None:
        """Sets the auto-stop interval for the Sandbox.

        The Sandbox will automatically stop after being idle (no new events) for the specified interval.
        Events include any state changes or interactions with the Sandbox through the SDK.
        Interactions using Sandbox Previews are not included.

        Args:
            interval (int): Number of minutes of inactivity before auto-stopping.
                Set to 0 to disable auto-stop. Defaults to 15.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Raises:
            DaytonaValidationError: If interval is negative

        Example:
            ```python
            # Auto-stop after 1 hour
            sandbox.set_autostop_interval(60)
            # Or disable auto-stop
            sandbox.set_autostop_interval(0)
            ```
        """
        if interval < 0:
            raise DaytonaValidationError("Auto-stop interval must be a non-negative integer")

        _ = await self._sandbox_api.set_autostop_interval(
            self.id, interval, _request_timeout=http_timeout(request_timeout)
        )
        self.auto_stop_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-pause interval: ")
    @with_instrumentation()
    async def set_auto_pause_interval(self, interval: int) -> None:
        """Sets the auto-pause interval for the Sandbox.

        The Sandbox will automatically pause after being idle (no new events) for the specified interval.
        Only supported for sandbox classes that support pausing.

        Args:
            interval (int): Number of minutes of inactivity before auto-pausing.
                Set to 0 to disable auto-pause.

        Raises:
            DaytonaValidationError: If interval is negative

        Example:
            ```python
            # Auto-pause after 1 hour
            await sandbox.set_auto_pause_interval(60)
            # Or disable auto-pause
            await sandbox.set_auto_pause_interval(0)
            ```
        """
        if interval < 0:
            raise DaytonaValidationError("Auto-pause interval must be a non-negative integer")

        _ = await self._sandbox_api.set_auto_pause_interval(self.id, interval)
        self.auto_pause_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-archive interval: ")
    @with_instrumentation()
    async def set_auto_archive_interval(self, interval: int, request_timeout: float | None = None) -> None:
        """Sets the auto-archive interval for the Sandbox.

        The Sandbox will automatically archive after being continuously stopped for the specified interval.

        Args:
            interval (int): Number of minutes after which a continuously stopped Sandbox will be auto-archived.
                Set to 0 for the maximum interval. Default is 7 days.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Raises:
            DaytonaValidationError: If interval is negative

        Example:
            ```python
            # Auto-archive after 1 hour
            sandbox.set_auto_archive_interval(60)
            # Or use the maximum interval
            sandbox.set_auto_archive_interval(0)
            ```
        """
        if interval < 0:
            raise DaytonaValidationError("Auto-archive interval must be a non-negative integer")

        _ = await self._sandbox_api.set_auto_archive_interval(
            self.id, interval, _request_timeout=http_timeout(request_timeout)
        )
        self.auto_archive_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-delete interval: ")
    @with_instrumentation()
    async def set_auto_delete_interval(self, interval: int, request_timeout: float | None = None) -> None:
        """Sets the auto-delete interval for the Sandbox.

        The Sandbox will automatically delete after being continuously stopped for the specified interval.

        Args:
            interval (int): Number of minutes after which a continuously stopped Sandbox will be auto-deleted.
                Set to negative value to disable auto-delete. Set to 0 to delete immediately upon stopping.
                By default, auto-delete is disabled.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # Auto-delete after 1 hour
            sandbox.set_auto_delete_interval(60)
            # Or delete immediately upon stopping
            sandbox.set_auto_delete_interval(0)
            # Or disable auto-delete
            sandbox.set_auto_delete_interval(-1)
            ```
        """
        _ = await self._sandbox_api.set_auto_delete_interval(
            self.id, interval, _request_timeout=http_timeout(request_timeout)
        )
        self.auto_delete_interval = interval

    @intercept_errors(message_prefix="Failed to update network settings: ")
    @with_instrumentation()
    async def update_network_settings(
        self,
        *,
        network_block_all: bool | None = None,
        network_allow_list: str | None = None,
        domain_allow_list: str | None = None,
        request_timeout: float | None = None,
    ) -> None:
        """Updates outbound network policy on the runner (block all, restore access, or CIDR allow list).

        Args:
            network_block_all: When ``True``, blocks all outbound traffic. When ``False``, restores general
                outbound access (and clears a stored allow list).
            network_allow_list: Comma-separated IPv4 CIDRs to allow; implies not blocking all.
            domain_allow_list: Comma-separated domains to allow; implies not blocking all.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Raises:
            DaytonaValidationError: If neither argument is set.

        Example:
            ```python
            await sandbox.update_network_settings(network_block_all=True)
            await sandbox.update_network_settings(network_block_all=False)
            ```
        """
        if network_block_all is None and network_allow_list is None and domain_allow_list is None:
            raise DaytonaValidationError(
                "At least one of network_block_all, network_allow_list or domain_allow_list must be set"
            )

        body = UpdateSandboxNetworkSettings(
            network_block_all=network_block_all,
            network_allow_list=network_allow_list,
            domain_allow_list=domain_allow_list,
        )
        updated = await self._sandbox_api.update_network_settings(
            self.id, body, _request_timeout=http_timeout(request_timeout)
        )
        self.network_block_all = updated.network_block_all
        self.network_allow_list = updated.network_allow_list
        self.domain_allow_list = updated.domain_allow_list

    @intercept_errors(message_prefix="Failed to update secrets: ")
    @with_instrumentation()
    async def update_secrets(self, secrets: dict[str, str]) -> None:
        """Updates the set of vault secrets mounted in the Sandbox, replacing the previously mounted set.

        Attached, detached and rotated secrets take effect for outbound requests within seconds.
        New environment variables only become visible to processes spawned after the update, and a
        Sandbox created without any secrets must be restarted for newly attached secrets to work.

        Args:
            secrets (dict[str, str]): Map of environment variable name to the name of an existing
                organization Secret. Pass an empty dict to detach all secrets.

        Example:
            ```python
            await sandbox.update_secrets({"ANTHROPIC_API_KEY": "anthropic-prod"})
            await sandbox.update_secrets({})  # detach all
            ```
        """
        body = UpdateSandboxSecrets(secrets=[{env_var: secret_name} for env_var, secret_name in secrets.items()])
        updated = await self._sandbox_api.update_sandbox_secrets(self.id, body)
        self.__process_sandbox_dto(updated)

    @intercept_errors(message_prefix="Failed to update environment: ")
    @with_instrumentation()
    async def update_env(self, env: dict[str, str], *, unset: list[str] | None = None) -> None:
        """Updates the Sandbox daemon's process environment.

        Newly spawned processes, sessions and PTYs inherit the change; already-running processes
        keep their environment.

        Args:
            env (dict[str, str]): Environment variables to set.
            unset (list[str] | None): Environment variable names to remove before `env` is applied.

        Example:
            ```python
            await sandbox.update_env({"MY_VAR": "value"}, unset=["OLD_VAR"])
            ```
        """
        request = UpdateEnvRequest(set=env, unset=unset)
        _ = await self._server_api.update_env(request=request)

    @intercept_errors(message_prefix="Failed to get preview link: ")
    @with_instrumentation()
    async def get_preview_link(self, port: int, request_timeout: float | None = None) -> PortPreviewUrl:
        """Retrieves the preview link for the sandbox at the specified port. If the port is closed,
        it will be opened automatically. For private sandboxes, a token is included to grant access
        to the URL.

        Args:
            port (int): The port to open the preview link on.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            PortPreviewUrl: The response object for the preview link, which includes the `url`
            and the `token` (to access private sandboxes).

        Example:
            ```python
            preview_link = sandbox.get_preview_link(3000)
            print(f"Preview URL: {preview_link.url}")
            print(f"Token: {preview_link.token}")
            ```
        """
        return await self._sandbox_api.get_port_preview_url(
            self.id, port, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to create signed preview url: ")
    async def create_signed_preview_url(
        self, port: int, expires_in_seconds: int | None = None, request_timeout: float | None = None
    ) -> SignedPortPreviewUrl:
        """Creates a signed preview URL for the sandbox at the specified port.

        Args:
            port (int): The port to open the preview link on.
            expires_in_seconds (int | None): The number of seconds the signed preview
                url will be valid for. Defaults to 60 seconds.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            SignedPortPreviewUrl: The response object for the signed preview url.
        """
        return await self._sandbox_api.get_signed_port_preview_url(
            self.id, port, expires_in_seconds=expires_in_seconds, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to expire signed preview url: ")
    async def expire_signed_preview_url(self, port: int, token: str, request_timeout: float | None = None) -> None:
        """Expires a signed preview URL for the sandbox at the specified port.

        Args:
            port (int): The port to expire the signed preview url on.
            token (str): The token to expire the signed preview url on.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        await self._sandbox_api.expire_signed_port_preview_url(
            self.id, port, token, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to archive sandbox: ")
    @with_instrumentation()
    async def archive(self, request_timeout: float | None = None) -> None:
        """Archives the sandbox, making it inactive and preserving its state. When sandboxes are
        archived, the entire filesystem state is moved to cost-effective object storage, making it
        possible to keep sandboxes available for an extended period. The tradeoff between archived
        and stopped states is that starting an archived sandbox takes more time, depending on its size.
        Sandbox must be stopped before archiving.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        _ = await self._sandbox_api.archive_sandbox(self.id, _request_timeout=http_timeout(request_timeout))
        await self.refresh_data(request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to resize sandbox: ")
    @with_timeout()
    @with_instrumentation()
    async def resize(self, resources: Resources, timeout: float | None = 60) -> None:
        """Resizes the Sandbox resources.

        Changes the CPU, memory, or disk allocation. Hot resize (on a running Sandbox) accepts
        only CPU and memory increases. Disk resize requires a stopped Sandbox; disk can only
        grow. GPU is not resizable — to change GPU, create a new Sandbox.

        Args:
            resources (Resources): New resource configuration. Only cpu, memory, and disk are
                applied; setting gpu or gpu_type raises an error.
            timeout (Optional[float]): Timeout in seconds for the resize operation. 0 means no
                timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If hot-resize constraints are violated, disk resize is attempted on
                a running Sandbox, disk decrease is attempted, no fields are provided, gpu or
                gpu_type is set, or the operation times out.

        Example:
            ```python
            await sandbox.resize(Resources(cpu=4, memory=8))

            await sandbox.stop()
            await sandbox.resize(Resources(cpu=2, memory=4, disk=30))
            ```
        """
        if resources.gpu is not None or resources.gpu_type is not None:
            raise DaytonaValidationError(
                "Resize does not support changes to gpu or gpu_type — to change GPU, create a new Sandbox"
            )
        resize_request = ResizeSandbox(
            cpu=resources.cpu,
            memory=resources.memory,
            disk=resources.disk,
        )
        sandbox = await self._sandbox_api.resize_sandbox(self.id, resize_request, _request_timeout=timeout or None)
        self.__process_sandbox_dto(sandbox)
        await self.wait_for_resize_complete(timeout=0)

    @intercept_errors(message_prefix="Failure during waiting for resize to complete: ")
    @with_timeout()
    @with_instrumentation()
    async def wait_for_resize_complete(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox resize operation to complete. Polls the Sandbox status until
        the state is no longer 'resizing'.

        Args:
            timeout (Optional[float]): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If resize operation times out.
        """
        check_interval = 0.1
        start_time = asyncio.get_event_loop().time()

        while self.state == "resizing":
            await self.refresh_data()

            if self.state in ["error", "build_failed"]:
                err_msg = f"Sandbox {self.id} resize failed with state: {self.state}, error reason: {self.error_reason}"
                raise DaytonaError(err_msg)

            if self.state != "resizing":
                return

            await asyncio.sleep(check_interval)
            if asyncio.get_event_loop().time() - start_time > 5:
                check_interval = min(check_interval * 1.1, 1.0)

    @intercept_errors(message_prefix="Failed to create SSH access: ")
    @with_instrumentation()
    async def create_ssh_access(
        self, expires_in_minutes: int | None = None, request_timeout: float | None = None
    ) -> SshAccessDto:
        """Creates an SSH access token for the sandbox.

        Args:
            expires_in_minutes (int | None): The number of minutes the SSH access token will be valid for.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        return await self._sandbox_api.create_ssh_access(
            self.id, expires_in_minutes=expires_in_minutes, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to revoke SSH access: ")
    @with_instrumentation()
    async def revoke_ssh_access(self, token: str, request_timeout: float | None = None) -> None:
        """Revokes an SSH access token for the sandbox.

        Args:
            token (str): The token to revoke.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        _ = await self._sandbox_api.revoke_ssh_access(self.id, token, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to validate SSH access: ")
    @with_instrumentation()
    async def validate_ssh_access(self, token: str, request_timeout: float | None = None) -> SshAccessValidationDto:
        """Validates an SSH access token for the sandbox.

        Args:
            token (str): The token to validate.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        return await self._sandbox_api.validate_ssh_access(token, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to refresh sandbox activity: ")
    async def refresh_activity(self, request_timeout: float | None = None) -> None:
        """Refreshes the sandbox activity to reset the timer for automated lifecycle management actions.

        This method updates the sandbox's last activity timestamp without changing its state.
        It is useful for keeping long-running sessions alive while there is still user activity.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            await sandbox.refresh_activity()
            ```
        """
        await self._sandbox_api.update_last_activity(self.id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to fork sandbox: ")
    @with_timeout()
    @with_instrumentation()
    async def _experimental_fork(self, name: str | None = None, timeout: float | None = 60) -> "AsyncSandbox":
        """Forks the Sandbox, creating a new Sandbox with an identical filesystem.

        The forked Sandbox is a copy-on-write clone of the original. It starts
        with the same disk contents but operates independently from that point on.

        Args:
            name (str | None): Optional name for the forked Sandbox. If not provided, a unique name will be generated.
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Returns:
            AsyncSandbox: The forked Sandbox.

        Raises:
            DaytonaError: If the fork operation fails or times out.

        Example:
            ```python
            sandbox = await daytona.get("my-sandbox")
            forked = await sandbox._experimental_fork(name="my-fork")
            print(f"Forked sandbox: {forked.id}")
            ```
        """
        sandbox_dto = await self._sandbox_api.fork_sandbox(
            self.id, ForkSandbox(name=name), _request_timeout=http_timeout(timeout)
        )

        language = sandbox_dto.labels.get(CODE_TOOLBOX_LANGUAGE_LABEL) or ""

        forked = AsyncSandbox(
            sandbox_dto,
            self._toolbox_api._api_client,
            self._sandbox_api,
            language,
            analytics_api_url_provider=self._analytics_api_url_provider,
        )
        await forked.wait_for_sandbox_start(timeout=0)
        return forked

    @intercept_errors(message_prefix="Failed to create snapshot: ")
    @with_instrumentation()
    async def _experimental_create_snapshot(self, name: str, timeout: float | None = 60) -> None:
        """Creates a snapshot from the current state of the Sandbox.

        This captures the Sandbox's filesystem into a reusable snapshot that can be
        used to create new Sandboxes. The method waits for the accepted snapshot
        to become active.

        Args:
            name (str): Name for the new snapshot.
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If the snapshot operation fails or times out.

        Example:
            ```python
            sandbox = await daytona.get("my-sandbox")
            await sandbox._experimental_create_snapshot("my-snapshot")
            print("Snapshot created successfully")
            ```
        """
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        accepted = await self._sandbox_api.create_sandbox_snapshot(
            self.id, CreateSandboxSnapshot(name=name), _request_timeout=http_timeout(timeout)
        )
        snapshot_id = accepted.id if accepted else None
        if not snapshot_id:
            raise DaytonaError("Failed to create snapshot. Didn't receive a snapshot ID from the server API.")

        deadline = None if timeout in (None, 0) else start_time + timeout
        await self.__wait_for_snapshot_complete(snapshot_id, deadline)

    @intercept_errors(message_prefix="Failed to pause sandbox")
    @with_instrumentation()
    async def pause(self, timeout: float = 60) -> None:
        """Pauses the Sandbox, freezing all running processes.

        The Sandbox will enter a 'pausing' state and transition to 'paused' when
        complete. While paused, the Sandbox retains its state in memory but does
        not consume CPU cycles.

        Args:
            timeout: Maximum time to wait in seconds. 0 means no timeout.
                    Defaults to 60-second timeout.

        Raises:
            DaytonaError: If timeout is negative or the operation fails/times out.
        """
        if timeout < 0:
            raise DaytonaError("Timeout must be a non-negative number")

        start_time = time.time()
        _ = await self._sandbox_api.pause_sandbox(self.id, _request_timeout=timeout if timeout > 0 else None)
        await self.refresh_data()

        elapsed = time.time() - start_time
        remaining = max(0.001, timeout - elapsed) if timeout > 0 else 0

        check_interval = 0.1
        wait_start = time.time()
        while self.state == "pausing":
            await self.refresh_data()
            if self.state == "error":
                raise DaytonaError(
                    f"Sandbox {self.id} pause failed with state: {self.state}, error reason: {self.error_reason}"
                )
            if 0 < remaining <= time.time() - wait_start:
                raise DaytonaError(f"Sandbox {self.id} failed to pause within {timeout} seconds")
            await asyncio.sleep(check_interval)
            check_interval = min(check_interval * 1.5, 1.0)

    async def __wait_for_snapshot_complete(self, snapshot_id: str, deadline: float | None) -> None:
        check_interval = 0.1
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while True:
            if deadline is not None and loop.time() >= deadline:
                raise DaytonaTimeoutError(
                    f"Timed out waiting for snapshot {snapshot_id}; capture continues on the server"
                )

            snapshot = await self._snapshots_api.get_snapshot(snapshot_id)
            if not snapshot or snapshot.id != snapshot_id:
                raise DaytonaError(f"Snapshot lookup for {snapshot_id} returned an invalid response")

            if snapshot.state == SnapshotState.ACTIVE:
                try:
                    await self.refresh_data()
                except Exception:  # Best-effort local cleanup after definitive server success.
                    pass
                return
            if snapshot.state in (SnapshotState.ERROR, SnapshotState.BUILD_FAILED):
                state = snapshot.state.value if isinstance(snapshot.state, SnapshotState) else snapshot.state
                raise DaytonaError(
                    f"Snapshot {snapshot.id} failed with state: {state}, error reason: {snapshot.error_reason}"
                )

            await asyncio.sleep(check_interval)
            if loop.time() - start_time > 5:
                check_interval = min(check_interval * 1.1, 1.0)

    def __process_sandbox_dto(self, sandbox_dto: SandboxDto | SandboxListItem) -> None:
        self.id: str = sandbox_dto.id
        self.name: str = sandbox_dto.name
        self.organization_id: str = sandbox_dto.organization_id
        self.snapshot: str | None = sandbox_dto.snapshot
        self.user: str = sandbox_dto.user
        self.labels: dict[str, str] = sandbox_dto.labels
        self.public: bool = sandbox_dto.public
        self.target: str = sandbox_dto.target
        self.cpu: float | int = sandbox_dto.cpu
        self.gpu: float | int = sandbox_dto.gpu
        self.memory: float | int = sandbox_dto.memory
        self.disk: float | int = sandbox_dto.disk
        self.state: SandboxState | None = sandbox_dto.state
        self.error_reason: str | None = sandbox_dto.error_reason
        self.recoverable: bool | None = sandbox_dto.recoverable
        self.backup_state: str | None = sandbox_dto.backup_state
        self.auto_stop_interval: float | int | None = sandbox_dto.auto_stop_interval
        self.auto_pause_interval: float | int | None = sandbox_dto.auto_pause_interval
        self.auto_archive_interval: float | int | None = sandbox_dto.auto_archive_interval
        self.auto_delete_interval: float | int | None = sandbox_dto.auto_delete_interval
        self.created_at: str | None = sandbox_dto.created_at
        self.updated_at: str | None = sandbox_dto.updated_at
        self.last_activity_at: str | None = sandbox_dto.last_activity_at
        self.toolbox_proxy_url: str = sandbox_dto.toolbox_proxy_url

        # Fields only present in the full SandboxDto (not returned by list results
        if isinstance(sandbox_dto, SandboxDto):
            self.env: dict[str, str] | None = sandbox_dto.env  # pyright: ignore[reportIncompatibleVariableOverride]
            self.network_block_all: bool | None = (  # pyright: ignore[reportIncompatibleVariableOverride]
                sandbox_dto.network_block_all
            )
            self.network_allow_list: str | None = sandbox_dto.network_allow_list
            self.domain_allow_list: str | None = sandbox_dto.domain_allow_list
            self.volumes: list[SandboxVolume] | None = sandbox_dto.volumes
            self.build_info: BuildInfo | None = sandbox_dto.build_info
            self.backup_created_at: str | None = sandbox_dto.backup_created_at

    async def __refresh_data_safe(self) -> None:
        """Refreshes the Sandbox data from the API, but does not throw an error if the sandbox has been deleted.
        Instead, it sets the state to destroyed.
        """
        try:
            await self.refresh_data()
        except DaytonaNotFoundError:
            self.state = SandboxState.DESTROYED
