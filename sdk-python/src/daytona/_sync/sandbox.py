# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import functools
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import httpx
from deprecated import deprecated
from pydantic import ConfigDict, PrivateAttr

from daytona_analytics_api_client import ApiClient as AnalyticsApiClient
from daytona_analytics_api_client import Configuration as AnalyticsConfiguration
from daytona_analytics_api_client import TelemetryApi
from daytona_api_client import BuildInfo, ConfigApi, CreateSandboxSnapshot, ForkSandbox, PortPreviewUrl, ResizeSandbox
from daytona_api_client import Sandbox as SandboxDto
from daytona_api_client import (
    SandboxApi,
    SandboxLabels,
    SandboxListItem,
    SandboxState,
    SandboxVolume,
    SignedPortPreviewUrl,
    SshAccessDto,
    SshAccessValidationDto,
    UpdateSandboxNetworkSettings,
    UpdateSandboxSecrets,
)
from daytona_toolbox_api_client import (
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

from .._utils.errors import intercept_errors, is_validation_error
from .._utils.file_url_signing import SIGNING_KEY_CACHE_TTL_SECONDS, build_signed_file_url
from .._utils.otel_decorator import with_instrumentation
from .._utils.timeout import http_timeout, with_timeout
from ..common.daytona import CODE_TOOLBOX_LANGUAGE_LABEL
from ..common.errors import DaytonaError, DaytonaNotFoundError, DaytonaValidationError
from ..common.lsp_server import LspLanguageId, LspLanguageIdLiteral
from ..common.sandbox import (
    SANDBOX_METRIC_NAMES,
    Resources,
    SandboxMetrics,
    pivot_sandbox_metrics,
    sandbox_metrics_from_system_metrics,
)
from ..internal.event_subscription_manager import SyncEventSubscriptionManager
from ..internal.toolbox_api_client_proxy import ToolboxApiClientProxy
from .code_interpreter import CodeInterpreter
from .computer_use import ComputerUse
from .filesystem import FileSystem
from .git import Git
from .lsp_server import LspServer
from .process import Process

_T = TypeVar("_T")


def with_events(cls: _T) -> _T:
    for name in list(vars(cls)):
        if name.startswith("_"):
            continue
        method = vars(cls)[name]
        if not callable(method):
            continue

        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, _m: Any = method, **kwargs: Any) -> Any:
            if getattr(self, "__pydantic_private__", None) is not None:
                self._ensure_subscribed()
            return _m(self, *args, **kwargs)

        setattr(cls, name, wrapper)
    return cls


@with_events
class Sandbox(SandboxDto):
    """Represents a Daytona Sandbox.

    Attributes:
        fs (FileSystem): File system operations interface.
        git (Git): Git operations interface.
        process (Process): Process execution interface.
        computer_use (ComputerUse): Computer use operations interface for desktop automation.
        code_interpreter (CodeInterpreter): Stateful interpreter interface for executing code.
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

    _fs: FileSystem = PrivateAttr()
    _git: Git = PrivateAttr()
    _process: Process = PrivateAttr()
    _computer_use: ComputerUse = PrivateAttr()
    _code_interpreter: CodeInterpreter = PrivateAttr()
    _state_waiters: list[Callable[[SandboxState | None], None]] = PrivateAttr(default_factory=list)
    _state_waiters_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _sub_id: str | None = PrivateAttr(default=None)

    # TODO: Remove model_config once everything is migrated to pydantic # pylint: disable=fixme
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        sandbox_dto: SandboxDto | SandboxListItem,
        toolbox_api: ApiClient,
        sandbox_api: SandboxApi,
        language: str,
        subscription_manager: SyncEventSubscriptionManager,
        http_client: httpx.Client,
        analytics_api_url_provider: Callable[[], str | None] | None = None,
    ):
        """Initialize a new Sandbox instance.

        Args:
            sandbox_dto (SandboxDto | SandboxListItem): The sandbox data from the API.
            toolbox_api (ApiClient): API client for toolbox operations.
            sandbox_api (SandboxApi): API client for Sandbox operations.
            subscription_manager: SyncEventSubscriptionManager for real-time updates.
            http_client (httpx.Client): Shared pooled client for file transfers.
        """
        super().__init__(**sandbox_dto.model_dump())
        self.__process_sandbox_dto(sandbox_dto)
        self._sandbox_api: SandboxApi = sandbox_api
        self._analytics_api_url_provider: Callable[[], str | None] | None = analytics_api_url_provider
        self._http_client: httpx.Client = http_client
        self._subscription_manager: SyncEventSubscriptionManager = subscription_manager
        if not self.toolbox_proxy_url:
            proxy_url = self._sandbox_api.get_toolbox_proxy_url(self.id)
            self.toolbox_proxy_url = proxy_url.url
        self._signing_key: str | None = None
        self._signing_key_fetched_at: float = 0
        # Wrap the toolbox API client to inject the sandbox ID into the resource path
        self._toolbox_api: ToolboxApiClientProxy[ApiClient] = ToolboxApiClientProxy(
            toolbox_api, self.id, self.toolbox_proxy_url
        )

        self._fs = FileSystem(FileSystemApi(self._toolbox_api), http_client=http_client)
        self._git = Git(GitApi(self._toolbox_api))
        self._process = Process(
            language,
            ProcessApi(self._toolbox_api),
            http_client=http_client,
        )
        self._computer_use = ComputerUse(ComputerUseApi(self._toolbox_api), http_client=http_client)
        self._code_interpreter = CodeInterpreter(
            InterpreterApi(self._toolbox_api),
            http_client=http_client,
        )
        self._info_api: InfoApi = InfoApi(self._toolbox_api)
        self._server_api: ServerApi = ServerApi(self._toolbox_api)
        self._system_api: SystemApi = SystemApi(self._toolbox_api)

        self._ensure_subscribed()

    @property
    def fs(self) -> FileSystem:
        return self._fs

    @property
    def git(self) -> Git:
        return self._git

    @property
    def process(self) -> Process:
        return self._process

    @property
    def computer_use(self) -> ComputerUse:
        return self._computer_use

    @property
    def code_interpreter(self) -> CodeInterpreter:
        return self._code_interpreter

    @intercept_errors(message_prefix="Failed to refresh sandbox data: ")
    @with_instrumentation()
    def refresh_data(self, request_timeout: float | None = None) -> None:
        """Refreshes the Sandbox data from the API.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            sandbox.refresh_data()
            print(f"Sandbox {sandbox.id}:")
            print(f"State: {sandbox.state}")
            print(f"Resources: {sandbox.cpu} CPU, {sandbox.memory} GiB RAM")
            ```
        """
        instance = self._sandbox_api.get_sandbox(self.id, _request_timeout=http_timeout(request_timeout))
        self.__process_sandbox_dto(instance)

    @intercept_errors(message_prefix="Failed to get user home directory: ")
    @with_instrumentation()
    def get_user_home_dir(self) -> str:
        """Gets the user's home directory path inside the Sandbox.

        Returns:
            str: The absolute path to the user's home directory inside the Sandbox.

        Example:
            ```python
            user_home_dir = sandbox.get_user_home_dir()
            print(f"Sandbox user home: {user_home_dir}")
            ```
        """
        response = self._info_api.get_user_home_dir()
        return response.dir

    @deprecated(
        reason=(
            "Method is deprecated. Use `get_user_home_dir` instead. This method will be removed in a future version."
        )
    )
    @with_instrumentation()
    def get_user_root_dir(self) -> str:
        return self.get_user_home_dir()

    @intercept_errors(message_prefix="Failed to get working directory path: ")
    @with_instrumentation()
    def get_work_dir(self) -> str:
        """Gets the working directory path inside the Sandbox.

        Returns:
            str: The absolute path to the Sandbox working directory. Uses the WORKDIR specified in
            the Dockerfile if present, or falling back to the user's home directory if not.

        Example:
            ```python
            work_dir = sandbox.get_work_dir()
            print(f"Sandbox working directory: {work_dir}")
            ```
        """
        response = self._info_api.get_work_dir()
        return response.dir

    @intercept_errors(message_prefix="Failed to get sandbox metrics: ")
    @with_instrumentation()
    def get_metrics_latest(self) -> SandboxMetrics:
        """Gets the most recent resource usage sample directly from the Sandbox daemon.

        Unlike :meth:`get_metrics`, which returns aggregated historical samples, this returns
        the single current reading without going through the telemetry backend.

        Returns:
            SandboxMetrics: The current CPU, memory, and disk usage sample for the Sandbox.
        """
        return sandbox_metrics_from_system_metrics(self._system_api.get_system_metrics())

    @intercept_errors(message_prefix="Failed to get sandbox metrics: ")
    @with_instrumentation()
    def get_metrics(self, start: datetime | None = None, end: datetime | None = None) -> list[SandboxMetrics]:
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

        analytics_api_url = self._get_analytics_api_url()
        if analytics_api_url:
            points = self._build_analytics_telemetry_api(
                analytics_api_url
            ).organization_organization_id_sandbox_sandbox_id_telemetry_metrics_get(
                self.organization_id,
                self.id,
                var_from=start.isoformat(),
                to=end.isoformat(),
                metric_names=",".join(SANDBOX_METRIC_NAMES),
            )
            return pivot_sandbox_metrics((p.metric_name, p.timestamp, p.value) for p in points)

        response = self._sandbox_api.get_sandbox_metrics(
            self.id, var_from=start, to=end, metric_names=SANDBOX_METRIC_NAMES
        )
        return pivot_sandbox_metrics(
            (s.metric_name, dp.timestamp, dp.value) for s in response.series or [] for dp in s.data_points or []
        )

    def _get_analytics_api_url(self) -> str | None:
        if self._analytics_api_url_provider is not None:
            return self._analytics_api_url_provider()
        return ConfigApi(self._sandbox_api.api_client).config_controller_get_config().analytics_api_url

    def _build_analytics_telemetry_api(self, analytics_api_url: str) -> TelemetryApi:
        client = AnalyticsApiClient(AnalyticsConfiguration(host=analytics_api_url))
        client.default_headers["Authorization"] = self._sandbox_api.api_client.default_headers["Authorization"]
        return TelemetryApi(client)

    @with_instrumentation()
    def create_lsp_server(self, language_id: LspLanguageId | LspLanguageIdLiteral, path_to_project: str) -> LspServer:
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
        return LspServer(
            language_id,
            path_to_project,
            LspApi(self._toolbox_api),
        )

    @intercept_errors(message_prefix="Failed to set labels: ")
    @with_instrumentation()
    def set_labels(self, labels: dict[str, str], request_timeout: float | None = None) -> dict[str, str]:
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
            self._sandbox_api.replace_labels(
                self.id,
                SandboxLabels(labels=labels),
                _request_timeout=http_timeout(request_timeout),
            )
        ).labels
        return self.labels

    def _ensure_signing_key(self) -> str:
        key = self._signing_key
        if key is None or (time.monotonic() - self._signing_key_fetched_at) > SIGNING_KEY_CACHE_TTL_SECONDS:
            key = self._sandbox_api.get_sandbox_signing_key(self.id)
            self._signing_key = key
            self._signing_key_fetched_at = time.monotonic()
        return key

    @intercept_errors(message_prefix="Failed to create download URL: ")
    @with_instrumentation()
    def download_url(self, path: str, ttl_seconds: int | None = None) -> str:
        """Creates a pre-signed URL for downloading a file from the Sandbox.

        The URL works with any HTTP client without auth headers and stays valid across
        sandbox restarts (downloads succeed only while the sandbox is running). The signing
        key is cached locally for up to 15 seconds; if the key was rotated from another
        client, URLs may be rejected until the cache refreshes.

        Args:
            path (str): Path to the file in the Sandbox.
            ttl_seconds (int | None): How long the URL stays valid, in seconds.
                Defaults to 3600. Zero or negative means the URL never expires.

        Returns:
            str: Pre-signed download URL.

        Example:
            ```python
            url = sandbox.download_url("/home/user/report.pdf")
            ```
            ```bash
            curl "$url" -o report.pdf
            ```
        """
        return build_signed_file_url(
            self.toolbox_proxy_url, self.id, "/files/download", "GET", path, self._ensure_signing_key(), ttl_seconds
        )

    @intercept_errors(message_prefix="Failed to create upload URL: ")
    @with_instrumentation()
    def upload_url(self, path: str, ttl_seconds: int | None = None) -> str:
        """Creates a pre-signed URL for uploading a file to the Sandbox.

        Send a POST request with the file as multipart/form-data. The URL works with any
        HTTP client without auth headers. The signing key is cached locally for up to
        15 seconds; if the key was rotated from another client, URLs may be rejected
        until the cache refreshes.

        Args:
            path (str): Destination path for the uploaded file in the Sandbox.
            ttl_seconds (int | None): How long the URL stays valid, in seconds.
                Defaults to 3600. Zero or negative means the URL never expires.

        Returns:
            str: Pre-signed upload URL.

        Example:
            ```python
            url = sandbox.upload_url("/home/user/data.bin")
            ```
            ```bash
            curl -X POST -F "file=@local.bin" "$url"
            ```
        """
        return build_signed_file_url(
            self.toolbox_proxy_url, self.id, "/files/upload-v2", "POST", path, self._ensure_signing_key(), ttl_seconds
        )

    @intercept_errors(message_prefix="Failed to rotate signing key: ")
    @with_instrumentation()
    def rotate_signing_key(self) -> None:
        """Rotates the sandbox signing key, invalidating all previously signed URLs.

        Example:
            ```python
            sandbox.rotate_signing_key()
            # all URLs created before this call now return 401
            ```
        """
        self._signing_key = self._sandbox_api.rotate_signing_key(self.id)
        self._signing_key_fetched_at = time.monotonic()

    @intercept_errors(message_prefix="Failed to start sandbox: ")
    @with_timeout()
    @with_instrumentation()
    def start(self, timeout: float | None = 60):
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
        sandbox = self._sandbox_api.start_sandbox(self.id, _request_timeout=http_timeout(timeout))
        self.__process_sandbox_dto(sandbox)
        # This method already handles a timeout, so we don't need to pass one to internal methods
        self.wait_for_sandbox_start(timeout=0)

    @intercept_errors(message_prefix="Failed to recover sandbox: ")
    @with_timeout()
    def recover(self, timeout: float | None = 60):
        """Recovers the Sandbox from a recoverable error and waits for it to be ready.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If sandbox fails to recover or times out.

        Example:
            ```python
            sandbox = daytona.get("my-sandbox-id")
            sandbox.recover(timeout=40)  # Wait up to 40 seconds
            print("Sandbox recovered successfully")
            ```
        """
        sandbox = self._sandbox_api.recover_sandbox(self.id, _request_timeout=http_timeout(timeout))
        self.__process_sandbox_dto(sandbox)
        # This method already handles a timeout, so we don't need to pass one to internal methods
        self.wait_for_sandbox_start(timeout=0)

    @intercept_errors(message_prefix="Failed to stop sandbox: ")
    @with_timeout()
    @with_instrumentation()
    def stop(self, timeout: float | None = 60, force: bool = False):
        """Stops the Sandbox and waits for it to be fully stopped.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.
            force (bool): If True, uses SIGKILL instead of SIGTERM to stop the sandbox. Default is False.

        Raises:
            DaytonaError: If timeout is negative; If sandbox fails to stop or times out

        Example:
            ```python
            sandbox = daytona.get("my-sandbox-id")
            sandbox.stop()
            print("Sandbox stopped successfully")
            ```
        """
        _ = self._sandbox_api.stop_sandbox(
            self.id,
            force=force,
            _request_timeout=http_timeout(timeout),
        )
        self.__refresh_data_safe()
        # This method already handles a timeout, so we don't need to pass one to internal methods
        self.wait_for_sandbox_stop(timeout=0)

    @intercept_errors(message_prefix="Failed to remove sandbox: ")
    @with_timeout()
    @with_instrumentation()
    def delete(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument
        wait: bool = False,
    ) -> None:
        """Deletes the Sandbox.

        By default returns as soon as the deletion request is accepted (fire-and-forget).
        Pass ``wait=True`` to block until the Sandbox reaches the 'destroyed' state.

        Args:
            timeout (float | None): Timeout (in seconds) for the request and, when ``wait``
                is True, for reaching 'destroyed'. 0 means no timeout. Default is 60 seconds.
            wait (bool): If True, wait until the Sandbox is destroyed. Defaults to False.
        """
        sandbox = self._sandbox_api.delete_sandbox(self.id, _request_timeout=http_timeout(timeout))
        if sandbox:
            self.__process_sandbox_dto(sandbox)

        try:
            if wait and self.state != SandboxState.DESTROYED:
                self._wait_for_state(
                    [SandboxState.DESTROYED],
                    [SandboxState.ERROR, SandboxState.BUILD_FAILED],
                    safe_refresh=True,
                )
        finally:
            self._unsubscribe_from_events()

    @intercept_errors(message_prefix="Failure during waiting for sandbox to start: ")
    @with_timeout()
    @with_instrumentation()
    def wait_for_sandbox_start(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox to reach the 'started' state.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative; If Sandbox fails to start or times out;
        """
        if self.state == SandboxState.STARTED:
            return

        self._wait_for_state(
            [SandboxState.STARTED],
            [SandboxState.ERROR, SandboxState.BUILD_FAILED],
        )

    @intercept_errors(message_prefix="Failure during waiting for sandbox to stop: ")
    @with_timeout()
    @with_instrumentation()
    def wait_for_sandbox_stop(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox to reach the 'stopped' state.
        Treats destroyed as stopped to cover ephemeral sandboxes that are automatically deleted after stopping.

        Args:
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If Sandbox fails to stop or times out.
        """
        if self.state in [SandboxState.STOPPED, SandboxState.DESTROYED]:
            return

        self._wait_for_state(
            [SandboxState.STOPPED, SandboxState.DESTROYED],
            [SandboxState.ERROR, SandboxState.BUILD_FAILED],
            safe_refresh=True,
        )

    @intercept_errors(message_prefix="Failed to set auto-stop interval: ")
    @with_instrumentation()
    def set_autostop_interval(self, interval: int, request_timeout: float | None = None) -> None:
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

        _ = self._sandbox_api.set_autostop_interval(self.id, interval, _request_timeout=http_timeout(request_timeout))
        self.auto_stop_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-pause interval: ")
    @with_instrumentation()
    def set_auto_pause_interval(self, interval: int) -> None:
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
            sandbox.set_auto_pause_interval(60)
            # Or disable auto-pause
            sandbox.set_auto_pause_interval(0)
            ```
        """
        if interval < 0:
            raise DaytonaValidationError("Auto-pause interval must be a non-negative integer")

        _ = self._sandbox_api.set_auto_pause_interval(self.id, interval)
        self.auto_pause_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-archive interval: ")
    @with_instrumentation()
    def set_auto_archive_interval(self, interval: int, request_timeout: float | None = None) -> None:
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

        _ = self._sandbox_api.set_auto_archive_interval(
            self.id, interval, _request_timeout=http_timeout(request_timeout)
        )
        self.auto_archive_interval = interval

    @intercept_errors(message_prefix="Failed to set auto-delete interval: ")
    @with_instrumentation()
    def set_auto_delete_interval(self, interval: int, request_timeout: float | None = None) -> None:
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
        _ = self._sandbox_api.set_auto_delete_interval(
            self.id, interval, _request_timeout=http_timeout(request_timeout)
        )
        self.auto_delete_interval = interval

    @intercept_errors(message_prefix="Failed to update network settings: ")
    @with_instrumentation()
    def update_network_settings(
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
            sandbox.update_network_settings(network_block_all=True)
            sandbox.update_network_settings(network_block_all=False)
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
        updated = self._sandbox_api.update_network_settings(
            self.id, body, _request_timeout=http_timeout(request_timeout)
        )
        self.network_block_all = updated.network_block_all
        self.network_allow_list = updated.network_allow_list
        self.domain_allow_list = updated.domain_allow_list

    @intercept_errors(message_prefix="Failed to update secrets: ")
    @with_instrumentation()
    def update_secrets(self, secrets: dict[str, str]) -> None:
        """Updates the set of vault secrets mounted in the Sandbox, replacing the previously mounted set.

        Attached, detached and rotated secrets take effect for outbound requests within seconds.
        New environment variables only become visible to processes spawned after the update, and a
        Sandbox created without any secrets must be restarted for newly attached secrets to work.

        Args:
            secrets (dict[str, str]): Map of environment variable name to the name of an existing
                organization Secret. Pass an empty dict to detach all secrets.

        Example:
            ```python
            sandbox.update_secrets({"ANTHROPIC_API_KEY": "anthropic-prod"})
            sandbox.update_secrets({})  # detach all
            ```
        """
        body = UpdateSandboxSecrets(secrets=[{env_var: secret_name} for env_var, secret_name in secrets.items()])
        updated = self._sandbox_api.update_sandbox_secrets(self.id, body)
        self.__process_sandbox_dto(updated)

    @intercept_errors(message_prefix="Failed to update environment: ")
    @with_instrumentation()
    def update_env(self, env: dict[str, str], *, unset: list[str] | None = None) -> None:
        """Updates the Sandbox daemon's process environment.

        Newly spawned processes, sessions and PTYs inherit the change; already-running processes
        keep their environment.

        Args:
            env (dict[str, str]): Environment variables to set.
            unset (list[str] | None): Environment variable names to remove before `env` is applied.

        Example:
            ```python
            sandbox.update_env({"MY_VAR": "value"}, unset=["OLD_VAR"])
            ```
        """
        request = UpdateEnvRequest(set=env, unset=unset)
        _ = self._server_api.update_env(request=request)

    @intercept_errors(message_prefix="Failed to get preview link: ")
    @with_instrumentation()
    def get_preview_link(self, port: int, request_timeout: float | None = None) -> PortPreviewUrl:
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
        return self._sandbox_api.get_port_preview_url(self.id, port, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to create signed preview url: ")
    def create_signed_preview_url(
        self,
        port: int,
        expires_in_seconds: int | None = None,
        request_timeout: float | None = None,
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
        return self._sandbox_api.get_signed_port_preview_url(
            self.id,
            port,
            expires_in_seconds=expires_in_seconds,
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to expire signed preview url: ")
    def expire_signed_preview_url(self, port: int, token: str, request_timeout: float | None = None) -> None:
        """Expires a signed preview URL for the sandbox at the specified port.

        Args:
            port (int): The port to expire the signed preview url on.
            token (str): The token to expire the signed preview url on.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        self._sandbox_api.expire_signed_port_preview_url(
            self.id, port, token, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to archive sandbox: ")
    @with_instrumentation()
    def archive(self, request_timeout: float | None = None) -> None:
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
        _ = self._sandbox_api.archive_sandbox(self.id, _request_timeout=http_timeout(request_timeout))
        self.refresh_data(request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to resize sandbox: ")
    @with_timeout()
    @with_instrumentation()
    def resize(self, resources: Resources, timeout: float | None = 60) -> None:
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
            sandbox.resize(Resources(cpu=4, memory=8))

            sandbox.stop()
            sandbox.resize(Resources(cpu=2, memory=4, disk=30))
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
        sandbox = self._sandbox_api.resize_sandbox(self.id, resize_request, _request_timeout=timeout or None)
        self.__process_sandbox_dto(sandbox)
        self.wait_for_resize_complete(timeout=0)

    @intercept_errors(message_prefix="Failure during waiting for resize to complete: ")
    @with_timeout()
    @with_instrumentation()
    def wait_for_resize_complete(
        self,
        timeout: float | None = 60,  # pylint: disable=unused-argument # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Waits for the Sandbox resize operation to complete.

        Args:
            timeout (Optional[float]): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If timeout is negative. If resize operation times out.
        """
        if self.state != SandboxState.RESIZING:
            return

        error_states = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
        exclude = {SandboxState.RESIZING} | set(error_states)
        target_states = [s for s in SandboxState if s not in exclude]

        self._wait_for_state(target_states, error_states)

    @intercept_errors(message_prefix="Failed to create SSH access: ")
    @with_instrumentation()
    def create_ssh_access(
        self,
        expires_in_minutes: int | None = None,
        request_timeout: float | None = None,
    ) -> SshAccessDto:
        """Creates an SSH access token for the sandbox.

        Args:
            expires_in_minutes (int | None): The number of minutes the SSH access token will be valid for.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        return self._sandbox_api.create_ssh_access(
            self.id,
            expires_in_minutes=expires_in_minutes,
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to revoke SSH access: ")
    @with_instrumentation()
    def revoke_ssh_access(self, token: str, request_timeout: float | None = None) -> None:
        """Revokes an SSH access token for the sandbox.

        Args:
            token (str): The token to revoke.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        _ = self._sandbox_api.revoke_ssh_access(self.id, token, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to validate SSH access: ")
    @with_instrumentation()
    def validate_ssh_access(self, token: str, request_timeout: float | None = None) -> SshAccessValidationDto:
        """Validates an SSH access token for the sandbox.

        Args:
            token (str): The token to validate.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        return self._sandbox_api.validate_ssh_access(token, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to refresh sandbox activity: ")
    def refresh_activity(self, request_timeout: float | None = None) -> None:
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
            sandbox.refresh_activity()
            ```
        """
        self._sandbox_api.update_last_activity(self.id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to fork sandbox: ")
    @with_timeout()
    @with_instrumentation()
    def _experimental_fork(self, name: str | None = None, timeout: float | None = 60) -> "Sandbox":
        """Forks the Sandbox, creating a new Sandbox with an identical filesystem.

        The forked Sandbox is a copy-on-write clone of the original. It starts
        with the same disk contents but operates independently from that point on.

        Args:
            name (str | None): Optional name for the forked Sandbox. If not provided, a unique name will be generated.
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Returns:
            Sandbox: The forked Sandbox.

        Raises:
            DaytonaError: If the fork operation fails or times out.

        Example:
            ```python
            sandbox = daytona.get("my-sandbox")
            forked = sandbox._experimental_fork(name="my-fork")
            print(f"Forked sandbox: {forked.id}")
            ```
        """
        sandbox_dto = self._sandbox_api.fork_sandbox(
            self.id, ForkSandbox(name=name), _request_timeout=http_timeout(timeout)
        )

        language = sandbox_dto.labels.get(CODE_TOOLBOX_LANGUAGE_LABEL) or ""

        forked = Sandbox(
            sandbox_dto,
            self._toolbox_api._api_client,
            self._sandbox_api,
            language,
            subscription_manager=self._subscription_manager,
            http_client=self._http_client,
            analytics_api_url_provider=self._analytics_api_url_provider,
        )
        forked.wait_for_sandbox_start(timeout=0)
        return forked

    @intercept_errors(message_prefix="Failed to create snapshot: ")
    @with_timeout()
    @with_instrumentation()
    def _experimental_create_snapshot(self, name: str, timeout: float | None = 60) -> None:
        """Creates a snapshot from the current state of the Sandbox.

        This captures the Sandbox's filesystem into a reusable snapshot that can be
        used to create new Sandboxes. The Sandbox will temporarily enter a
        'snapshotting' state and return to its previous state when complete.

        Args:
            name (str): Name for the new snapshot.
            timeout (float | None): Maximum time to wait in seconds. 0 means no timeout. Default is 60 seconds.

        Raises:
            DaytonaError: If the snapshot operation fails or times out.

        Example:
            ```python
            sandbox = daytona.get("my-sandbox")
            sandbox._experimental_create_snapshot("my-snapshot")
            print("Snapshot created successfully")
            ```
        """
        response = self._sandbox_api.create_sandbox_snapshot(
            self.id,
            CreateSandboxSnapshot(name=name),
            _request_timeout=http_timeout(timeout),
        )
        self.__process_sandbox_dto(response)

        error_states = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
        exclude = {SandboxState.SNAPSHOTTING} | set(error_states)
        target_states = [s for s in SandboxState if s not in exclude]

        self._wait_for_state(target_states, error_states)

    @intercept_errors(message_prefix="Failed to pause sandbox")
    @with_timeout()
    @with_instrumentation()
    def pause(self, timeout: float = 60) -> None:
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

        _ = self._sandbox_api.pause_sandbox(self.id, _request_timeout=timeout if timeout > 0 else None)
        self.refresh_data()
        error_states = [SandboxState.ERROR, SandboxState.BUILD_FAILED]
        exclude = {SandboxState.PAUSING} | set(error_states)
        target_states = [s for s in SandboxState if s not in exclude]
        self._wait_for_state(target_states, error_states)

    def _ensure_subscribed(self) -> None:
        with self._state_waiters_lock:
            if self._sub_id is not None:
                if self._subscription_manager.refresh(self._sub_id):
                    return
                self._sub_id = None

            subscription_id = self._subscription_manager.subscribe(
                self.id,
                self._handle_event,
                events=["sandbox.state.updated", "sandbox.created"],
            )
            self._sub_id = subscription_id or None

    def _handle_event(self, event_name: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        raw: object = data.get("sandbox", data)  # pyright: ignore[reportUnknownVariableType]

        if event_name == "sandbox.created":
            sandbox_dto = SandboxDto.from_dict(raw)  # pyright: ignore[reportArgumentType]
            if sandbox_dto is not None:
                self.__process_sandbox_dto(sandbox_dto)
        else:
            new_state = (  # pyright: ignore[reportUnknownVariableType]
                raw.get("state") if isinstance(raw, dict) else None
            ) or data.get("newState")
            if new_state is not None:
                try:
                    self._apply_state(SandboxState(new_state))
                except ValueError:
                    pass

    def _unsubscribe_from_events(self) -> None:
        with self._state_waiters_lock:
            if self._sub_id is not None:
                self._subscription_manager.unsubscribe(self._sub_id)
                self._sub_id = None

    def _apply_state(self, new_state: SandboxState | None) -> None:
        if new_state == self.state:
            return

        self.state: SandboxState | None = new_state

        with self._state_waiters_lock:
            for waiter in list(self._state_waiters):
                waiter(new_state)

    def _wait_for_state(
        self,
        target_states: list[SandboxState],
        error_states: list[SandboxState],
        safe_refresh: bool = False,
    ) -> None:
        """Wait for sandbox to reach a target state via WebSocket events with periodic polling safety net.

        Args:
            target_states: States that indicate success.
            error_states: States that indicate failure.
            safe_refresh: If True, use safe refresh that treats 404 as destroyed (for delete operations).
        """
        self._ensure_subscribed()

        # Fast-path only on cached *target* states (parity with main's pre-check).
        # Cached error states are deliberately NOT evaluated here — main always
        # refreshed before failing, so a stale ERROR must survive one refresh.
        if self.state in target_states:
            return

        subscribed = self._sub_id is not None
        poll_interval = 1.0 if subscribed else 0.1
        poll_start = time.monotonic()
        state_resolved = threading.Event()
        resolve_lock = threading.Lock()
        result_state: SandboxState | None = None

        def _waiter(state: SandboxState | None) -> None:
            nonlocal result_state
            if state is None or (state not in target_states and state not in error_states):
                return

            with resolve_lock:
                if state_resolved.is_set():
                    return

                result_state = state
                state_resolved.set()

        with self._state_waiters_lock:
            self._state_waiters.append(_waiter)
        try:
            # First poll runs immediately (main always refreshed before state evaluation).
            if safe_refresh:
                self.__refresh_data_safe()
            else:
                self.refresh_data()

            _waiter(self.state)

            while not state_resolved.is_set():
                is_set = state_resolved.wait(timeout=poll_interval)

                if is_set:
                    break

                if safe_refresh:
                    self.__refresh_data_safe()
                else:
                    self.refresh_data()

                if subscribed or time.monotonic() - poll_start > 5.0:
                    poll_interval = min(poll_interval * 1.1, 1.0)

            if result_state in error_states:
                raise DaytonaError(
                    f"Sandbox {self.id} entered error state: {result_state}, error reason: {self.error_reason}"
                )
        except Exception as exc:
            # Parity with main: complete one final refresh-then-evaluate before
            # propagating, so a clamped/short timeout still observes the latest state.
            if not state_resolved.is_set():
                try:
                    if safe_refresh:
                        self.__refresh_data_safe()
                    else:
                        self.refresh_data()
                except Exception:
                    pass  # fall through to re-raise below
                if state_resolved.is_set():
                    if result_state in error_states:
                        message = (
                            f"Sandbox {self.id} entered error state: {result_state}, error reason: {self.error_reason}"
                        )
                        raise DaytonaError(message) from exc
                    return  # Target state reached — suppress the timeout
            raise
        finally:
            with self._state_waiters_lock:
                if _waiter in self._state_waiters:
                    self._state_waiters.remove(_waiter)

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
        new_proxy_url = sandbox_dto.toolbox_proxy_url
        if new_proxy_url:
            if new_proxy_url != self.toolbox_proxy_url and hasattr(self, "_toolbox_api"):
                self._toolbox_api._toolbox_base_url = new_proxy_url
            self.toolbox_proxy_url: str = new_proxy_url

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

        self._apply_state(sandbox_dto.state)

    def __refresh_data_safe(self) -> None:
        """Refreshes sandbox data, treating 404 as DESTROYED and tolerating pydantic validation errors."""
        try:
            self.refresh_data()
        except DaytonaNotFoundError:
            self._apply_state(SandboxState.DESTROYED)
        except Exception as e:
            if not is_validation_error(e):
                raise
