# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import math
import time
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from datetime import datetime, timezone
from typing import Callable, Dict, Final, Mapping, Optional, Sequence
from urllib.parse import quote, urlsplit

from typing_extensions import Self

from . import _clock
from ._api_core import (
    expect_create_image_resp,
    expect_create_snapshot_resp,
    expect_fork_resp,
    expect_sandbox_detail,
)
from ._auth import ProjectTokenAuth
from ._body_limit import DEFAULT_MAX_RESPONSE_BYTES
from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    TransportPayload,
    artifact_ref_from_wire,
    assert_lifecycle_class_support,
    enum_str,
    expect_object,
    expect_system_metrics,
    lifecycle_body,
    mib_to_gb,
    parse_image,
    parse_lifecycle,
    parse_snapshot,
    sandbox_state,
)
from ._events import WaitHub, WaitOutcome, _TargetPredicate, endpoint_from_headers, region_of, wait_query_key
from ._gen.api import (
    LifecyclePolicy,
    SandboxListItem,
    SignSandboxFileUrlRequest,
)
from ._gen.api import (
    Sandbox as SandboxDto,
)
from ._transport import AsyncTransport
from ._wait import WAIT_FOR_STATE_MIN_INTERVAL_S
from .build_logs import (
    BuildLogFollowResult,
    _EventCallback,
    _LogsCallback,
    follow_build_logs,
    read_build_logs,
)
from .computer_use import AsyncComputerUseService
from .docker import AsyncDockerService
from .errors import DaytonaError, SandboxTerminalStateError, create_error
from .filesystem import AsyncFileSystemService
from .git import AsyncGitService
from .interpreter import AsyncInterpreterService
from .lsp import AsyncLspService
from .network_policy import parse_network_policy_response, serialize_network_policy_input
from .ports import AsyncPortsService
from .process import AsyncProcessService, AsyncPtyService
from .sandbox_secrets import (
    parse_sandbox_secrets_response,
    serialize_sandbox_secrets_input,
)
from .sandbox_volumes import AsyncSandboxVolumesService
from .types import (
    DEFAULT_SANDBOX_TERMINAL_STATES,
    AsyncInterpreterContext,
    Image,
    ImageRef,
    InterpreterContextJSON,
    InterpreterResult,
    JsonObject,
    JsonValue,
    LifecycleAction,
    LifecyclePolicyConfig,
    MacBootMode,
    NetworkPolicyInfo,
    NetworkPolicyInput,
    ProcessResult,
    RunKeepLogs,
    SandboxAccessToken,
    SandboxClass,
    SandboxInfo,
    SandboxLifecycle,
    SandboxSecretsInfo,
    SandboxState,
    SandboxWaitState,
    Snapshot,
    SnapshotRef,
    StdinMode,
    SystemMetrics,
    _artifact_ref_to_json,
)

CODE_TOOLBOX_LANGUAGE_LABEL = "code-toolbox-language"
_SANDBOX_CLASSES: tuple[SandboxClass, ...] = ("linux", "macos")
_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE = (
    "pre-signed URLs require the API-provided toolbox URL or a sandbox-scoped URL template"
)
WAIT_FOR_STATE_SLICE_MS = 30_000
# macOS: a failed pause returns to ``started`` and an unfinished resume ends ``stopped``;
# after those requests each state means failure, not something to keep waiting past.
_MACOS_PAUSE_TERMINAL_STATES: tuple[SandboxState, ...] = (*DEFAULT_SANDBOX_TERMINAL_STATES, "started")
_MACOS_RESUME_TERMINAL_STATES: tuple[SandboxState, ...] = (*DEFAULT_SANDBOX_TERMINAL_STATES, "stopped")
SANDBOX_TOKEN_REFRESH_LEAD_SECONDS = 60.0


class _SandboxTokenAuth:
    """The transport-facing half of the sandbox token refresh loop.

    Bound to the sandbox handle's scoped toolbox transport when the handle can
    mint (an API transport with a project key, and no caller-supplied
    ``access_token``). ``bearer()`` runs before every sandbox service request;
    ``on_unauthorized()`` runs at most once per request after a 401.
    """

    def __init__(self, sandbox: AsyncSandbox) -> None:
        self._sandbox = sandbox

    async def bearer(self) -> Optional[str]:
        return await self._sandbox._sandbox_token_bearer()

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
        return await self._sandbox._sandbox_token_remint(failed_token)


_INVALID_ENV_KEY_REASON: Final = "env key is not a valid variable name"


class AsyncSandbox:
    """Serializable sandbox handle with process/fs/git/interpreter/pty namespaces."""

    def __init__(
        self,
        info: SandboxInfo,
        api: Optional[AsyncTransport],
        toolbox: Optional[AsyncTransport],
        *,
        toolbox_owner: Optional[AsyncTransport] = None,
        toolbox_url: Optional[str] = None,
        language: Optional[str] = None,
        access_token: Optional[str] = None,
        toolbox_base_url: Optional[str] = None,
        toolbox_token: Optional[str] = None,
        direct_toolbox_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
        access_token_expires_at: Optional[str] = None,
        access_token_caller_supplied: Optional[bool] = None,
        hub: Optional[WaitHub] = None,
        hub_provider: Optional[Callable[[], Optional[WaitHub]]] = None,
    ) -> None:
        self.id = info.id
        self.name = info.name
        self.state = info.state
        self.image_build_id = info.image_build_id
        self.image: ImageRef | None = info.image
        self.snapshot: SnapshotRef | None = info.snapshot
        self.target = info.target
        region = region_of(info.id)
        self.region: Optional[str] = info.region or (region if region != "_" else None)
        if self.region is None and info.target and info.target != "local":
            self.region = info.target
        self.labels: Dict[str, str] = dict(info.labels)
        self.cpu: Optional[float] = info.cpu
        self.cpu_max: Optional[float] = info.cpu_max
        self.memory: Optional[float] = info.memory
        self.memory_max: Optional[float] = info.memory_max
        self.disk: Optional[float] = info.disk
        self.disk_max: Optional[float] = info.disk_max
        self._mac_boot_mode: Optional[MacBootMode] = None
        # None until fetched, and for a class this SDK does not know (class-gated
        # calls then raise); ``_wire_class`` keeps the raw wire value.
        self.sandbox_class: Optional[SandboxClass] = info.sandbox_class
        self._wire_class: Optional[str] = info.sandbox_class
        # None on a metadata-less handle that has never been refreshed.
        self.persistent: Optional[bool] = info.persistent
        self.spot: Optional[bool] = info.spot
        self.spot_evicted_at: Optional[str] = info.spot_evicted_at
        self.lifecycle_policy: Optional[SandboxLifecycle] = info.lifecycle_policy
        self.forked_from: Optional[str] = info.forked_from
        # macOS only: the external ID of the reservation selected by the current or
        # latest successful claim. Unset before first claim, and on search
        # reads (search(), a search-routed list()), which omit it: refresh_data()
        # reads it.
        self.reservation_id: Optional[str] = info.reservation_id
        self.user: Optional[str] = info.user
        self.error: Optional[str] = info.error
        self.error_reason: Optional[str] = info.error
        self.access_token = access_token
        #: Expiry of ``access_token`` (RFC 3339), or ``None`` when not reported.
        self.access_token_expires_at = access_token_expires_at
        # Unstated (None) defaults to "an explicitly passed token is the
        # caller's": only the SDK's own construction paths, whose token came
        # from the create response, pass False — the TS twin's
        # accessTokenFromCreate shape. A wrong default here silently replaces
        # a caller's scoped credential with a freshly minted full-grant one.
        if access_token_caller_supplied is None:
            access_token_caller_supplied = access_token is not None
        self._access_token_caller_supplied = access_token_caller_supplied
        self._sandbox_token_key: Optional[str] = getattr(api, "token", None) if api is not None else None
        # Use project authentication when token minting is unavailable.
        # JWT-only clients resolve their project token instead of holding a static key.
        _auth_obj = getattr(api, "_auth", None) if api is not None else None
        self._project_auth: Optional[ProjectTokenAuth] = (
            _auth_obj if isinstance(_auth_obj, ProjectTokenAuth) else None
        )
        api_transport_has_credential = api is not None and (
            bool(self._sandbox_token_key)
            or getattr(api, "auth_provider", None) is not None
            or getattr(api, "_auth", None) is not None
        )
        # A caller-supplied token is NEVER silently replaced; everything else
        # with a project key mints and refreshes sandbox access tokens
        # transparently.
        self._sandbox_token_mintable = not access_token_caller_supplied and api_transport_has_credential
        self._sandbox_token_lock = asyncio.Lock()
        self._sandbox_token_mint: Optional[asyncio.Task[str]] = None
        self._sandbox_token_mint_unsupported = False
        self.toolbox_proxy_url = toolbox_url
        self._raw_state: str = ""  # wire status string, set by from_api
        self._direct_toolbox_url = direct_toolbox_url
        self._toolbox_base_url = toolbox_base_url
        self._toolbox_token = toolbox_token or access_token
        self._request_timeout = request_timeout
        self._api = api
        self._toolbox = toolbox
        self._toolbox_owner = toolbox_owner
        self._language = language
        self._hub = hub
        self._hub_provider = hub_provider
        self._metadata_loaded = _sandbox_info_has_metadata(info)
        self._process: Optional[AsyncProcessService] = None
        self._fs: Optional[AsyncFileSystemService] = None
        self._git: Optional[AsyncGitService] = None
        self._interpreter: Optional[AsyncInterpreterService] = None
        self._pty: Optional[AsyncPtyService] = None
        self._computer_use: Optional[AsyncComputerUseService] = None
        self._docker: Optional[AsyncDockerService] = None
        self._ports: Optional[AsyncPortsService] = None
        self._volumes: Optional[AsyncSandboxVolumesService] = None
        self._lsp: Optional[AsyncLspService] = None
        self._bind_sandbox_token_auth()

    def _bind_sandbox_token_auth(self) -> None:
        if self._toolbox is None:
            return
        # A transport adopted from another handle (refresh_data) may carry that
        # handle's minting provider; a non-mintable handle clears it so a
        # caller-supplied scoped token is never silently replaced.
        if self._sandbox_token_mintable:
            self._toolbox.auth_provider = _SandboxTokenAuth(self)
        else:
            self._toolbox.auth_provider = None

    @property
    def raw_state(self) -> str:
        """Wire status string exactly as the server sent it.

        Informational only — the SDK never branches on this value.  Use
        :attr:`state` for all flow-control logic; ``raw_state`` is useful for
        logging when ``state == "unknown"`` (the SDK does not recognize the value).
        """
        return self._raw_state

    @classmethod
    def from_api(
        cls,
        sandbox_data: SandboxDto | SandboxListItem | Mapping[str, JsonValue],
        api: Optional[AsyncTransport],
        toolbox: Optional[AsyncTransport],
        *,
        toolbox_owner: Optional[AsyncTransport] = None,
        toolbox_url: Optional[str] = None,
        language: Optional[str] = None,
        access_token: Optional[str] = None,
        toolbox_base_url: Optional[str] = None,
        toolbox_token: Optional[str] = None,
        direct_toolbox_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
        access_token_expires_at: Optional[str] = None,
        access_token_caller_supplied: Optional[bool] = None,
        hub: Optional[WaitHub] = None,
        hub_provider: Optional[Callable[[], Optional[WaitHub]]] = None,
        _toolbox_url_from_api: bool = False,
    ) -> AsyncSandbox:
        """Create a sandbox handle from an API response.

        An explicitly passed ``access_token`` is treated as caller-supplied
        (never silently replaced by a minted one) unless
        ``access_token_caller_supplied=False`` marks it as the SDK-owned
        create-response credential.

        A ``toolbox_proxy_url`` inside ``sandbox_data`` is ignored unless the SDK's own
        refresh/fork paths mark it as a fresh API response, so
        stored data never chooses where a credential goes. Pass
        ``toolbox`` or ``toolbox_url`` to set the toolbox explicitly — the
        toolbox credential goes there, so pass only a URL you trust.
        """
        if isinstance(sandbox_data, (SandboxDto, SandboxListItem)):
            sandbox_id = sandbox_data.id
            sandbox_name = sandbox_data.name
            sandbox_status = sandbox_data.status
            sandbox_image = artifact_ref_from_wire(sandbox_data.image)
            sandbox_snapshot = artifact_ref_from_wire(sandbox_data.snapshot)
            sandbox_target = sandbox_data.region_id
            toolbox_base = _sandbox_toolbox_base(sandbox_data) if _toolbox_url_from_api else None
            cpu = sandbox_data.cpu
            cores_max = getattr(sandbox_data, "cores_max", None)
            memory_mib = sandbox_data.memory_mib
            memory_max_mib = sandbox_data.memory_max_mib
            disk_mib = sandbox_data.disk_mib
            disk_max_mib = sandbox_data.disk_max_mib
            wire_class: Optional[str] = enum_str(sandbox_data.var_class)
            mac_boot_mode = getattr(sandbox_data, "mac_boot_mode", None)
            persistent = sandbox_data.persistent
            labels = _string_map_or_empty(getattr(sandbox_data, "labels", None))
            region = sandbox_data.region_id
            spot = getattr(sandbox_data, "spot", None)
            spot_evicted_at = _isoformat_or_none(getattr(sandbox_data, "spot_evicted_at", None))
            lifecycle_policy = getattr(sandbox_data, "lifecycle_policy", None)
            forked_from = getattr(sandbox_data, "forked_from", None)
            reservation_id = getattr(sandbox_data, "reservation_id", None)
            user = getattr(sandbox_data, "user", None)
            error = getattr(sandbox_data, "error", None)
        else:
            sandbox_id = str(sandbox_data.get("id"))
            sandbox_name = str(sandbox_data["name"]) if isinstance(sandbox_data.get("name"), str) else None
            sandbox_status = str(sandbox_data.get("status") or sandbox_data.get("state") or "")
            sandbox_image = artifact_ref_from_wire(sandbox_data.get("image"))
            sandbox_snapshot = artifact_ref_from_wire(sandbox_data.get("snapshot"))
            sandbox_target = str(sandbox_data.get("region_id") or sandbox_data.get("target") or "") or None
            toolbox_base = _sandbox_toolbox_base(sandbox_data) if _toolbox_url_from_api else None
            cpu = sandbox_data.get("cpu")
            cores_max = sandbox_data.get("cores_max")
            memory_mib = sandbox_data.get("memory_mib")
            memory_max_mib = sandbox_data.get("memory_max_mib")
            disk_mib = sandbox_data.get("disk_mib")
            disk_max_mib = sandbox_data.get("disk_max_mib")
            wire_class = (
                str(sandbox_data.get("class")) if isinstance(sandbox_data.get("class"), str) else None
            )
            mac_boot_mode = (
                str(sandbox_data.get("mac_boot_mode"))
                if isinstance(sandbox_data.get("mac_boot_mode"), str)
                else None
            )
            persistent = sandbox_data["persistent"] is True if "persistent" in sandbox_data else None
            labels = _string_map_or_empty(sandbox_data.get("labels"))
            region = (
                str(sandbox_data.get("region_id")) if isinstance(sandbox_data.get("region_id"), str) else None
            )
            spot = sandbox_data["spot"] is True if "spot" in sandbox_data else None
            spot_evicted_at = (
                str(sandbox_data.get("spot_evicted_at"))
                if isinstance(sandbox_data.get("spot_evicted_at"), str)
                else None
            )
            raw_lifecycle_policy = sandbox_data.get("lifecycle_policy")
            lifecycle_policy = raw_lifecycle_policy if isinstance(raw_lifecycle_policy, dict) else None
            forked_from = (
                str(sandbox_data.get("forked_from"))
                if isinstance(sandbox_data.get("forked_from"), str)
                else None
            )
            reservation_id = (
                str(sandbox_data.get("reservation_id"))
                if isinstance(sandbox_data.get("reservation_id"), str)
                else None
            )
            user = str(sandbox_data.get("user")) if isinstance(sandbox_data.get("user"), str) else None
            error = str(sandbox_data.get("error")) if isinstance(sandbox_data.get("error"), str) else None
        # Every in-repo caller passes a typed DTO (Sandbox / SandboxListItem), and
        # only Sandbox carries this field; the mapping arm is for a caller
        # that hands in a decoded body directly.
        raw_build = (
            sandbox_data.get("image_build_id")
            if isinstance(sandbox_data, dict)
            else getattr(sandbox_data, "image_build_id", None)
        )
        image_build_id = str(raw_build) if isinstance(raw_build, str) else None
        wire_class = wire_class or "linux"
        info = SandboxInfo(
            id=sandbox_id,
            name=sandbox_name,
            state=sandbox_state(sandbox_status),
            image=sandbox_image,
            snapshot=sandbox_snapshot,
            target=sandbox_target,
            region=region if region is not None else sandbox_target,
            labels=labels,
            image_build_id=image_build_id,
            cpu=float(cpu) if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) else None,
            cpu_max=_optional_number(cores_max),
            memory=_gib(memory_mib, exact=wire_class == "macos"),
            memory_max=_gib(memory_max_mib, exact=wire_class == "macos"),
            disk=_gib(disk_mib, exact=wire_class == "macos"),
            disk_max=_gib(disk_max_mib, exact=wire_class == "macos"),
            sandbox_class=_known_sandbox_class(wire_class),
            persistent=persistent,
            spot=spot,
            spot_evicted_at=spot_evicted_at,
            lifecycle_policy=parse_lifecycle(lifecycle_policy)
            if isinstance(lifecycle_policy, (LifecyclePolicy, dict))
            else None,
            forked_from=forked_from,
            reservation_id=reservation_id,
            user=user,
            error=error,
        )
        if toolbox is None:
            base = toolbox_base_url or toolbox_base or toolbox_url
            resolved = _scoped_toolbox_url(info.id, direct=toolbox_url, base=base)
            if resolved:
                if toolbox_owner is not None:
                    toolbox = toolbox_owner.scoped(resolved, toolbox_token or access_token)
                else:
                    # Inherit the api transport's X-Daytona-Source label so an
                    # externally constructed handle is attributed like its root.
                    toolbox = AsyncTransport(
                        resolved,
                        toolbox_token or access_token,
                        source=api._source if api is not None else "sdk-python-async",
                        request_timeout=request_timeout,
                        max_response_bytes=api.max_response_bytes
                        if api is not None
                        else DEFAULT_MAX_RESPONSE_BYTES,
                    )
            toolbox_url = resolved
            toolbox_base_url = base
        sandbox = cls(
            info,
            api,
            toolbox,
            toolbox_owner=toolbox_owner,
            toolbox_url=toolbox_url,
            language=language,
            access_token=access_token,
            toolbox_base_url=toolbox_base_url,
            toolbox_token=toolbox_token,
            direct_toolbox_url=direct_toolbox_url,
            request_timeout=request_timeout,
            access_token_expires_at=access_token_expires_at,
            access_token_caller_supplied=access_token_caller_supplied,
            hub=hub,
            hub_provider=hub_provider,
        )
        sandbox._wire_class = wire_class
        sandbox._mac_boot_mode = mac_boot_mode
        sandbox._raw_state = sandbox_status
        return sandbox

    @property
    def mac_boot_mode(self) -> Optional[MacBootMode]:
        """macOS only: how the sandbox resumed, as reported by the API, or None."""
        return self._mac_boot_mode

    @property
    def process(self) -> AsyncProcessService:
        if self._process is None:
            self._process = AsyncProcessService(self.id, self._require_toolbox(), self._language)
        return self._process

    @property
    def fs(self) -> AsyncFileSystemService:
        if self._fs is None:
            self._fs = AsyncFileSystemService(self._require_toolbox(), _file_url_signer=self._sign_file_url)
        return self._fs

    @property
    def git(self) -> AsyncGitService:
        if self._git is None:
            self._git = AsyncGitService(self._require_toolbox())
        return self._git

    @property
    def interpreter(self) -> AsyncInterpreterService:
        if self._interpreter is None:
            self._interpreter = AsyncInterpreterService(self.id, self._require_toolbox(), self.process)
        return self._interpreter

    @property
    def pty(self) -> AsyncPtyService:
        if self._pty is None:
            self._pty = AsyncPtyService(self.process)
        return self._pty

    @property
    def computer_use(self) -> AsyncComputerUseService:
        if self._computer_use is None:
            self._computer_use = AsyncComputerUseService(self._require_toolbox())
        return self._computer_use

    @property
    def docker(self) -> AsyncDockerService:
        """Docker Engine status inside the sandbox; see :class:`AsyncDockerService`."""
        if self._docker is None:
            self._docker = AsyncDockerService(self._require_toolbox())
        return self._docker

    @property
    def ports(self) -> AsyncPortsService:
        if self._ports is None:
            self._ports = AsyncPortsService(
                self._toolbox,
                self._api,
                sandbox_id=self.id,
                mint_preview_token=self._mint_preview_token,
            )
        return self._ports

    @property
    def lsp(self) -> AsyncLspService:
        if self._lsp is None:
            self._lsp = AsyncLspService(self._require_toolbox())
        return self._lsp

    @property
    def volumes(self) -> AsyncSandboxVolumesService:
        if self._volumes is None:
            self._volumes = AsyncSandboxVolumesService(self._require_toolbox())
        return self._volumes

    async def metrics(self) -> SystemMetrics:
        """A snapshot of the sandbox's CPU, memory and disk usage."""
        return expect_system_metrics(
            await self._require_toolbox().send(RequestSpec("GET", "/system/metrics"))
        )

    async def exec(
        self,
        command: Optional[str] = None,
        *,
        argv: Optional[Sequence[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        user: Optional[str] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        stdin: StdinMode = "closed",
        timeout: Optional[float] = None,
        execution_timeout: Optional[float] = None,
        name: Optional[str] = None,
        keep_logs: Optional[RunKeepLogs] = None,
        tag: Optional[str] = None,
    ) -> ProcessResult:
        """Run a command, wait for its shell to exit, and collect its output.

        A convenience alias for :meth:`process.run` — identical behaviour, same
        parameters, same return value.

        **Shell selection.** Passing a string ``command`` runs it through the
        sandbox shell: ``bash`` when the image has it, else ``sh``. Pass
        ``shell=`` to pin another interpreter, and quote globs and
        brackets (``pip install -e '.[test]'``) so the command means the same
        under any shell. Use ``argv=`` to bypass the shell entirely, which is
        the safe form for untrusted arguments. For persistent shell state, use
        :meth:`process.create_session`.

        **Background processes.** The call returns when the shell exits. If the
        command launches a background child that holds output open (e.g.
        ``python3 -m http.server 8000 &``), the call waits at most ~2 seconds
        for output to drain and then returns; output the child writes later is not
        part of the result. For a long-running server use
        :meth:`process.start`, which returns a :class:`ProcessHandle` immediately
        (logs, wait, kill). Alternatively redirect: ``nohup cmd > server.log 2>&1 &``.

        :param command: Shell command string; mutually exclusive with ``argv``.
        :param argv: Argument vector for shell-free execution.
        :param cwd: Working directory inside the sandbox.
        :param env: Extra environment variables merged into the process environment.
        :param shell: Shell binary to use (e.g. ``"bash"``); overrides auto-detection.
        :param timeout: Caller wait budget in seconds. Omit to wait until the process
            ends. Does not kill the process; use ``execution_timeout`` for that.
        :param execution_timeout: Server-side deadline in seconds. The sandbox kills
            the process tree when this expires.
        :param keep_logs: How long the sandbox keeps the process log: ``"on_exit_ttl"``
            (default) or ``"until_cleanup"``.
        :returns: :class:`~daytona.v2.types.ProcessResult` with ``stdout``, ``stderr``,
            ``output``, ``logs``, ``exit_code``, and ``reason``.
        """
        return await self.process.run(
            command,
            argv=argv,
            cwd=cwd,
            env=env,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            timeout=timeout,
            execution_timeout=execution_timeout,
            name=name,
            keep_logs=keep_logs,
            tag=tag,
        )

    async def run_code(
        self,
        code: str,
        *,
        language: Optional[str] = None,
        context: str | AsyncInterpreterContext | InterpreterContextJSON | None = None,
        isolated: bool = False,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> InterpreterResult:
        """Execute code and return its result: an alias for ``interpreter.run()``, same parameters."""
        return await self.interpreter.run(
            code,
            language=language,
            context=context,
            isolated=isolated,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )

    def to_json(self) -> Dict[str, JsonValue]:
        """The sandbox's data as JSON, without any toolbox URL; resume a stored sandbox with ``get(info["sandboxId"])``."""
        payload: Dict[str, JsonValue] = {"sandboxId": self.id}
        _put_handle_json_value(payload, "name", self.name)
        _put_handle_json_value(payload, "image", _artifact_ref_to_json(self.image))
        _put_handle_json_value(payload, "snapshot", _artifact_ref_to_json(self.snapshot))
        if self._metadata_loaded:
            _put_handle_json_value(payload, "target", self.target)
            _put_handle_json_value(payload, "cpu", self.cpu)
            _put_handle_json_value(payload, "cpuMax", self.cpu_max)
            _put_handle_json_value(payload, "memory", self.memory)
            _put_handle_json_value(payload, "memoryMax", self.memory_max)
            _put_handle_json_value(payload, "disk", self.disk)
            _put_handle_json_value(payload, "diskMax", self.disk_max)
            if self.labels:
                payload["labels"] = dict(self.labels)
        if self._metadata_loaded or self.state != "unknown":
            payload["state"] = self.state
        _put_handle_json_value(payload, "error", self.error)
        _put_handle_json_value(payload, "imageBuildId", self.image_build_id)
        _put_handle_json_value(payload, "sandboxClass", self.sandbox_class)
        _put_handle_json_value(payload, "region", self.region)
        _put_handle_json_value(payload, "persistent", self.persistent)
        _put_handle_json_value(payload, "spot", self.spot)
        _put_handle_json_value(payload, "spotEvictedAt", self.spot_evicted_at)
        if self.lifecycle_policy is not None:
            payload["lifecyclePolicy"] = _sandbox_lifecycle_handle_json(self.lifecycle_policy)
        _put_handle_json_value(payload, "forkedFrom", self.forked_from)
        _put_handle_json_value(payload, "reservationId", self.reservation_id)
        _put_handle_json_value(payload, "user", self.user)
        return payload

    async def set_labels(self, *, labels: Mapping[str, str]) -> Dict[str, str]:
        """Replace the sandbox label set; pass ``{}`` to clear all labels."""
        response = expect_object(
            await self._require_api().send(
                RequestSpec(
                    "PUT",
                    f"/sandboxes/{quote(self.id, safe='')}/labels",
                    json_body={"labels": _validated_labels(labels)},
                )
            )
        )
        # The labels route answers `SandboxLabels` ({labels, id}), never a Sandbox.
        self.labels = _string_map_or_empty(response.get("labels"))
        return dict(self.labels)

    async def entrypoint_logs(self) -> str:
        """Return the sandbox entrypoint-session log as text."""
        payload = await self._require_api().send(
            RequestSpec("GET", f"/sandboxes/{quote(self.id, safe='')}/logs")
        )
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        raise create_error("expected entrypoint logs text response", 500, None, "SDK")

    async def update_env(self, env: Optional[Mapping[str, str]] = None, *, unset: Sequence[str] = ()) -> None:
        """Update SDK-managed environment entries for new processes, PTYs, and sessions.

        Processes, PTYs, and sessions started after this call inherit the updated
        environment. Running processes and already-running session kernels keep
        their current environment unchanged. ``unset`` removes only values
        previously set through ``update_env()``; image-level environment entries
        remain untouched.
        """
        await self._require_toolbox().send(
            RequestSpec(
                "POST",
                "/env",
                json_body={"env": _validated_env_map(env), "unset": _validated_env_unset(unset)},
            )
        )

    async def refresh_data(
        self,
        *,
        timeout: Optional[float] = None,
        wait: SandboxWaitState | None = None,
        wait_timeout_ms: int | None = None,
    ) -> None:
        """Refresh sandbox fields from the API."""
        await self._refresh_data(timeout=timeout, wait=wait, wait_timeout_ms=wait_timeout_ms)

    async def _refresh_data(
        self,
        *,
        timeout: Optional[float] = None,
        wait: SandboxWaitState | None = None,
        wait_timeout_ms: int | None = None,
        exact: bool = False,
    ) -> None:
        api = self._require_api()
        params: Dict[str, JsonValue] = {}
        if wait is not None:
            params["wait_exact" if exact else "wait"] = _wait_query_state(wait)
        if wait_timeout_ms is not None:
            params["timeout_ms"] = wait_timeout_ms
        # A long-poll read is bounded by its server slice plus a little slack —
        # also when the CALLER set no deadline. "No overall deadline" means the
        # wait loop keeps issuing slices, never that one stalled connection may
        # hang it forever (parity with TypeScript's `refreshDataWithin`).
        request_timeout = timeout
        if wait_timeout_ms is not None:
            slice_seconds = wait_timeout_ms / 1000
            request_timeout = (slice_seconds if timeout is None else timeout) + WAIT_READ_SLACK_SECONDS
        fresh = AsyncSandbox.from_api(
            expect_sandbox_detail(
                await api.send(
                    RequestSpec(
                        "GET",
                        f"/sandboxes/{quote(self.id, safe='')}",
                        timeout=request_timeout,
                        params=params,
                    )
                )
            ),
            api,
            None,
            toolbox_owner=self._toolbox_owner,
            toolbox_url=self._direct_toolbox_url,
            toolbox_base_url=self._toolbox_base_url,
            toolbox_token=self._toolbox_token,
            direct_toolbox_url=self._direct_toolbox_url,
            request_timeout=self._request_timeout,
            hub=self._events_hub(),
            hub_provider=self._hub_provider,
            _toolbox_url_from_api=True,
        )
        self._apply_sandbox_state(fresh)
        if (
            self._direct_toolbox_url is None
            and fresh.toolbox_proxy_url
            and fresh.toolbox_proxy_url != self.toolbox_proxy_url
        ):
            if self._toolbox is not None:
                await self._toolbox.close()
            self.toolbox_proxy_url = fresh.toolbox_proxy_url
            self._toolbox_base_url = fresh._toolbox_base_url
            self._toolbox = fresh._toolbox
            # The adopted transport carries `fresh`'s auth binding; rebind it to
            # THIS handle, whose token state the caller observes and refreshes.
            self._bind_sandbox_token_auth()
            self._process = None
            self._fs = None
            self._git = None
            self._interpreter = None
            self._pty = None
            self._computer_use = None
            self._docker = None
            self._ports = None
            self._volumes = None
            self._lsp = None

    def _apply_sandbox_state(self, fresh: AsyncSandbox) -> None:
        self.name = fresh.name
        self.state = fresh.state
        self._raw_state = fresh._raw_state
        self.image_build_id = fresh.image_build_id
        self.image = fresh.image
        self.snapshot = fresh.snapshot
        self.target = fresh.target
        self.region = fresh.region
        self.labels = dict(fresh.labels)
        self.cpu = fresh.cpu
        self.cpu_max = fresh.cpu_max
        self.memory = fresh.memory
        self.memory_max = fresh.memory_max
        self.disk = fresh.disk
        self.disk_max = fresh.disk_max
        self._mac_boot_mode = fresh._mac_boot_mode
        self.sandbox_class = fresh.sandbox_class
        self._wire_class = fresh._wire_class
        self.persistent = fresh.persistent
        self.spot = fresh.spot
        self.spot_evicted_at = fresh.spot_evicted_at
        self.lifecycle_policy = fresh.lifecycle_policy
        self.forked_from = fresh.forked_from
        self.reservation_id = fresh.reservation_id
        self.user = fresh.user
        self.error = fresh.error
        self.error_reason = fresh.error_reason
        # `fresh` came from a full Sandbox: the handle now carries the complete
        # metadata snapshot, whatever it was constructed from.
        self._metadata_loaded = True

    async def wait_for_state(
        self,
        states: Sequence[SandboxWaitState],
        terminal_states: Sequence[SandboxState] = DEFAULT_SANDBOX_TERMINAL_STATES,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        """Wait until the sandbox reaches one of ``states``.

        ``timeout`` is the caller wait budget in seconds; ``None`` and ``0``
        wait unbounded. If the sandbox reaches a terminal state before any
        target state, a typed conflict error is raised with the observed public
        sandbox state attached as ``error.state``.
        """
        if not states:
            raise create_error(
                "wait_for_state() requires at least one target state", 400, "BAD_REQUEST", "SDK"
            )

        deadline = _wait_deadline(timeout)
        target_states = frozenset(states)
        terminal_state_set = frozenset(terminal_states)
        wait_target = states[0] if len(target_states) == 1 else None
        effective_terminal = terminal_state_set - ({"destroying"} if "destroyed" in target_states else set())
        exact = (
            wait_target is not None
            and wait_query_key(_wait_query_state(wait_target), effective_terminal) == "wait_exact"
        )

        hub = self._events_hub()
        if hub is not None and hub.enabled:
            predicate = _TargetPredicate(
                states=target_states,
                terminal_states=effective_terminal,
            )
            last_poll_end: Optional[float] = None
            # Whether the latest outcome came from a full GET (the fallback
            # poll) rather than a socket frame, which carries only the state.
            refreshed_by_poll = False

            async def _sandbox_poll_fn() -> WaitOutcome:
                # The hub's long-poll fallback: one refresh per call, paced
                # like the polling loop below (the back-off floor sits
                # BETWEEN two polls, never after a resolving one).
                nonlocal last_poll_end, refreshed_by_poll
                if last_poll_end is not None:
                    pause = WAIT_FOR_STATE_MIN_INTERVAL_S - (_clock.monotonic() - last_poll_end)
                    if deadline is not None:
                        pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                    if pause > 0:
                        await asyncio.sleep(pause)
                remaining = None if deadline is None else deadline - _clock.monotonic()
                if remaining is not None and remaining <= 0:
                    raise create_error(
                        "sandbox failed to reach the requested state before timeout",
                        408,
                        "REQUEST_TIMEOUT",
                        "SDK",
                    )
                ms = (
                    WAIT_FOR_STATE_SLICE_MS
                    if remaining is None
                    else min(WAIT_FOR_STATE_SLICE_MS, max(1, math.ceil(remaining * 1000)))
                )
                await self._refresh_data(
                    timeout=None if remaining is None else ms / 1000,
                    wait=wait_target,
                    wait_timeout_ms=ms if wait_target is not None else None,
                    exact=exact,
                )
                last_poll_end = _clock.monotonic()
                refreshed_by_poll = True
                return WaitOutcome(self.state, self._raw_state)

            def _observe_outcome(outcome: WaitOutcome) -> None:
                self.state = outcome.state
                self._raw_state = outcome.raw_state

            def _on_observed(outcome: WaitOutcome) -> None:
                nonlocal refreshed_by_poll
                refreshed_by_poll = False
                _observe_outcome(outcome)

            try:
                resolved = await hub.wait(
                    self.id,
                    predicate,
                    timeout=timeout,
                    region_hint=(
                        self.target
                        if self.target is not None and self.target.strip().lower() != "local"
                        else None
                    ),
                    sandbox_poll_fn=_sandbox_poll_fn,
                    on_observed=_on_observed,
                )
            except SandboxTerminalStateError as error:
                self.state = error.state
                if error.raw_state is not None:
                    self._raw_state = error.raw_state
                # Fill in the error reason within the wait's budget. A destroyed
                # sandbox has nothing to fill in (the GET would 404), and a
                # spent budget skips it: the terminal state is the outcome
                # either way, and a failed or timed-out refresh must not
                # replace it.
                fresh_read = refreshed_by_poll
                remaining = None if deadline is None else deadline - _clock.monotonic()
                if (
                    not refreshed_by_poll
                    and error.state != "destroyed"
                    and (remaining is None or remaining > 0)
                ):
                    with contextlib.suppress(DaytonaError):
                        await self._refresh_data(timeout=remaining)
                        fresh_read = True
                # The hub reports only the state. When the read that followed
                # still shows that state, name the server's explanation for it
                # (why a deletion did not finish, say) in the error.
                # `failed` and `lost` share the public `error`; match the wire
                # status too, so one's explanation never labels the other.
                if (
                    fresh_read
                    and error.reason is None
                    and self.error_reason
                    and self.state == error.state
                    and (error.raw_state is None or self._raw_state == error.raw_state)
                ):
                    raise SandboxTerminalStateError(
                        self.id, error.state, raw_state=error.raw_state, reason=self.error_reason
                    ) from None
                raise
            _observe_outcome(resolved)
            # A socket frame carries only the state. Callers (create, start,
            # resume) hand back this handle, so fill in the rest — toolbox URL,
            # region, resources, error reason — with one GET, as the polling
            # path does, and within the same budget. A destroyed sandbox has
            # nothing to fill in (the GET would be a 404).
            if not refreshed_by_poll and resolved.state != "destroyed":
                remaining = None if deadline is None else deadline - _clock.monotonic()
                if remaining is not None and remaining <= 0:
                    raise create_error(
                        "sandbox failed to reach the requested state before timeout",
                        408,
                        "REQUEST_TIMEOUT",
                        "SDK",
                    )
                await self._refresh_data(timeout=remaining)
            return

        fresh = False
        while True:
            if fresh:
                if self.state in target_states:
                    return
                if self.state in effective_terminal:
                    raise _sandbox_terminal_state_error(
                        self.id, self.state, raw_state=self._raw_state or None, reason=self.error_reason
                    )
            remaining_timeout = None if deadline is None else deadline - _clock.monotonic()
            if remaining_timeout is not None and remaining_timeout <= 0:
                raise create_error(
                    "sandbox failed to reach the requested state before timeout",
                    408,
                    "REQUEST_TIMEOUT",
                    "SDK",
                )
            wait_timeout_ms = (
                WAIT_FOR_STATE_SLICE_MS
                if remaining_timeout is None
                else min(
                    WAIT_FOR_STATE_SLICE_MS,
                    max(1, math.ceil(remaining_timeout * 1000)),
                )
            )
            poll_started = _clock.monotonic()
            await self._refresh_data(
                timeout=None if remaining_timeout is None else wait_timeout_ms / 1000,
                wait=wait_target,
                wait_timeout_ms=wait_timeout_ms if wait_target is not None else None,
                exact=exact,
            )
            fresh = True
            if self.state in target_states:
                return
            if self.state in effective_terminal:
                raise _sandbox_terminal_state_error(
                    self.id, self.state, raw_state=self._raw_state or None, reason=self.error_reason
                )
            elapsed = _clock.monotonic() - poll_started
            if elapsed < WAIT_FOR_STATE_MIN_INTERVAL_S:
                pause = WAIT_FOR_STATE_MIN_INTERVAL_S - elapsed
                if deadline is not None:
                    pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                if pause > 0:
                    await asyncio.sleep(pause)

    async def _wait_until_started(
        self,
        *,
        timeout: Optional[float] = 60,
        terminal_states: Sequence[SandboxState] = DEFAULT_SANDBOX_TERMINAL_STATES,
    ) -> None:
        await self.wait_for_state(["started"], terminal_states, timeout=timeout)

    def _events_hub(self) -> Optional[WaitHub]:
        """The hub, attached on first use if the handle was created off-loop."""
        if self._hub is not None and getattr(self._hub, "_closed", False) and self._hub_provider is not None:
            self._hub = None
        if self._hub is None and self._hub_provider is not None:
            self._hub = self._hub_provider()
        return self._hub

    def _learn_endpoint(self, endpoint_header: Optional[str]) -> None:
        hub = self._events_hub()
        if endpoint_header and hub is not None:
            hub.learn(self.id, endpoint_header)

    def _learn_endpoint_from_headers(self, headers: Mapping[str, str]) -> None:
        self._learn_endpoint(endpoint_from_headers(headers))

    async def _lifecycle_mutation(self, spec: RequestSpec) -> None:
        await self._require_api().send(
            dataclasses.replace(spec, on_headers=self._learn_endpoint_from_headers)
        )

    async def wait(self, *, until: SandboxWaitState = "started", timeout: Optional[float] = 60) -> Self:
        """Wait for one public sandbox state and return ``self``.

        ``timeout`` is a caller wait budget in seconds. The default is 60;
        ``None`` and ``0`` wait without a deadline. Timeout raises
        ``RequestTimeoutError`` and a terminal state before ``until`` raises
        ``SandboxTerminalStateError``.
        """
        await self.wait_for_state([until], timeout=timeout)
        return self

    async def start(self, *, timeout: float = 60) -> None:
        """Start a stopped sandbox, or resume a paused one, and wait for started."""
        _validate_timeout(timeout)
        await self.refresh_data()
        if self.state == "started":
            return
        terminal_states: Sequence[SandboxState] = DEFAULT_SANDBOX_TERMINAL_STATES
        if self.state == "paused":
            await self._require_macos_lifecycle()
            await self._lifecycle_mutation(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/resume")
            )
            terminal_states = _MACOS_RESUME_TERMINAL_STATES
        elif self.state == "stopped":
            await self._require_macos_lifecycle()
            await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/start"))
        await self._wait_until_started(timeout=timeout, terminal_states=terminal_states)

    async def pause(self, *, timeout: float = 60) -> None:
        """Pause a macOS sandbox, preserving its memory.

        macOS waits up to ``timeout`` for ``paused`` and raises
        ``SandboxTerminalStateError`` if the pause fails. A paused macOS
        sandbox keeps its Mac slot but does not keep its reservation renewing.
        """
        _validate_timeout(timeout)
        await self._require_macos_lifecycle()
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/pause"))
        await self.wait_for_state(["paused"], _MACOS_PAUSE_TERMINAL_STATES, timeout=timeout)

    async def resume(self, *, timeout: float = 60) -> None:
        """Resume a paused macOS sandbox and wait for started.

        A macOS resume the Mac cannot finish ends ``stopped``; this then raises
        ``SandboxTerminalStateError``.
        """
        _validate_timeout(timeout)
        await self._require_macos_lifecycle()
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/resume"))
        await self._wait_until_started(timeout=timeout, terminal_states=_MACOS_RESUME_TERMINAL_STATES)

    async def stop(self) -> None:
        """Stop a persistent macOS sandbox."""
        await self._require_macos_lifecycle()
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/stop"))
        await self.refresh_data()

    async def fork(
        self,
        *,
        name: Optional[str] = None,
        memory: Optional[bool] = None,
        timeout: Optional[float] = None,
    ) -> AsyncSandbox:
        """Fork this sandbox.

        By default the fork continues from the source's memory (running
        processes included). ``memory=False`` copies only the disk, and the
        fork boots fresh. Linux forks are created from running sandboxes and
        start automatically.

        A macOS fork copies the disk only: omit ``memory`` or pass
        ``memory=False`` (``memory=True`` is refused with
        ``NOT_SUPPORTED_FOR_CLASS``). A fork of a running macOS sandbox boots
        with a fresh machine identity; a fork of a paused or stopped one rests
        ``stopped``. A fork needs available capacity for its size; insufficient
        capacity can answer ``NO_FREE_SLOT`` or ``MAC_DISK_FULL``.

        With ``timeout`` (seconds; ``0`` waits without a deadline), wait for
        the state the fork settles in: ``started`` for a Linux fork, and
        ``started`` or ``stopped`` for a macOS fork. Without it, return as soon
        as the server accepts the fork.
        """
        sandbox_class = await self._require_sandbox_class()
        if sandbox_class not in ("linux", "macos"):
            raise create_error(
                f"{sandbox_class} sandboxes do not support fork", 400, "UNSUPPORTED_OPERATION", "SDK"
            )
        body: JsonObject = {}
        if name is not None:
            body["name"] = name
        if memory is not None:
            body["memory"] = memory
        # A Mac fork settles `started` or `stopped`; either is a valid result
        # because the source's state can change before the request is accepted.
        if timeout is not None:
            _validate_timeout(timeout)
        data = expect_fork_resp(
            await self._require_api().send(
                RequestSpec(
                    "POST",
                    f"/sandboxes/{quote(self.id, safe='')}/fork",
                    json_body=body,
                    on_headers=self._learn_endpoint_from_headers,
                )
            )
        )
        fork_id = data.id
        api_token = getattr(self._api, "token", None) if self._api else None
        fork_toolbox_base = data.toolbox_proxy_url
        # The fork gets its own toolbox URL from the API; inheriting the
        # parent's override could send its file and process calls to the parent.
        fork = AsyncSandbox.from_api(
            expect_sandbox_detail(
                await self._require_api().send(RequestSpec("GET", f"/sandboxes/{quote(fork_id, safe='')}"))
            ),
            self._api,
            None,
            toolbox_owner=self._toolbox_owner,
            toolbox_base_url=fork_toolbox_base,
            toolbox_token=api_token,
            request_timeout=self._request_timeout,
            # The events hub IS inherited: it is per process and identity, not
            # per sandbox (unlike the toolbox override above).
            hub=self._events_hub(),
            hub_provider=self._hub_provider,
            _toolbox_url_from_api=True,
        )
        if timeout is not None:
            if sandbox_class == "macos":
                await fork.wait_for_state(["started", "stopped"], timeout=timeout)
            else:
                await fork._wait_until_started(timeout=timeout)
        return fork

    async def delete(self) -> None:
        """Request sandbox deletion.

        Returning, a ``destroyed`` state, or a subsequent 404 does not guarantee
        that the image can already be deleted. Image deletion may still return 409.
        """
        await self._lifecycle_mutation(RequestSpec("DELETE", f"/sandboxes/{quote(self.id, safe='')}"))
        try:
            await self.refresh_data()
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            self.state = "destroyed"

    async def update_lifecycle_policy(
        self,
        *,
        timeout_seconds: Optional[int] = None,
        on_timeout: Optional[LifecycleAction] = None,
        idle_expiry_seconds: Optional[int] = None,
        on_idle_timeout: Optional[LifecycleAction] = None,
    ) -> SandboxLifecycle:
        """Update this sandbox's lifecycle policy; omitted knobs stay unchanged.

        ``timeout_seconds`` re-anchors the deadline from now (``>= 1``; the timeout
        cannot be disabled). ``idle_expiry_seconds=DISABLED`` clears the idle pair;
        arming it on a sandbox with no idle policy requires ``on_idle_timeout``.
        Setting either action to ``"delete"`` — or touching a pair whose current
        or resulting action is ``"delete"`` — requires the API key to hold
        ``delete:sandboxes`` in addition to ``write:sandboxes``.
        """
        requested: LifecyclePolicyConfig = {}
        if timeout_seconds is not None:
            requested["timeout_seconds"] = timeout_seconds
        if on_timeout is not None:
            requested["on_timeout"] = on_timeout
        if idle_expiry_seconds is not None:
            requested["idle_expiry_seconds"] = idle_expiry_seconds
        if on_idle_timeout is not None:
            requested["on_idle_timeout"] = on_idle_timeout
        body = lifecycle_body(requested)
        if body is None:
            raise create_error(
                "update_lifecycle_policy requires at least one lifecycle field", 400, "BAD_REQUEST", "SDK"
            )
        if self._wire_class is None or self.persistent is None:
            await self.refresh_data()
        assert_lifecycle_class_support(
            body,
            sandbox_class=await self._require_sandbox_class(),
            persistent=True if self.persistent is None else self.persistent,
            action="update_lifecycle_policy",
        )
        data = expect_object(
            await self._require_api().send(
                RequestSpec(
                    "PATCH",
                    f"/sandboxes/{quote(self.id, safe='')}/lifecycle-policy",
                    json_body=body,
                    on_headers=self._learn_endpoint_from_headers,
                )
            )
        )
        self.lifecycle_policy = parse_lifecycle(data)
        return self.lifecycle_policy

    async def reset_idle_timer(self) -> SandboxLifecycle:
        """Reset this sandbox's idle timer (an explicit out-of-band keepalive).

        For work the platform cannot see: bumps the sandbox's activity clock
        (``last_activity_at``, attributed
        server-side to the ``api`` source), which restarts the idle countdown —
        ``idle_timeout_at`` re-anchors to now + ``idle_expiry_seconds``. The
        wall-clock ``timeout_at`` is NOT affected. Succeeds whether or not an
        idle timeout is armed (an unarmed sandbox echoes
        ``idle_timeout_at=None``), and in any state — a never-started sandbox
        already carries its creation-time activity stamp, which the reset
        advances.

        The echo is the full post-reset lifecycle, the same shape
        :meth:`update_lifecycle_policy` returns. Advance-only: a newer concurrent
        activity bump keeps the clock, and what comes back is the state that
        won.
        """
        data = expect_object(
            await self._require_api().send(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/reset-idle-timer")
            )
        )
        fresh = parse_lifecycle(data)
        # Concurrent resets can return out of order, and a late-arriving older
        # echo must not roll the cached clock backwards. A real reset always
        # echoes a clock, so a degenerate echo whose clock is missing or
        # unparseable is evidence of something wrong and never replaces a valid
        # cached one; a cache that is empty or unparseable always takes the
        # fresh echo.
        cached_at = _parse_activity_clock(
            self.lifecycle_policy.last_activity_at if self.lifecycle_policy else None
        )
        echoed_at = _parse_activity_clock(fresh.last_activity_at)
        if cached_at is None or (echoed_at is not None and echoed_at >= cached_at):
            self.lifecycle_policy = fresh
        return fresh

    async def get_network_policy(self) -> NetworkPolicyInfo:
        self._assert_known_network_policy_support()
        response = await self._require_api().get(f"/sandboxes/{quote(self.id, safe='')}/network-policy")
        return parse_network_policy_response(response)

    async def update_network_policy(
        self,
        *,
        network_policy: NetworkPolicyInput,
        if_match: str,
    ) -> NetworkPolicyInfo:
        self._assert_known_network_policy_support()
        response = await self._require_api().put(
            f"/sandboxes/{quote(self.id, safe='')}/network-policy",
            json=serialize_network_policy_input(network_policy),
            headers={"If-Match": if_match},
        )
        return parse_network_policy_response(response)

    async def reset_network_policy(self, *, if_match: str) -> NetworkPolicyInfo:
        self._assert_known_network_policy_support()
        response = await self._require_api().request(
            "PUT",
            f"/sandboxes/{quote(self.id, safe='')}/network-policy",
            content=b"null",
            headers={"If-Match": if_match, "Content-Type": "application/json"},
        )
        return parse_network_policy_response(response)

    async def get_secrets(self) -> SandboxSecretsInfo:
        """Read this sandbox's attached secret names and their apply state (``GET /sandboxes/{id}/secrets``).

        Returns names and revisions only, never values. ``etag`` is the ``If-Match`` value for
        :meth:`replace_secrets`; ``status`` is ``pending`` until the sandbox has applied
        ``desired_revision``.
        """
        response = await self._require_api().get(f"/sandboxes/{quote(self.id, safe='')}/secrets")
        return parse_sandbox_secrets_response(response)

    async def replace_secrets(
        self,
        *,
        secrets: Sequence[str],
        if_match: str,
    ) -> SandboxSecretsInfo:
        """Replace the full set of project secrets attached to this sandbox (``PUT /sandboxes/{id}/secrets``).

        ``secrets`` names project secrets; unchanged names remain attached. ``if_match`` is
        the ``etag`` of the last read; a stale one raises :class:`PreconditionFailedError`. The change
        applies asynchronously: poll :meth:`get_secrets` until ``status`` is ``applied``. Running
        processes keep their environment; processes started afterwards see the new names.
        """
        response = await self._require_api().put(
            f"/sandboxes/{quote(self.id, safe='')}/secrets",
            json=serialize_sandbox_secrets_input(secrets),
            headers={"If-Match": if_match},
        )
        return parse_sandbox_secrets_response(response)

    async def snapshot_filesystem(self, *, name: Optional[str] = None, idle: Optional[int] = None) -> Image:
        """Snapshot this sandbox's filesystem as a new :class:`Image` (``POST /sandboxes/{id}/images``).

        The result boots new sandboxes (``daytona.create(image.id)``); a Linux
        sandbox keeps running. A macOS
        sandbox must be persistent and stopped; it cannot start until the
        snapshot is ``ready``.

        ``name`` makes the image named and permanent. ``idle`` (seconds) is the
        idle-retention window of an ANONYMOUS capture: it is deleted once that
        long has passed without a sandbox being created from it (every create
        restarts the clock). Same rule as ``images.build``: omitted or ``-1`` ⇒
        the server maximum (7 days by default); above it ⇒ 400. Promote the
        image to keep it. ``idle`` with ``name`` is a 400.
        """
        body: JsonObject = {}
        if name is not None:
            body["name"] = name
        if idle is not None:
            body["idle_expiry_seconds"] = idle
        operation = expect_create_image_resp(
            await self._require_api().send(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/images", json_body=body)
            )
        )
        return parse_image(
            await self._require_api().send(RequestSpec("GET", f"/images/{quote(operation.id, safe='')}"))
        )

    async def create_snapshot(self, name: str) -> Snapshot:
        created = expect_create_snapshot_resp(
            await self._require_api().send(
                RequestSpec(
                    "POST", f"/sandboxes/{quote(self.id, safe='')}/snapshots", json_body={"name": name}
                )
            )
        )
        return parse_snapshot(
            await self._require_api().send(RequestSpec("GET", f"/snapshots/{quote(created.id, safe='')}"))
        )

    async def build_logs(self) -> str:
        """Return retained build output as one-shot text, not a live follower.

        The body includes a state line and the currently available build tail.
        A sandbox with no image build (for example a registry image) raises
        ``NotFoundError``. Retained-tail truncation is indicated in the text.
        Use :meth:`follow_build_logs` for live output and completeness metadata.
        """
        return await read_build_logs(
            self._require_api(), f"/sandboxes/{quote(self.id, safe='')}/build-logs-url"
        )

    async def follow_build_logs(
        self,
        *,
        on_logs: _LogsCallback,
        on_event: Optional[_EventCallback] = None,
        cursor: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BuildLogFollowResult:
        """Observe this sandbox's build without changing its execution lifetime.

        ``on_logs`` receives decoded output; optional ``on_event`` receives each
        typed ``BuildLogEvent``. Either callback may return an awaitable, which
        is awaited before the next event. ``timeout`` (seconds) bounds this
        observation only; cancelling the awaiting task closes the stream, not
        the build. Discovery failures, including a 404 for a sandbox with no
        image build, raise ``BuildLogInterruptedError`` with the HTTP status
        and last cursor. They do not fall back to another log source.
        """
        return await follow_build_logs(
            self._require_api(),
            f"/sandboxes/{quote(self.id, safe='')}/build-logs-url",
            on_logs=on_logs,
            on_event=on_event,
            cursor=cursor,
            timeout=timeout,
        )

    def _require_api(self) -> AsyncTransport:
        if self._api is None:
            raise create_error("sandbox handle has no API transport", 400, "BAD_REQUEST", "SDK")
        return self._api

    def _require_toolbox(self) -> AsyncTransport:
        if self._toolbox is None:
            raise create_error(
                "sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolbox_url)",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return self._toolbox

    def _assert_known_network_policy_support(self) -> None:
        if self._wire_class is not None and self.sandbox_class is None:
            raise create_error(
                f"unsupported sandbox class {self._wire_class}",
                400,
                "UNSUPPORTED_OPERATION",
                "SDK",
            )

    async def _require_sandbox_class(self) -> SandboxClass:
        """The class every class-gated call checks.

        A class this SDK does not know is refused here rather than checked as
        ``linux``.
        """
        if self.sandbox_class is None and self._wire_class is None:
            await self.refresh_data()
        sandbox_class = self.sandbox_class
        if sandbox_class is None:
            raise create_error(
                f"unsupported sandbox class {self._wire_class}", 400, "UNSUPPORTED_OPERATION", "SDK"
            )
        return sandbox_class

    async def _require_macos_lifecycle(self) -> None:
        sandbox_class = await self._require_sandbox_class()
        if sandbox_class != "macos":
            raise create_error(
                "this lifecycle operation requires a macOS sandbox", 400, "UNSUPPORTED_OPERATION", "SDK"
            )

    async def create_access_token(
        self,
        *,
        operations: Optional[Sequence[str]] = None,
        ports: Optional[Sequence[int]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> SandboxAccessToken:
        """Mint a scoped, short-lived access token for THIS sandbox.

        The token authorizes sandbox services (toolbox, SSH, desktop,
        private previews) and the self-service sandbox routes, for exactly the
        granted ``operations`` and ``ports`` — hand it to a collaborator, a CI
        job, or a browser instead of a project API key.

        ``operations`` is the sandbox-operation vocabulary (``toolbox``,
        ``toolbox:exec``, ``toolbox:fs``, ``toolbox:git``, ``toolbox:lsp``,
        ``desktop``, ``ssh``, ``sandbox:stop``, ``sandbox:pause``,
        ``sandbox:delete``); omitted means the minter's full grantable set, and
        ``[]`` is a valid ports-only grant. ``ports`` names the private-preview
        ports the token may reach (omitted = all, ``[]`` = none). ``ttl_seconds``
        overrides the deployment's default lifetime, capped server-side; an
        unknown operation or an over-cap TTL is a 400, never a silent
        narrowing. The token cannot be refreshed and cannot mint further
        credentials — mint a fresh one here instead.
        """
        body: JsonObject = {}
        if operations is not None:
            body["operations"] = list(operations)
        if ports is not None:
            for port in ports:
                _assert_port(port)
            body["ports"] = list(ports)
        if ttl_seconds is not None:
            if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
                raise create_error("ttl_seconds must be a positive integer", 400, "BAD_REQUEST", "SDK")
            body["ttl_seconds"] = ttl_seconds
        payload = await self._require_api().send(
            RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/access-token", json_body=body)
        )
        return _sandbox_access_token_from_payload(payload)

    def _sandbox_token_fresh(self) -> bool:
        """Whether the held token can be sent as-is.

        A token with no known expiry is used as-is until a 401. A known expiry
        within the 60 s lead triggers a proactive re-mint.
        """
        if not self.access_token:
            return False
        expires = _rfc3339_epoch(self.access_token_expires_at)
        if expires is None:
            return True
        return expires - time.time() > SANDBOX_TOKEN_REFRESH_LEAD_SECONDS

    async def _sandbox_token_bearer(self) -> Optional[str]:
        if self._sandbox_token_fresh():
            return self.access_token
        if self._sandbox_token_mint_unsupported:
            return await self._fallback_sandbox_bearer()
        try:
            return await self._refresh_sandbox_token(failed_token=None)
        except Exception as exc:
            if _mint_route_unsupported(exc):
                self._sandbox_token_mint_unsupported = True
                return await self._fallback_sandbox_bearer()
            # A failed proactive refresh must not fail requests a still-valid
            # token would have served: inside the 60 s refresh lead but before
            # ``expires_at``, use the held token until expiry and let the next
            # call retry the mint.
            # Only a token that is actually gone surfaces the mint error.
            expires = _rfc3339_epoch(self.access_token_expires_at)
            if self.access_token and expires is not None and expires > time.time():
                return self.access_token
            raise

    async def _fallback_sandbox_bearer(self) -> Optional[str]:
        """The bearer on a deployment without the mint routes: the held token
        until it is known to have expired, then the API credential (the
        API key, or the project token of a JWT client)."""
        expires = _rfc3339_epoch(self.access_token_expires_at)
        if self.access_token and (expires is None or expires > time.time()):
            return self.access_token
        return await self._api_credential() or self.access_token

    async def _api_credential(self) -> Optional[str]:
        return self._sandbox_token_key or await self._project_token()

    async def _project_token(self) -> Optional[str]:
        if self._project_auth is None:
            return None
        return await self._project_auth.get_token()

    async def _sandbox_token_remint(self, failed_token: Optional[str]) -> Optional[str]:
        """One re-mint after a sandbox service 401; ``None`` surfaces the 401."""
        if not self._sandbox_token_mintable:
            return None
        if self._sandbox_token_mint_unsupported:
            return await self._api_credential_retry(failed_token)
        try:
            return await self._refresh_sandbox_token(failed_token=failed_token)
        except Exception as exc:
            if _mint_route_unsupported(exc):
                self._sandbox_token_mint_unsupported = True
                return await self._api_credential_retry(failed_token)
            if getattr(exc, "status_code", None) == 403:
                # A FORBIDDEN mint is the actionable error — this credential
                # cannot mint for this sandbox — and surfacing the original
                # toolbox 401 instead would point at the wrong credential.
                raise
            # The sandbox service 401 is the actionable error; a failed re-mint
            # must not mask it.
            return None

    async def _api_credential_retry(self, failed_token: Optional[str]) -> Optional[str]:
        """Without the mint routes a rejected sandbox token is retried once with
        the API credential; the same credential failing again surfaces. A
        rejected sandbox token is dropped, so later requests go straight to the
        API credential instead of repeating the 401."""
        fallback = await self._api_credential()
        if fallback and fallback == failed_token and self._project_auth is not None:
            # The rejected token IS the cached project token: re-exchange it.
            fallback = await self._project_auth.on_unauthorized(failed_token)
        if not fallback or fallback == failed_token:
            return None
        if failed_token is not None and failed_token == self.access_token:
            self.access_token = None
            self.access_token_expires_at = None
        return fallback

    async def _refresh_sandbox_token(self, *, failed_token: Optional[str]) -> str:
        """Mint a full-grant token, single-flighted per handle.

        Concurrent callers coalesce on ONE shared mint task and share its
        result — or its exception: a failed mint must fail every joiner once,
        not serialize N sequential attempts behind a lock (the TS mirror's
        shared-promise semantics; a struggling API must not take
        N× mint load exactly when it is struggling). On the 401 path
        (``failed_token`` set) a stored token that already differs from the
        one that failed was minted by a concurrent caller and is returned
        as-is.
        """
        if failed_token is None:
            if self._sandbox_token_fresh():
                assert self.access_token is not None
                return self.access_token
        elif self.access_token and self.access_token != failed_token:
            return self.access_token
        # The lock only guards task creation; nobody holds it across the mint.
        async with self._sandbox_token_lock:
            if failed_token is None:
                if self._sandbox_token_fresh():
                    assert self.access_token is not None
                    return self.access_token
            elif self.access_token and self.access_token != failed_token:
                return self.access_token
            task = self._sandbox_token_mint
            # A finished task whose done-callback has not run yet is not
            # joinable — re-raising a settled attempt's stale exception is not
            # a fresh mint.
            if task is None or task.done():
                task = asyncio.create_task(self._mint_sandbox_token_now())
                task.add_done_callback(self._clear_sandbox_token_mint)
                self._sandbox_token_mint = task
        # A cancelled joiner must not cancel the shared mint under the other
        # joiners. `asyncio.wait` never propagates the waiter's cancellation
        # into `task` (unlike a bare await), and unlike `asyncio.shield` it
        # attaches no exception logger of its own — on 3.14+ shield's inner
        # handler would log the mint's traceback at ERROR when every joiner
        # was cancelled, even though _clear_sandbox_token_mint consumes the
        # exception.
        await asyncio.wait({task})
        return task.result()

    def _clear_sandbox_token_mint(self, done: asyncio.Task[str]) -> None:
        # Consume the exception first: with every joiner cancelled,
        # an abandoned failed mint would otherwise emit "Task exception was
        # never retrieved" at GC.
        if not done.cancelled():
            done.exception()
        if self._sandbox_token_mint is done:
            self._sandbox_token_mint = None

    async def _mint_sandbox_token_now(self) -> str:
        minted = await self.create_access_token()
        self.access_token = minted.access_token
        self.access_token_expires_at = minted.expires_at
        return minted.access_token

    async def _mint_preview_token(self, port: int) -> Optional[SandboxAccessToken]:
        """A ports-scoped token for one private preview port, or ``None``.

        ``None`` (caller-supplied token, no key, or a deployment without the
        mint routes) tells the ports namespace to fall back to the
        route-returned preview token.
        """
        if not self._sandbox_token_mintable or self._sandbox_token_mint_unsupported:
            return None
        try:
            minted = await self.create_access_token(operations=[], ports=[port])
        except Exception as exc:
            if _mint_route_unsupported(exc):
                self._sandbox_token_mint_unsupported = True
                return None
            raise
        return minted

    async def _sign_file_url(self, method: str, path: str, expires: int) -> TransportPayload:
        # Pre-signed file URLs are minted by the API (``POST …/files/sign``).
        # ``method`` is ``GET`` for a download link and ``POST`` for
        # an upload link; ``expires`` is absolute unix seconds, bounded by the
        # caller (``SIGNED_FILE_MAX_TTL_SECONDS``) before this runs. The raw
        # decoded body travels to the filesystem payload parser unshaped: a
        # JSON ``null`` or non-object answer is its invalid-payload error
        # (parity with the TS SDK), not the transport's object guard.
        self._require_proxy_toolbox_for_presigned_urls()
        return await self._require_api().send(
            RequestSpec(
                "POST",
                f"/sandboxes/{quote(self.id, safe='')}/files/sign",
                json_body=SignSandboxFileUrlRequest(
                    method=method,
                    path=path,
                    expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
                ).model_dump(mode="json"),
            )
        )

    async def rotate_signing_key(self) -> None:
        """Rotate the sandbox's signing key, invalidating existing pre-signed URLs.

        Every pre-signed URL minted so far stops working within ~30 s; new URLs
        from ``presigned_download_url`` / ``presigned_upload_url`` use the new
        key. The key itself is never returned.

        Like minting, this requires the API-provided toolbox URL or a
        sandbox-scoped URL template, rather than an arbitrary custom endpoint.
        """
        self._require_proxy_toolbox_for_presigned_urls()
        await self._require_api().send(
            RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/signing-key/rotate")
        )

    def _require_proxy_toolbox_for_presigned_urls(self) -> None:
        if self._uses_direct_toolbox_for_presigned_urls():
            raise create_error(
                _CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE,
                400,
                "UNSUPPORTED_OPERATION",
                "SDK",
            )

    def _uses_direct_toolbox_for_presigned_urls(self) -> bool:
        # Trust the URL from the API or serialized handle. A per-sandbox
        # override must explicitly identify the sandbox; hostnames alone
        # cannot establish whether an arbitrary endpoint accepts signed URLs.
        if self._direct_toolbox_url is None:
            return False
        return not _looks_like_scoped_proxy_toolbox_url(self._direct_toolbox_url, self.id)


def sandbox_info_from_id(sandbox_id: str) -> SandboxInfo:
    return SandboxInfo(id=sandbox_id)


def _parse_activity_clock(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Normalize to aware UTC: comparing a naive and an aware datetime raises
    # TypeError, which would turn an already-applied reset into an SDK error.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _assert_port(port: int) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise create_error(f"port must be an integer in 1..65535, got {port!r}", 400, "BAD_REQUEST", "SDK")


def _sandbox_toolbox_base(
    sandbox_data: SandboxDto | SandboxListItem | Mapping[str, JsonValue],
) -> Optional[str]:
    if isinstance(sandbox_data, (SandboxDto, SandboxListItem)):
        value = sandbox_data.toolbox_proxy_url
    else:
        value = sandbox_data.get("toolbox_proxy_url") or sandbox_data.get("toolboxProxyUrl")
    return str(value).rstrip("/") if isinstance(value, str) and value else None


def _optional_number(value: object) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _gib(mib: object, *, exact: bool) -> Optional[float]:
    """GiB from a wire MiB amount: exact for macOS (4608 MiB -> 4.5, 117760 MiB -> 115), whole and rounded up otherwise."""
    if not isinstance(mib, int) or isinstance(mib, bool):
        return None
    return mib / 1024 if exact else mib_to_gb(mib)


def _string_map_or_empty(value: object) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}


def _validated_labels(labels: object) -> Dict[str, str]:
    if not isinstance(labels, MappingABC):
        raise create_error(
            "labels must be a mapping of string keys to string values", 400, "BAD_REQUEST", "SDK"
        )
    validated: Dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise create_error(
                "labels must be a mapping of string keys to string values", 400, "BAD_REQUEST", "SDK"
            )
        validated[key] = value
    return validated


def _validated_env_map(env: Optional[Mapping[str, str]]) -> Dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, MappingABC):
        raise create_error("env must be a mapping of string keys to string values", 400, "BAD_REQUEST", "SDK")
    validated: Dict[str, str] = {}
    for key, value in env.items():
        _validate_env_key(key)
        if not isinstance(value, str):
            raise create_error("env values must be strings", 400, "BAD_REQUEST", "SDK")
        if "\0" in value:
            raise create_error("env values must not contain NUL", 400, "BAD_REQUEST", "SDK")
        validated[key] = value
    return validated


def _validated_env_unset(unset: Sequence[str]) -> list[str]:
    if isinstance(unset, (str, bytes)) or not isinstance(unset, SequenceABC):
        raise create_error("env unset must be a non-string sequence of strings", 400, "BAD_REQUEST", "SDK")
    validated: list[str] = []
    for key in unset:
        _validate_env_key(key)
        validated.append(key)
    return validated


def _validate_env_key(key: str) -> None:
    if not isinstance(key, str):
        raise create_error("env keys must be strings", 400, "BAD_REQUEST", "SDK")
    # Reject invalid environment names locally. The API rejects reserved
    # names with a 400 naming the key.
    if not key or any(invalid in key for invalid in ("=", "\0", "\n", "\r")):
        raise create_error(_INVALID_ENV_KEY_REASON, 400, "BAD_REQUEST", "SDK")


def _put_handle_json_value(payload: Dict[str, JsonValue], key: str, value: JsonValue) -> None:
    if value is not None:
        payload[key] = value


def _sandbox_lifecycle_handle_json(value: SandboxLifecycle) -> JsonObject:
    payload: JsonObject = {}
    _put_handle_json_value(payload, "timeoutSeconds", value.timeout_seconds)
    _put_handle_json_value(payload, "onTimeout", value.on_timeout)
    _put_handle_json_value(payload, "timeoutAt", value.timeout_at)
    _put_handle_json_value(payload, "idleExpirySeconds", value.idle_expiry_seconds)
    _put_handle_json_value(payload, "onIdleTimeout", value.on_idle_timeout)
    _put_handle_json_value(payload, "idleTimeoutAt", value.idle_timeout_at)
    _put_handle_json_value(payload, "lastActivityAt", value.last_activity_at)
    _put_handle_json_value(payload, "lastActivitySource", value.last_activity_source)
    return payload


def _sandbox_info_has_metadata(info: SandboxInfo) -> bool:
    return info != SandboxInfo(id=info.id)


# Both placeholder spellings are documented (README.md) and must work in
# either SDK: a template is often copied between a Python and a TypeScript
# integration verbatim.
_SANDBOX_ID_PLACEHOLDERS = ("{sandbox_id}", "{sandboxId}")


def _has_sandbox_id_placeholder(url: str) -> bool:
    return any(placeholder in url for placeholder in _SANDBOX_ID_PLACEHOLDERS)


def _assert_placeholder_in_path(url: str) -> None:
    # URL templates support sandbox IDs only in the path, not the hostname,
    # scheme, query or fragment.
    parsed = urlsplit(url)
    # Everything that is not the path: scheme, authority, query, fragment.
    if any(
        _has_sandbox_id_placeholder(part)
        for part in (parsed.scheme, parsed.netloc, parsed.query, parsed.fragment)
    ):
        raise create_error(
            "the {sandbox_id} placeholder in a toolbox URL is only supported in the path "
            "(e.g. https://toolbox.example/sandboxes/{sandbox_id}), not in the scheme, host, query or fragment",
            400,
            "BAD_REQUEST",
            "SDK",
        )


def _expand_sandbox_id_placeholder(url: str, sandbox_id: str) -> str:
    _assert_placeholder_in_path(url)
    # Percent-encode like TypeScript's strictUriEncode (parity): the id is one
    # path segment, so '/' and ' ' in it must not split or corrupt the path.
    encoded = quote(sandbox_id, safe="")
    for placeholder in _SANDBOX_ID_PLACEHOLDERS:
        url = url.replace(placeholder, encoded)
    return url


def _scoped_toolbox_url(sandbox_id: str, *, direct: Optional[str], base: Optional[str]) -> Optional[str]:
    if direct:
        trimmed = direct.rstrip("/")
        if _has_sandbox_id_placeholder(trimmed):
            return _expand_sandbox_id_placeholder(trimmed, sandbox_id)
        return trimmed
    if not base:
        return None
    trimmed = base.rstrip("/")
    if _has_sandbox_id_placeholder(trimmed):
        return _expand_sandbox_id_placeholder(trimmed, sandbox_id)
    return f"{trimmed}/{sandbox_id}"


def _looks_like_scoped_proxy_toolbox_url(toolbox_url: str, sandbox_id: str) -> bool:
    trimmed = toolbox_url.rstrip("/")
    if _has_sandbox_id_placeholder(trimmed):
        return True
    path = urlsplit(trimmed).path.rstrip("/")
    encoded_id = quote(sandbox_id, safe="")
    return path.endswith((f"/sandboxes/{sandbox_id}", f"/sandboxes/{encoded_id}"))


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout < 0:
        raise create_error("timeout must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")


def _wait_deadline(timeout: Optional[float]) -> Optional[float]:
    if timeout is None or timeout == 0:
        return None
    _validate_timeout(timeout)
    return _clock.monotonic() + timeout


def _wait_query_state(state: SandboxWaitState) -> str:
    match state:
        case "queued":
            return "queued"
        case "starting":
            return "booting"
        case "started":
            return "running"
        case "pausing" | "paused" | "stopping" | "stopped":
            return state
        case "destroying":
            return "deleting"
        case "destroyed":
            return "deleted"
        case unreachable:
            raise AssertionError(f"unexpected wait state: {unreachable}")


def _sandbox_terminal_state_error(
    sandbox_id: str, state: SandboxState, *, raw_state: Optional[str] = None, reason: Optional[str] = None
) -> Exception:
    return SandboxTerminalStateError(sandbox_id, state, raw_state=raw_state, reason=reason)


def _known_sandbox_class(value: str) -> Optional[SandboxClass]:
    for known in _SANDBOX_CLASSES:
        if value == known:
            return known
    return None


def _sandbox_access_token_from_payload(payload: object) -> SandboxAccessToken:
    """The minted token, or ``BadRequestError`` naming the field that breaks the
    response contract (the TypeScript SDK's messages for the same body)."""
    if not isinstance(payload, dict):
        raise create_error("access token response is not a JSON object", 400, "BAD_REQUEST", "SDK")
    access_token = payload.get("access_token")
    expires_at = payload.get("expires_at")
    if not isinstance(access_token, str) or not access_token:
        raise create_error(
            "access token response access_token must be a non-empty string", 400, "BAD_REQUEST", "SDK"
        )
    if not isinstance(expires_at, str):
        raise create_error("access token response expires_at must be a string", 400, "BAD_REQUEST", "SDK")
    token_type = payload.get("token_type")
    raw_operations = payload.get("operations")
    operations = (
        [operation for operation in raw_operations if isinstance(operation, str)]
        if isinstance(raw_operations, list)
        else []
    )
    raw_ports = payload.get("ports")
    granted_ports = (
        [port for port in raw_ports if isinstance(port, int) and not isinstance(port, bool)]
        if isinstance(raw_ports, list)
        else None
    )
    return SandboxAccessToken(
        access_token=access_token,
        token_type=token_type if isinstance(token_type, str) else "Bearer",
        expires_at=expires_at,
        operations=operations,
        ports=granted_ports,
    )


def _rfc3339_epoch(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _mint_route_unsupported(exc: Exception) -> bool:
    """A mint failure indicating the access-token route is unavailable.

    404/405/501 on ``POST …/access-token`` marks the route absent; the handle keeps
    ``access_token or api key`` behavior instead of failing every sandbox service
    call.
    """
    return getattr(exc, "status_code", None) in (404, 405, 501)


def _isoformat_or_none(value: datetime | None) -> Optional[str]:
    if value is not None:
        return value.isoformat().replace("+00:00", "Z")
    return None
