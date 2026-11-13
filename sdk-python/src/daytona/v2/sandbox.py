# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import enum
import math
import time
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Callable,
    ClassVar,
    Dict,
    Final,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    get_args,
)
from urllib.parse import quote, urlsplit

import anyio
from typing_extensions import Self

from . import _clock
from ._api_core import (
    expect_create_image_resp,
    expect_create_snapshot_resp,
    expect_fork_resp,
    expect_image,
    expect_sandbox_lifecycle_policy,
    expect_snapshot,
    sandbox_detail_wire,
)
from ._auth import ProjectTokenAuth
from ._body_limit import DEFAULT_MAX_RESPONSE_BYTES
from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    TransportPayload,
    artifact_ref_from_wire,
    assert_lifecycle_class_support,
    check_label_keys,
    enum_str,
    expect_object,
    expect_system_metrics,
    finite_number,
    header_text,
    image_options_body,
    lifecycle_body,
    mib_to_gib,
    parse_lifecycle,
    parse_snapshot,
    parse_timestamp,
    path_segment,
    preview_port,
    rfc3339,
    sandbox_state,
    wait_budget_seconds,
)
from ._events import WaitHub, WaitOutcome, _TargetPredicate, endpoint_from_headers, region_of, wait_query_key
from ._gen.api import (
    Sandbox as SandboxDto,
)
from ._gen.api import (
    SandboxListItem,
    SignSandboxFileUrlRequest,
)
from ._timestamps import rfc3339_timestamp
from ._transport import AsyncTransport
from ._wait import WAIT_FOR_STATE_MIN_INTERVAL_S
from .build_logs import (
    BuildLogEventCallback,
    BuildLogFollowResult,
    BuildLogsCallback,
    follow_build_logs,
    read_build_logs,
)
from .computer_use import AsyncComputerUseService
from .docker import AsyncDockerService
from .errors import (
    AuthenticationError,
    ClientTimeoutError,
    DaytonaError,
    ForbiddenError,
    NotFoundError,
    RequestTimeoutError,
    SandboxTerminalStateError,
    create_error,
)
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
    ArtifactRefJSON,
    AsyncInterpreterContext,
    Image,
    ImageRef,
    InterpreterContextJSON,
    InterpreterResult,
    JsonObject,
    JsonValue,
    LifecycleAction,
    LifecyclePolicyOptions,
    MacBootMode,
    NetworkPolicyInfo,
    NetworkPolicyInput,
    ProcessResult,
    RunKeepLogs,
    SandboxAccessToken,
    SandboxClass,
    SandboxHandleJSON,
    SandboxLifecycle,
    SandboxLifecycleJSON,
    SandboxSecretsInfo,
    SandboxState,
    SandboxWaitState,
    Snapshot,
    SnapshotRef,
    StdinMode,
    SystemMetrics,
    _SandboxInfo,
)

CODE_TOOLBOX_LANGUAGE_LABEL = "code-toolbox-language"
_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE = (
    "pre-signed URLs require the API-provided toolbox URL or a sandbox-scoped URL template"
)
WAIT_FOR_STATE_SLICE_MS = 30_000
# The end of a sandbox wait's budget kept for a read of the server when the
# events stream has decided nothing: at most 5 s, at most a quarter of it.
WAIT_FINAL_READ_RESERVE_S = 5.0
# A failed pause returns to ``started`` and an unfinished resume ends ``stopped``;
# after those requests each state means failure, not something to keep waiting past.
_PAUSE_TERMINAL_STATES: tuple[SandboxState, ...] = (*DEFAULT_SANDBOX_TERMINAL_STATES, "started")
_RESUME_TERMINAL_STATES: tuple[SandboxState, ...] = (*DEFAULT_SANDBOX_TERMINAL_STATES, "stopped")
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


class _Unset(enum.Enum):
    """``_update``'s "leave this field as it is" marker (``None`` is a value)."""

    UNSET = 0


_UNSET = _Unset.UNSET


class AsyncSandbox:
    """A sandbox handle with process/fs/git/interpreter/pty namespaces.

    Built by the client (``create*()``, ``get()``, ``list()``, ``fork()``); the
    constructor is internal. Observed metadata is read-only and changes only
    when the SDK reads the sandbox again (``refresh_data()``, a wait).
    """

    # Metadata getters the generated synchronous ``Sandbox`` reads directly: each
    # returns a field of an immutable snapshot the loop replaces whole.
    _SYNC_DIRECT_PROPERTIES: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "name",
            "state",
            "raw_state",
            "image",
            "snapshot",
            "image_build_id",
            "region",
            "labels",
            "cpu",
            "cpu_max",
            "memory",
            "memory_max",
            "disk",
            "disk_max",
            "sandbox_class",
            "raw_sandbox_class",
            "mac_boot_mode",
            "persistent",
            "spot",
            "spot_evicted_at",
            "lifecycle_policy",
            "forked_from",
            "reservation_id",
            "user",
            "error",
            "access_token",
            "access_token_expires_at",
        }
    )

    def __init__(
        self,
        _info: _SandboxInfo,
        _api: Optional[AsyncTransport],
        _toolbox: Optional[AsyncTransport],
        *,
        _toolbox_owner: Optional[AsyncTransport] = None,
        _toolbox_url: Optional[str] = None,
        _language: Optional[str] = None,
        _access_token: Optional[str] = None,
        _toolbox_base_url: Optional[str] = None,
        _toolbox_token: Optional[str] = None,
        _direct_toolbox_url: Optional[str] = None,
        _request_timeout: Optional[float] = None,
        _access_token_expires_at: Optional[datetime | str] = None,
        _access_token_caller_supplied: Optional[bool] = None,
        _hub: Optional[WaitHub] = None,
        _hub_provider: Optional[Callable[[], Optional[WaitHub]]] = None,
    ) -> None:
        api = _api
        access_token = _access_token
        info = _info
        if info.region is None:
            region = region_of(info.id)
            if region != "_":
                info = dataclasses.replace(info, region=region)
        self._info = info
        self._access_token = access_token
        self._access_token_expires_at = parse_timestamp(_access_token_expires_at)
        # Unstated (None) defaults to "an explicitly passed token is the
        # caller's": only the SDK's own construction paths, whose token came
        # from the create response, pass False — the TS twin's
        # accessTokenFromCreate shape. A wrong default here silently replaces
        # a caller's scoped credential with a freshly minted full-grant one.
        access_token_caller_supplied = _access_token_caller_supplied
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
        # with an API credential (a key, or a JWT client's project token)
        # mints and refreshes sandbox access tokens
        # transparently.
        self._sandbox_token_mintable = not access_token_caller_supplied and api_transport_has_credential
        self._sandbox_token_lock = asyncio.Lock()
        self._sandbox_token_mint: Optional[asyncio.Task[str]] = None
        self._sandbox_token_mint_unsupported = False
        self._toolbox_proxy_url = _toolbox_url
        self._direct_toolbox_url = _direct_toolbox_url
        self._toolbox_base_url = _toolbox_base_url
        self._toolbox_token = _toolbox_token or access_token
        self._request_timeout = _request_timeout
        self._api = api
        self._toolbox = _toolbox
        self._toolbox_owner = _toolbox_owner
        self._language = _language
        self._hub = _hub
        self._hub_provider = _hub_provider
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

    def _update(
        self,
        *,
        state: Union[SandboxState, _Unset] = _UNSET,
        raw_state: Union[Optional[str], _Unset] = _UNSET,
        labels: Union[Optional[Mapping[str, str]], _Unset] = _UNSET,
        sandbox_class: Union[Optional[Literal["linux", "macos", "unknown"]], _Unset] = _UNSET,
        raw_sandbox_class: Union[Optional[str], _Unset] = _UNSET,
        persistent: Union[Optional[bool], _Unset] = _UNSET,
        lifecycle_policy: Union[Optional[SandboxLifecycle], _Unset] = _UNSET,
        wire_timestamps: Union[Mapping[str, str], _Unset] = _UNSET,
    ) -> None:
        """Replace observed metadata fields (internal: the SDK's own reads)."""
        info = self._info
        if not isinstance(lifecycle_policy, _Unset):
            info = dataclasses.replace(info, lifecycle_policy=lifecycle_policy)
            if isinstance(wire_timestamps, _Unset):
                # A policy read without its wire strings must not keep the old ones.
                wire_timestamps = {
                    key: value for key, value in info.wire_timestamps.items() if key == "spotEvictedAt"
                }
        if not isinstance(wire_timestamps, _Unset):
            info = dataclasses.replace(info, wire_timestamps=wire_timestamps)
        if not isinstance(state, _Unset):
            info = dataclasses.replace(info, state=state)
        if not isinstance(raw_state, _Unset):
            info = dataclasses.replace(info, raw_state=raw_state)
        if not isinstance(labels, _Unset):
            info = dataclasses.replace(info, labels=labels)
        if not isinstance(sandbox_class, _Unset):
            info = dataclasses.replace(info, sandbox_class=sandbox_class)
        if not isinstance(raw_sandbox_class, _Unset):
            info = dataclasses.replace(info, raw_sandbox_class=raw_sandbox_class)
        if not isinstance(persistent, _Unset):
            info = dataclasses.replace(info, persistent=persistent)
        self._info = info

    def _update_lifecycle(self, policy: SandboxLifecycle, data: Mapping[str, JsonValue]) -> None:
        """Replace the lifecycle policy with one read from ``data``, keeping its wire timestamps."""
        evicted = {key: value for key, value in self._info.wire_timestamps.items() if key == "spotEvictedAt"}
        self._update(lifecycle_policy=policy, wire_timestamps={**evicted, **_lifecycle_wire_timestamps(data)})

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def name(self) -> Optional[str]:
        """The sandbox name; ``None`` until the API reports it."""
        return self._info.name

    @property
    def state(self) -> SandboxState:
        """The last observed state; ``"unknown"`` before any read and for a state this SDK does not know."""
        return self._info.state

    @property
    def raw_state(self) -> Optional[str]:
        """Wire status string exactly as the server sent it (``None`` before any read).

        Informational only — the SDK never branches on this value.  Use
        :attr:`state` for all flow-control logic; ``raw_state`` is useful for
        logging when ``state == "unknown"`` (the SDK does not recognize the value).
        """
        return self._info.raw_state

    @property
    def image(self) -> Optional[ImageRef]:
        """The image the sandbox booted, or ``None`` (a snapshot restore, or not yet known)."""
        return self._info.image

    @property
    def snapshot(self) -> Optional[SnapshotRef]:
        """The memory snapshot this sandbox resumed, or ``None`` when it booted an image."""
        return self._info.snapshot

    @property
    def image_build_id(self) -> Optional[str]:
        """The image build this sandbox booted from, or ``None`` for other image sources."""
        return self._info.image_build_id

    @property
    def region(self) -> Optional[str]:
        return self._info.region

    @property
    def labels(self) -> Optional[Mapping[str, str]]:
        """The sandbox labels: a fresh copy on every read (changing it changes
        nothing); replace them with :meth:`set_labels`. ``None`` until the API
        has reported them: a ``create*()`` or ``fork()`` handle whose wait read
        nothing holds only what the create response carries (call
        :meth:`refresh_data` to load them)."""
        return None if self._info.labels is None else dict(self._info.labels)

    @property
    def cpu(self) -> Optional[float]:
        """Reserved CPU (the guarantee), in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU)."""
        return self._info.cpu

    @property
    def cpu_max(self) -> Optional[float]:
        """CPU limit (the boot shape), in cores; ``None`` when the API did not report it."""
        return self._info.cpu_max

    @property
    def memory(self) -> Optional[float]:
        """Memory in GiB, exactly the API's MiB / 1024 (4608 MiB -> 4.5)."""
        return self._info.memory

    @property
    def memory_max(self) -> Optional[float]:
        return self._info.memory_max

    @property
    def disk(self) -> Optional[float]:
        """Disk in GiB, exactly the API's MiB / 1024."""
        return self._info.disk

    @property
    def disk_max(self) -> Optional[float]:
        return self._info.disk_max

    @property
    def sandbox_class(self) -> Optional[Literal["linux", "macos", "unknown"]]:
        """``"linux"``, ``"macos"``, or ``"unknown"`` for a class this SDK does not know
        (see :attr:`raw_sandbox_class`); ``None`` until the class is known."""
        return self._info.sandbox_class

    @property
    def raw_sandbox_class(self) -> Optional[str]:
        """The class exactly as the API reported it, or ``None`` until known."""
        return self._info.raw_sandbox_class

    @property
    def mac_boot_mode(self) -> Optional[MacBootMode]:
        """macOS only: how the sandbox resumed, as reported by the API, or None."""
        return self._info.mac_boot_mode

    @property
    def persistent(self) -> Optional[bool]:
        """macOS only: whether a stop keeps the disk; ``None`` until known."""
        return self._info.persistent

    @property
    def spot(self) -> Optional[bool]:
        return self._info.spot

    @property
    def spot_evicted_at(self) -> Optional[datetime]:
        return self._info.spot_evicted_at

    @property
    def lifecycle_policy(self) -> Optional[SandboxLifecycle]:
        return self._info.lifecycle_policy

    @property
    def forked_from(self) -> Optional[str]:
        return self._info.forked_from

    @property
    def reservation_id(self) -> Optional[str]:
        """macOS only: the reservation selected by the current or latest successful claim.

        ``None`` before the first claim, and on search reads (``search()``),
        which omit it: ``refresh_data()`` reads it.
        """
        return self._info.reservation_id

    @property
    def user(self) -> Optional[str]:
        return self._info.user

    @property
    def error(self) -> Optional[str]:
        """The failure the API recorded, or ``None``."""
        return self._info.error

    @property
    def access_token(self) -> Optional[str]:
        """The sandbox-scoped bearer this handle currently authenticates with.

        It comes from the create response (returned only by the first call of
        a named get-or-create), a caller override (``get(id, access_token=...)``)
        or a token the handle minted; ``None`` until one is known. Never
        serialized by :meth:`to_json`.
        """
        return self._access_token

    @property
    def access_token_expires_at(self) -> Optional[datetime]:
        """Expiry of :attr:`access_token`, or ``None`` when not reported."""
        return self._access_token_expires_at

    @classmethod
    def _from_api(
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
        access_token_expires_at: Optional[datetime | str] = None,
        access_token_caller_supplied: Optional[bool] = None,
        hub: Optional[WaitHub] = None,
        hub_provider: Optional[Callable[[], Optional[WaitHub]]] = None,
        toolbox_url_from_api: bool = False,
    ) -> AsyncSandbox:
        """Build a handle from an API response (internal).

        An explicitly passed ``access_token`` is treated as caller-supplied
        (never silently replaced by a minted one) unless
        ``access_token_caller_supplied=False`` marks it as the SDK-owned
        create-response credential. A ``toolbox_proxy_url`` inside
        ``sandbox_data`` is used only when ``toolbox_url_from_api`` marks the
        data as a fresh API response, so stored data never chooses where a
        credential goes.
        """
        data: Mapping[str, JsonValue] = (
            sandbox_data.to_dict()
            if isinstance(sandbox_data, (SandboxDto, SandboxListItem))
            else sandbox_data
        )
        info = _sandbox_info_from_wire(data)
        toolbox_base = _sandbox_toolbox_base(data) if toolbox_url_from_api else None
        if toolbox is None:
            base = toolbox_base_url or toolbox_base or toolbox_url
            resolved = _scoped_toolbox_url(info.id, direct=toolbox_url, base=base)
            if resolved:
                if toolbox_owner is not None:
                    toolbox = toolbox_owner.scoped(resolved, toolbox_token or access_token)
                else:
                    # Inherit the api transport's X-Daytona-Source label so a
                    # handle built without the client is attributed like its root.
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
        return cls(
            info,
            api,
            toolbox,
            _toolbox_owner=toolbox_owner,
            _toolbox_url=toolbox_url,
            _language=language,
            _access_token=access_token,
            _toolbox_base_url=toolbox_base_url,
            _toolbox_token=toolbox_token,
            _direct_toolbox_url=direct_toolbox_url,
            _request_timeout=request_timeout,
            _access_token_expires_at=access_token_expires_at,
            _access_token_caller_supplied=access_token_caller_supplied,
            _hub=hub,
            _hub_provider=hub_provider,
        )

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
                _sandbox_id=self.id,
                _mint_preview_token=self._mint_preview_token,
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
        :param stdin: Only ``"closed"`` (default); ``"open"`` is refused, as by
            :meth:`process.run`.
        :param execution_timeout: Server-side deadline in seconds. The sandbox kills
            the process tree when this expires. Omitted or ``0`` means no deadline.
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
        /,
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

    def to_json(self) -> SandboxHandleJSON:
        """The handle's metadata as :class:`SandboxHandleJSON` (camelCase, no URL, no credential).

        A snapshot, not a way back: reconnect to a stored sandbox with
        ``client.get(json["sandboxId"])``. A key is present only when the handle
        knows its value; timestamps are RFC 3339 strings.
        """
        info = self._info
        payload: SandboxHandleJSON = {"sandboxId": info.id}
        if info.name is not None:
            payload["name"] = info.name
        if info.state != "unknown" or info.raw_state:
            payload["state"] = info.state
        if info.raw_state:
            payload["rawState"] = info.raw_state
        if info.image is not None:
            payload["image"] = _artifact_json(info.image.id, info.image.name)
        if info.snapshot is not None:
            payload["snapshot"] = _artifact_json(info.snapshot.id, info.snapshot.name)
        if info.region is not None:
            payload["region"] = info.region
        if info.labels is not None:
            payload["labels"] = dict(info.labels)
        # An integral size is written as an integer, as JavaScript's JSON does.
        if info.cpu is not None:
            payload["cpu"] = _json_number(info.cpu)
        if info.cpu_max is not None:
            payload["cpuMax"] = _json_number(info.cpu_max)
        if info.memory is not None:
            payload["memory"] = _json_number(info.memory)
        if info.memory_max is not None:
            payload["memoryMax"] = _json_number(info.memory_max)
        if info.disk is not None:
            payload["disk"] = _json_number(info.disk)
        if info.disk_max is not None:
            payload["diskMax"] = _json_number(info.disk_max)
        if info.sandbox_class is not None:
            payload["sandboxClass"] = info.sandbox_class
        if info.raw_sandbox_class is not None:
            payload["rawSandboxClass"] = info.raw_sandbox_class
        if info.persistent is not None:
            payload["persistent"] = info.persistent
        if info.spot is not None:
            payload["spot"] = info.spot
        if info.spot_evicted_at is not None:
            payload["spotEvictedAt"] = info.wire_timestamps.get("spotEvictedAt") or rfc3339(
                info.spot_evicted_at
            )
        if info.lifecycle_policy is not None:
            payload["lifecyclePolicy"] = _sandbox_lifecycle_handle_json(
                info.lifecycle_policy, info.wire_timestamps
            )
        if info.forked_from is not None:
            payload["forkedFrom"] = info.forked_from
        if info.reservation_id is not None:
            payload["reservationId"] = info.reservation_id
        if info.user is not None:
            payload["user"] = info.user
        if info.error is not None:
            payload["error"] = info.error
        if info.image_build_id is not None:
            payload["imageBuildId"] = info.image_build_id
        return payload

    async def set_labels(self, labels: Mapping[str, str], /) -> Mapping[str, str]:
        """Replace the sandbox's labels; pass ``{}`` to clear all but ``name``.

        The reserved ``name`` label, which :meth:`Daytona.find_by_name` and a
        ``name`` label search match, is kept unless ``labels`` sets it: without
        a ``name`` key the sandbox is read first (one more request, which also
        refreshes this handle's metadata) and the ``name`` label it reports,
        if any, is sent along (a read without labels reports none); the read and the write are not atomic. The ``name``
        label cannot be removed. Returns the labels the API applied, as a fresh
        plain ``dict`` (typed ``Mapping``, as :attr:`labels` is): changing it
        changes nothing. An empty label key is refused with
        :class:`~daytona.v2.errors.BadRequestError` before any request. A
        sandbox that is deleted or being deleted raises
        :class:`ConflictError` (``CONFLICT``); one the API no longer finds raises
        :class:`NotFoundError`.
        """
        body = _validated_labels(labels)
        if "name" not in body:
            await self.refresh_data()
            # A detail read always sets labels (absent means none); the type also
            # allows the unknown labels of a handle no read has filled.
            labels_now = self.labels
            current = None if labels_now is None else labels_now.get("name")
            if current is not None:
                body["name"] = current
        response = expect_object(
            await self._require_api().send(
                RequestSpec(
                    "PUT",
                    f"/sandboxes/{quote(self.id, safe='')}/labels",
                    json_body={"labels": body},
                )
            )
        )
        # The labels route answers `SandboxLabels` ({labels, id}), never a Sandbox:
        # its required label map of strings, or a malformed response, as in TypeScript.
        applied = _response_labels(response.get("labels"))
        self._update(labels=applied)
        return dict(applied)

    async def entrypoint_logs(self) -> str:
        """Return the sandbox entrypoint-session log as text."""
        payload = await self._require_api().send(
            RequestSpec("GET", f"/sandboxes/{quote(self.id, safe='')}/logs")
        )
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        raise create_error("entrypoint logs response is not text", 400, "BAD_REQUEST", "SDK")

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

    async def refresh_data(self) -> None:
        """Read the sandbox's metadata from the API again."""
        await self._refresh_data_within()

    async def _refresh_data_within(
        self,
        *,
        timeout: Optional[float] = None,
        wait: SandboxWaitState | None = None,
        wait_timeout_ms: int | None = None,
        deadline: Optional[float] = None,
        exact: bool = False,
    ) -> None:
        """Read the sandbox's metadata; with ``wait``, as one long-poll slice.

        ``deadline`` (a ``_clock.monotonic()`` instant) is the caller's wait
        deadline: the read never outlives it, and a read the deadline cut is the
        wait running out (:class:`RequestTimeoutError`, source ``SDK``), as in
        TypeScript's ``refreshDataWithin``. ``exact`` holds the long poll with
        ``wait_exact`` instead of ``wait`` (a wait for ``destroyed`` alone,
        :func:`wait_query_key`).
        """
        api = self._require_api()
        params: Dict[str, JsonValue] = {}
        if wait is not None:
            params["wait_exact" if exact else "wait"] = _wait_query_state(wait)
        root = getattr(api, "request_timeout_seconds", 0) or 0
        request_timeout = timeout if timeout is None or root <= 0 else min(timeout, root)
        if wait_timeout_ms is not None:
            # A long poll is governed by the wait budget, not the request
            # timeout. Its read is bounded by the hold plus a little slack —
            # also when the CALLER set no deadline: "no overall deadline" means
            # the wait loop keeps issuing slices, never that one stalled
            # connection may hang it forever (parity with TypeScript's
            # `refreshDataWithin`).
            params["timeout_ms"] = wait_timeout_ms
            request_timeout = wait_timeout_ms / 1000 + WAIT_READ_SLACK_SECONDS
        deadline_owns_timeout = False
        if deadline is not None:
            cap = deadline - _clock.monotonic()
            if cap <= 0:
                raise _wait_timed_out()
            # Read slack belongs to the long-poll slice, never beyond the
            # caller's deadline; the wire timeout_ms is unchanged.
            if request_timeout is None or request_timeout == 0 or cap <= request_timeout:
                request_timeout = cap
                deadline_owns_timeout = True
        try:
            payload = await api.send(
                RequestSpec(
                    "GET",
                    f"/sandboxes/{quote(self.id, safe='')}",
                    timeout=request_timeout,
                    params=params,
                )
            )
        except ClientTimeoutError as error:
            # An earlier slice or transport timer, and a server 408, stay request
            # failures; only expiry of this wait's own cap is the wait timing out.
            if deadline_owns_timeout and deadline is not None and _clock.monotonic() >= deadline - 0.001:
                raise _wait_timed_out() from error
            raise
        fresh = AsyncSandbox._from_api(
            sandbox_detail_wire(payload),
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
            toolbox_url_from_api=True,
        )
        self._info = fresh._info
        if (
            self._direct_toolbox_url is None
            and fresh._toolbox_proxy_url
            and fresh._toolbox_proxy_url != self._toolbox_proxy_url
        ):
            if self._toolbox is not None:
                await self._toolbox.close()
            self._toolbox_proxy_url = fresh._toolbox_proxy_url
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

    async def _wait_for_state(
        self,
        states: Sequence[SandboxWaitState],
        terminal_states: Sequence[SandboxState] = DEFAULT_SANDBOX_TERMINAL_STATES,
        *,
        timeout: Optional[float] = None,
        missing_is_destroyed: bool = False,
    ) -> None:
        """Wait until the sandbox reaches one of ``states`` (the engine behind :meth:`wait`).

        ``timeout`` is the caller wait budget in seconds; ``None`` and ``0``
        wait unbounded. If the sandbox reaches a terminal state before any
        target state, a typed conflict error is raised with the observed public
        sandbox state attached as ``error.state``. A terminal state the events
        hub reports is confirmed with one read while the wait has budget left,
        unless that state already came from a full read or is ``destroyed``;
        the read decides: a target state returns, another terminal state
        raises, and any other state means the report was stale, so the wait
        polls on under the same deadline. Without that read, or when it fails,
        the reported state raises. The events hub gets the budget less a final
        reserve (``WAIT_FINAL_READ_RESERVE_S``, at most a quarter of it); when
        it has decided nothing by then, the reserve is a long-poll read of the
        server, so a state the stream never reported still ends the wait.
        ``missing_is_destroyed`` (delete) reads a 404 as ``destroyed``.
        """
        if not states:
            raise create_error("wait requires at least one target state", 400, "BAD_REQUEST", "SDK")

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
            hub_budget = _hub_wait_budget(deadline)
            # Where the hub's share ends: its probes hold no long poll past it,
            # so none still runs when the reserve starts reading.
            hub_deadline = None if hub_budget is None else _clock.monotonic() + hub_budget

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
                held = (
                    remaining
                    if hub_deadline is None
                    else min(remaining or 0.0, hub_deadline - _clock.monotonic())
                )
                ms = (
                    WAIT_FOR_STATE_SLICE_MS
                    if held is None
                    else min(WAIT_FOR_STATE_SLICE_MS, max(1, math.ceil(held * 1000)))
                )
                await self._refresh_data_within(
                    timeout=None if remaining is None else ms / 1000,
                    wait=wait_target,
                    wait_timeout_ms=ms if wait_target is not None else None,
                    # The read itself (its slack included) ends with the hub's share.
                    deadline=deadline if hub_deadline is None else hub_deadline,
                    exact=exact,
                )
                last_poll_end = _clock.monotonic()
                refreshed_by_poll = True
                return WaitOutcome(self.state, self.raw_state or "")

            def _observe_outcome(outcome: WaitOutcome) -> None:
                # A frame without a status leaves the wire status unknown (None, never "").
                self._update(state=outcome.state, raw_state=outcome.raw_state or None)

            def _on_observed(outcome: WaitOutcome) -> None:
                nonlocal refreshed_by_poll
                refreshed_by_poll = False
                _observe_outcome(outcome)

            try:
                resolved = await hub.wait(
                    self.id,
                    predicate,
                    timeout=hub_budget,
                    # `local` names no concrete region.
                    region_hint=None if (self.region or "").strip().lower() == "local" else self.region,
                    sandbox_poll_fn=_sandbox_poll_fn,
                    on_observed=_on_observed,
                )
            except RequestTimeoutError as error:
                # The events stream decided nothing within its share of the
                # budget (a stream that sends no transitions decides only at
                # its reconcile). The reserve left is read from the server,
                # under the same deadline, so a wait never runs out on the
                # stream alone. A server's 408 is not this wait running out.
                if deadline is None or error.source != "SDK":
                    raise
                try:
                    await self._poll_for_state(
                        target_states,
                        effective_terminal,
                        wait_target,
                        deadline,
                        exact,
                        last_read_end=last_poll_end,
                    )
                except (SandboxTerminalStateError, NotFoundError, AuthenticationError, ForbiddenError):
                    # What the read decided (a 404 outside a delete: the
                    # sandbox is gone), or a credential the API refuses (final
                    # for the hub's own probe too).
                    raise
                except RequestTimeoutError as read_error:
                    if read_error.source == "SDK":
                        raise
                    raise error from read_error
                except DaytonaError as read_error:
                    # The wait ran out; a failed read in its reserve does not
                    # replace that outcome.
                    raise error from read_error
                return
            except SandboxTerminalStateError as error:
                # What the handle last read, before the report overwrites it.
                seen = (self.state, self.raw_state, self.error)
                self._update(state=error.state)
                if error.raw_state is not None:
                    self._update(raw_state=error.raw_state)
                # A terminal state the hub reports may be one it held before
                # this request's frames arrived (an `error` from an earlier
                # wait, while a DELETE already moves the sandbox on): one read
                # confirms it, and the read decides. No read is possible once
                # the budget is spent, when the state already came from a full
                # read, or for `destroyed` (the GET would 404).
                remaining = None if deadline is None else deadline - _clock.monotonic()
                if (
                    refreshed_by_poll
                    or error.state == "destroyed"
                    or (remaining is not None and remaining <= 0)
                ):
                    raise _terminal_error_with_reason(
                        self.id, error, seen if refreshed_by_poll else None
                    ) from None
                try:
                    await self._refresh_data_within(timeout=remaining, deadline=deadline)
                except NotFoundError:
                    if not missing_is_destroyed:
                        raise error from None
                    # A sandbox the API no longer finds is gone.
                    self._update(state="destroyed", raw_state="deleted")
                except DaytonaError:
                    # The terminal outcome takes precedence over a failed read.
                    raise error from None
                if self.state in target_states:
                    return
                if self.state in effective_terminal:
                    raise _sandbox_terminal_state_error(
                        self.id, self.state, raw_state=self.raw_state or None, reason=self.error
                    ) from None
                # Neither a target nor terminal: the hub's report was stale.
                # Poll for the rest of the budget, under the same deadline.
            else:
                _observe_outcome(resolved)
                # A socket frame carries only the state. Callers (create, start,
                # resume) hand back this handle, so fill in the rest — toolbox URL,
                # region, resources, error reason — with one GET, as the polling
                # path does, and within the same budget. A destroyed sandbox has
                # nothing to fill in (the GET would be a 404). The target was
                # observed, so neither a spent budget nor a failed refresh turns
                # that success into an error.
                if not refreshed_by_poll and resolved.state != "destroyed":
                    remaining = None if deadline is None else deadline - _clock.monotonic()
                    if remaining is None or remaining > 0:
                        with contextlib.suppress(DaytonaError):
                            await self._refresh_data_within(timeout=remaining, deadline=deadline)
                return

        await self._poll_for_state(target_states, effective_terminal, wait_target, deadline, exact)

    async def _poll_for_state(
        self,
        target_states: frozenset[SandboxWaitState],
        effective_terminal: frozenset[SandboxState],
        wait_target: SandboxWaitState | None,
        deadline: Optional[float],
        exact: bool,
        *,
        last_read_end: Optional[float] = None,
    ) -> None:
        """The polling half of :meth:`_wait_for_state`: long-poll reads until a target or terminal state.

        ``last_read_end`` (a ``_clock.monotonic()`` instant) is when the wait's
        previous read ended; the first read keeps the minimum spacing after it.
        """
        if last_read_end is not None:
            pause = WAIT_FOR_STATE_MIN_INTERVAL_S - (_clock.monotonic() - last_read_end)
            # The spacing never spends what is left: with less than it, the
            # one read happens at once.
            if pause > 0 and (deadline is None or deadline - _clock.monotonic() > pause):
                await asyncio.sleep(pause)
        fresh = False
        while True:
            if fresh:
                if self.state in target_states:
                    return
                if self.state in effective_terminal:
                    raise _sandbox_terminal_state_error(
                        self.id, self.state, raw_state=self.raw_state or None, reason=self.error
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
            await self._refresh_data_within(
                timeout=None if remaining_timeout is None else wait_timeout_ms / 1000,
                wait=wait_target,
                wait_timeout_ms=wait_timeout_ms if wait_target is not None else None,
                deadline=deadline,
                exact=exact,
            )
            fresh = True
            if self.state in target_states:
                return
            if self.state in effective_terminal:
                raise _sandbox_terminal_state_error(
                    self.id, self.state, raw_state=self.raw_state or None, reason=self.error
                )
            elapsed = _clock.monotonic() - poll_started
            if elapsed < WAIT_FOR_STATE_MIN_INTERVAL_S:
                pause = WAIT_FOR_STATE_MIN_INTERVAL_S - elapsed
                if deadline is not None:
                    pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                if pause > 0:
                    await asyncio.sleep(pause)

    def _events_hub(self) -> Optional[WaitHub]:
        """The hub, attached on first use if the handle was created off-loop."""
        if self._hub_provider is not None:
            # Consult the owner even when another client keeps the shared hub alive.
            provided = self._hub_provider()
            if self._hub is None or getattr(self._hub, "_closed", False):
                self._hub = provided
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

    async def wait(
        self,
        *,
        until: SandboxWaitState | Sequence[SandboxWaitState] = "started",
        timeout: float = 60,
    ) -> Self:
        """Wait until the sandbox reaches ``until`` (one state or any of several) and return ``self``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a value
        above about 24.8 days, waits without a deadline). Running out raises :class:`RequestTimeoutError`;
        reaching ``destroying``, ``destroyed`` or ``error`` first raises
        :class:`SandboxTerminalStateError`. A terminal state the events hub
        reports is confirmed with one read before it raises, while the wait has
        budget left, unless that state already came from a full read or is
        ``destroyed``. When the events hub has reported nothing decisive, the
        last part of a bounded budget (up to 5 s) reads the state from the
        server, so a state the hub missed still ends the wait; the same holds
        for the other lifecycle waits and the create and fork waits.
        """
        timeout = _validate_timeout(timeout)
        await self._wait_for_state(_wait_states(until), timeout=timeout)
        return self

    async def start(self, *, timeout: float = 60) -> None:
        """Start a stopped sandbox, or resume a paused one, and wait until it is ``started``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a
        value above about 24.8 days, = no deadline); running out raises :class:`RequestTimeoutError`, and a
        terminal state raises :class:`SandboxTerminalStateError`. Whether the
        sandbox can be started is the API's decision: a refusal surfaces as its
        typed error.
        If a stop or pause is still in progress, wait for it to settle before
        sending start or resume, using the same timeout budget.

        A stopped macOS sandbox starts with its disk and size on any Mac of the
        organization; after a start on a different Mac, iCloud asks the user to
        sign in again. It raises :class:`ConflictError` with
        ``DISK_NOT_READY`` while its disk is not ready yet (retry later),
        ``NO_FREE_SLOT`` when no Mac of the organization has space for its
        size, ``MAC_DISK_FULL`` when the Mac with space is low on free disk
        space, and ``NO_ACTIVE_RESERVATION`` when the organization has no Mac
        reservation in the region and auto-reserve is off. When the
        organization has no Mac reservation in the region and auto-reserve is
        on, the start buys one, so it can fail as a create that buys one does: :class:`LimitExceededError` with
        ``limit == "max_mac_reservations"`` at the organization's Mac
        reservation limit, ``AUTO_RESERVE_LIMIT_REACHED``,
        ``NO_MAC_AVAILABLE``, ``INSUFFICIENT_BALANCE`` or ``CHARGE_FAILED``.
        """
        timeout = _validate_timeout(timeout)
        deadline = _wait_deadline(timeout)
        await self.refresh_data()
        if self.state in ("stopping", "pausing"):
            # The in-flight operation must finish before start or resume.
            if deadline is not None:
                timeout = deadline - _clock.monotonic()
                if timeout <= 0:
                    raise _wait_timed_out()
            await self._wait_for_state(["started", "paused", "stopped"], timeout=timeout)
            if deadline is not None:
                timeout = deadline - _clock.monotonic()
                if timeout <= 0:
                    raise _wait_timed_out()
        if self.state == "started":
            return
        terminal_states: Sequence[SandboxState] = DEFAULT_SANDBOX_TERMINAL_STATES
        if self.state == "paused":
            await self._lifecycle_mutation(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/resume")
            )
            terminal_states = _RESUME_TERMINAL_STATES
        elif self.state == "stopped":
            await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/start"))
        await self._wait_for_state(["started"], terminal_states, timeout=timeout)

    async def stop(self, *, timeout: float = 60) -> None:
        """Stop the sandbox and wait until it is ``stopped``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a
        value above about 24.8 days, = no deadline); running out raises :class:`RequestTimeoutError`, and a
        terminal state raises :class:`SandboxTerminalStateError`. Which
        sandboxes can stop (today: persistent macOS sandboxes) is the API's
        decision; a refusal surfaces as its typed error. While it is stopped,
        toolbox calls (process, files, git, …) and its preview URLs answer
        :class:`SandboxNotRunningError` (409, ``SANDBOX_NOT_RUNNING``);
        :meth:`start` it first.
        """
        timeout = _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/stop"))
        await self._wait_for_state(["stopped"], timeout=timeout)

    async def pause(self, *, timeout: float = 60) -> None:
        """Pause the sandbox with its memory and wait until it is ``paused``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a
        value above about 24.8 days, = no deadline). A pause that fails returns the sandbox to ``started``; this
        then raises :class:`SandboxTerminalStateError`. A paused macOS sandbox
        keeps its memory on its Mac. With auto-renew on, it keeps its
        reservation renewing; otherwise the sandbox is stopped, with its disk,
        before the reservation ends. Which sandboxes can pause is the API's
        decision.
        """
        timeout = _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/pause"))
        await self._wait_for_state(["paused"], _PAUSE_TERMINAL_STATES, timeout=timeout)

    async def resume(self, *, timeout: float = 60) -> None:
        """Resume a paused sandbox and wait until it is ``started``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a
        value above about 24.8 days, = no deadline). A resume that cannot finish ends ``stopped``; this then
        raises :class:`SandboxTerminalStateError`.
        """
        timeout = _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/resume"))
        await self._wait_for_state(["started"], _RESUME_TERMINAL_STATES, timeout=timeout)

    async def fork(
        self,
        *,
        name: Optional[str] = None,
        memory: Optional[bool] = None,
        wait_seconds: float = 60,
    ) -> AsyncSandbox:
        """Fork this sandbox and return the fork's handle.

        By default the fork continues from the source's memory (running
        processes included). ``memory=False`` copies only the disk, and the
        fork boots fresh. A macOS fork copies the disk only: omit ``memory``
        or pass ``memory=False`` (``memory=True`` is refused with
        ``NOT_SUPPORTED_FOR_CLASS``); a fork of a running macOS sandbox boots
        with a fresh machine identity. A macOS fork answers ``DISK_NOT_READY``
        while the source's disk is not ready yet; retry later.

        ``wait_seconds`` (default 60; ``0`` returns as soon as the API accepts
        the fork; a value above about 24.8 days, the timer maximum, waits with
        no deadline) bounds the wait for the state the fork settles in: a fork of
        a running source waits for ``started``, even if a macOS fork is
        temporarily ``stopped`` while its source releases the disk. A fork of
        a paused or stopped macOS source waits for ``stopped``. Linux forks
        otherwise rest ``paused`` for a memory fork of a paused source, else
        ``stopped`` (call ``start()``). Like ``create()``, ``fork()`` never raises
        because of how the wait ended: the default 60-second budget may return
        a not-yet-started handle. When the budget runs out or the fork fails,
        it returns the handle — check ``fork.state``.
        """
        budget = _validate_wait_seconds(wait_seconds)
        # None: no deadline (above the timer maximum); 0.0: one request.
        waits = budget is None or budget > 0
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise create_error("fork name must be a non-empty string when given", 400, "BAD_REQUEST", "SDK")
        if memory is not None and not isinstance(memory, bool):
            raise create_error("fork memory must be a boolean", 400, "BAD_REQUEST", "SDK")
        class_known = self.sandbox_class is not None
        # wait_seconds=0 is one request, as for create: the route is the same
        # for every class, so an unknown class is not read first; the fork then
        # carries the source's class only when this handle knows it.
        sandbox_class: Optional[str] = (
            await self._require_sandbox_class() if waits else (self.raw_sandbox_class or self.sandbox_class)
        )
        targets: list[SandboxWaitState] = ["started"]
        if waits:
            # Refresh once before the POST; loading the class may already have
            # read the source. A running macOS fork's stopped state is interim.
            # The target comes from this read: if the source stops between it
            # and the POST, a macOS fork rests stopped while the wait looks for
            # started, and fork() returns the handle at the end of the budget,
            # not at once. Callers check fork.state.
            if class_known:
                await self.refresh_data()
            if self.state == "started":
                targets = ["started"]
            elif sandbox_class == "linux" and self.state == "paused" and memory is not False:
                targets = ["paused"]
            else:
                targets = ["stopped"]
        body: JsonObject = {}
        if name is not None:
            body["name"] = name
        if memory is not None:
            body["memory"] = memory
        endpoints: list[str] = []

        def _capture_endpoint(headers: Mapping[str, str]) -> None:
            endpoint = endpoint_from_headers(headers)
            if endpoint:
                endpoints.append(endpoint)

        data = expect_fork_resp(
            await self._require_api().send(
                RequestSpec(
                    "POST",
                    f"/sandboxes/{quote(self.id, safe='')}/fork",
                    json_body=body,
                    on_headers=_capture_endpoint,
                )
            )
        )
        fork_id = data.id
        api_token = getattr(self._api, "token", None) if self._api else None
        # The fork's detail read counts against the wait budget; with no
        # deadline it is an ordinary request, bounded by the request timeout.
        deadline = _clock.monotonic() + budget if budget is not None and budget > 0 else None
        detail: Optional[Mapping[str, JsonValue]] = None
        # wait_seconds=0 is one request, as for create: no detail read, and a
        # zero budget never becomes an unbounded read.
        if waits:
            try:
                with anyio.move_on_after(
                    math.inf if deadline is None else max(0.0, deadline - _clock.monotonic())
                ):
                    detail = sandbox_detail_wire(
                        await self._require_api().send(
                            RequestSpec("GET", f"/sandboxes/{quote(fork_id, safe='')}")
                        )
                    )
            except DaytonaError:
                # The fork exists; a failed read never costs the caller its handle.
                detail = None
        accepted = data.to_dict()
        forked_from = accepted.get("forked_from") or self.id
        if detail is not None and not detail.get("forked_from"):
            # A detail without `forked_from` keeps the source's id the fork names.
            detail = {**detail, "forked_from": forked_from}
        # The fork gets its own toolbox URL from the API; inheriting the
        # parent's override could send its file and process calls to the parent.
        fork = AsyncSandbox._from_api(
            detail
            if detail is not None
            else {
                "id": fork_id,
                "status": accepted.get("status"),
                "forked_from": forked_from,
                **({"class": sandbox_class} if sandbox_class is not None else {}),
            },
            self._api,
            None,
            toolbox_owner=self._toolbox_owner,
            toolbox_base_url=data.toolbox_proxy_url or None,
            toolbox_token=api_token,
            request_timeout=self._request_timeout,
            # The events hub IS inherited: it is per process and identity, not
            # per sandbox (unlike the toolbox override above).
            hub=self._events_hub(),
            hub_provider=self._hub_provider,
            toolbox_url_from_api=True,
        )
        if detail is None:
            # The fork response carries no labels: unknown until a detail read.
            fork._update(labels=None)
        for endpoint in endpoints:
            # Source and fork share the regional API endpoint that accepted the fork.
            self._learn_endpoint(endpoint)
            fork._learn_endpoint(endpoint)
        if detail is None or not waits or fork.state in targets:
            return fork
        if deadline is None:
            # No deadline: the state wait's 0 is no deadline.
            await _wait_without_raising_for_its_end(fork, targets, 0)
            return fork
        remaining = deadline - _clock.monotonic()
        if remaining > 0:
            await _wait_without_raising_for_its_end(fork, targets, remaining)
        return fork

    async def delete(self, *, timeout: float = 60) -> None:
        """Delete the sandbox and wait until it is ``destroyed``.

        ``timeout`` is the wait budget in seconds (default 60; ``0``, or a
        value above about 24.8 days, = no deadline); running out raises :class:`RequestTimeoutError`, and an
        ``error`` state raises :class:`SandboxTerminalStateError` (one the
        events hub reports is confirmed with one read first, while the wait
        has budget left and unless it already came from a full read). A 404 while
        waiting counts as destroyed. A destroyed sandbox does not guarantee
        that its image can already be deleted: image deletion may still
        answer 409 for a while.
        """
        timeout = _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("DELETE", f"/sandboxes/{quote(self.id, safe='')}"))
        try:
            await self._wait_for_state(["destroyed"], timeout=timeout, missing_is_destroyed=True)
        except NotFoundError:
            # A sandbox the API no longer finds is gone: the wire status of `destroyed`.
            self._update(state="destroyed", raw_state="deleted")

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
        ``delete:sandboxes`` in addition to ``write:sandboxes``. A sandbox that
        is deleted or being deleted raises :class:`ConflictError` (``CONFLICT``).
        """
        requested: LifecyclePolicyOptions = {}
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
        # One read when the class is unknown, and for a macOS sandbox whose
        # persistence is unknown, as TypeScript reads it; never for a known
        # Linux sandbox (persistence is macOS-only).
        sandbox_class = await self._require_sandbox_class()
        if sandbox_class == "macos" and self.persistent is None:
            await self.refresh_data()
        assert_lifecycle_class_support(
            body,
            sandbox_class=sandbox_class,
            persistent=True if self.persistent is None else self.persistent,
            action="update_lifecycle_policy",
        )
        data = expect_sandbox_lifecycle_policy(
            await self._require_api().send(
                RequestSpec(
                    "PATCH",
                    f"/sandboxes/{quote(self.id, safe='')}/lifecycle-policy",
                    json_body=body,
                    on_headers=self._learn_endpoint_from_headers,
                )
            )
        )
        policy = parse_lifecycle(data)
        self._update_lifecycle(policy, data)
        return policy

    async def reset_idle_timer(self) -> SandboxLifecycle:
        """Reset this sandbox's idle timer (an explicit out-of-band keepalive).

        For work the platform cannot see: bumps the sandbox's activity clock
        (``last_activity_at``, attributed
        server-side to the ``api`` source), which restarts the idle countdown —
        ``idle_timeout_at`` re-anchors to now + ``idle_expiry_seconds``. The
        wall-clock ``timeout_at`` is NOT affected. Succeeds whether or not an
        idle timeout is armed (an unarmed sandbox echoes
        ``idle_timeout_at=None``), and in any state but deleting or deleted —
        a never-started sandbox already carries its creation-time activity
        stamp, which the reset advances. A sandbox that is deleted or being
        deleted raises :class:`ConflictError` (``CONFLICT``).

        The echo is the full post-reset lifecycle, the same shape
        :meth:`update_lifecycle_policy` returns. Advance-only: a newer concurrent
        activity bump keeps the clock, and what comes back is the state that
        won.
        """
        data = expect_sandbox_lifecycle_policy(
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
            self._update_lifecycle(fresh, data)
        return fresh

    async def get_network_policy(self) -> NetworkPolicyInfo:
        response = await self._require_api().get(f"/sandboxes/{quote(self.id, safe='')}/network-policy")
        return parse_network_policy_response(response)

    async def update_network_policy(
        self,
        *,
        network_policy: NetworkPolicyInput,
        if_match: str,
    ) -> NetworkPolicyInfo:
        _require_if_match(if_match)
        response = await self._require_api().put(
            f"/sandboxes/{quote(self.id, safe='')}/network-policy",
            json=serialize_network_policy_input(network_policy),
            headers={"If-Match": if_match},
        )
        return parse_network_policy_response(response)

    async def reset_network_policy(self, *, if_match: str) -> NetworkPolicyInfo:
        _require_if_match(if_match)
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
        _require_if_match(if_match)
        response = await self._require_api().put(
            f"/sandboxes/{quote(self.id, safe='')}/secrets",
            json=serialize_sandbox_secrets_input(secrets),
            headers={"If-Match": if_match},
        )
        return parse_sandbox_secrets_response(response)

    async def capture_image(
        self, *, name: Optional[str] = None, idle_expiry_seconds: Optional[int] = None
    ) -> Image:
        """Capture this sandbox's filesystem as a new :class:`Image` (``POST /sandboxes/{id}/images``).

        The result boots new sandboxes (``daytona.create(image.id)``); a Linux
        sandbox keeps running. A macOS
        sandbox must be persistent and stopped; the capture answers
        ``DISK_NOT_READY`` while its disk is not ready yet (retry later).
        The credential needs ``write:sandboxes`` in addition to ``write:images``;
        without it the capture raises :class:`ForbiddenError` (403).

        ``name`` makes the image named and permanent (same name rules as
        ``images.build``; a name that already exists raises
        :class:`ImageNameTakenError`). ``idle_expiry_seconds`` is
        the idle-retention window of an ANONYMOUS capture: it is deleted once
        that long has passed without a sandbox being created from it (every
        create restarts the clock). Same rule as ``images.build``: omitted or
        ``-1`` ⇒ the server maximum (7 days by default); above it ⇒ 400.
        Promote the image to keep it. ``idle_expiry_seconds`` with ``name`` is
        a 400. Returns once the API accepts the capture; wait for readiness
        with ``images.get(image.id, wait="ready")``.
        """
        body: JsonObject = {}
        body.update(image_options_body(name=name, idle_expiry_seconds=idle_expiry_seconds))
        operation = expect_create_image_resp(
            await self._require_api().send(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/images", json_body=body)
            )
        )
        return expect_image(
            await self._require_api().send(
                RequestSpec("GET", f"/images/{path_segment(operation.id, 'image id')}")
            )
        )

    async def create_snapshot(self, name: str, /) -> Snapshot:
        """Capture this sandbox, memory and disk, as a new :class:`Snapshot`
        (``POST /sandboxes/{id}/snapshots``).

        The credential needs ``write:sandboxes`` in addition to
        ``write:snapshots``; without it the capture raises
        :class:`ForbiddenError` (403).
        """
        if not isinstance(name, str) or not name.strip():
            raise create_error("name must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        created = expect_create_snapshot_resp(
            await self._require_api().send(
                RequestSpec(
                    "POST", f"/sandboxes/{quote(self.id, safe='')}/snapshots", json_body={"name": name}
                )
            )
        )
        # The detail is checked against the contract before it is mapped, as images are.
        return parse_snapshot(
            expect_snapshot(
                await self._require_api().send(
                    RequestSpec("GET", f"/snapshots/{path_segment(created.id, 'snapshot id')}")
                )
            )
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
        on_logs: BuildLogsCallback,
        on_event: Optional[BuildLogEventCallback] = None,
        cursor: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BuildLogFollowResult:
        """Observe this sandbox's build without changing its execution lifetime.

        ``on_logs`` receives decoded output; optional ``on_event`` receives each
        typed ``BuildLogEvent``. Either callback may return an awaitable, which
        is awaited before the next event. ``timeout`` (seconds) bounds this
        observation only; cancelling the awaiting task closes the stream, not
        the build. An HTTP error of the initial discovery request, including a
        404 for a sandbox with no image build, raises its typed error
        (``NotFoundError``, ...) unchanged; once the follower is running, a
        failure raises ``BuildLogInterruptedError`` with the last cursor. It
        does not fall back to another log source.
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

    async def _require_sandbox_class(self) -> SandboxClass:
        """The class every class-gated call checks.

        A class this SDK does not know is refused here rather than checked as
        ``linux``.
        """
        if self.sandbox_class is None:
            await self.refresh_data()
        sandbox_class = self.sandbox_class
        if sandbox_class == "linux" or sandbox_class == "macos":
            return sandbox_class
        raise create_error(
            f"unsupported sandbox class {self.raw_sandbox_class}", 400, "UNSUPPORTED_OPERATION", "SDK"
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
        ``[]`` is a valid ports-only grant. ``toolbox:git`` without
        ``toolbox:exec`` covers the git operations except writing git config:
        ``git.set_config()`` with such a token raises :class:`ForbiddenError`
        (403); ``git.get_config()`` still works. ``ports`` names the private-preview
        ports the token may reach (omitted = all, ``[]`` = none); the sandbox
        daemon's ports 2280, 2281 and 22220 are never preview ports and are
        refused with :class:`~daytona.v2.errors.BadRequestError` before any
        request. ``ttl_seconds``
        overrides the deployment's default lifetime, capped server-side; an
        unknown operation or an over-cap TTL is a 400, never a silent
        narrowing. The token cannot be refreshed and cannot mint further
        credentials — mint a fresh one here instead.
        """
        body: JsonObject = {}
        if operations is not None:
            if (
                isinstance(operations, (str, bytes))
                or not isinstance(operations, Sequence)
                or not all(isinstance(operation, str) for operation in operations)
            ):
                raise create_error("operations must be a list of strings", 400, "BAD_REQUEST", "SDK")
            body["operations"] = list(operations)
        if ports is not None:
            if isinstance(ports, (str, bytes)) or not isinstance(ports, Sequence):
                raise create_error("ports must be a list of port numbers", 400, "BAD_REQUEST", "SDK")
            body["ports"] = [preview_port(port) for port in ports]
        if ttl_seconds is not None:
            if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
                raise create_error("ttl_seconds must be a positive integer", 400, "BAD_REQUEST", "SDK")
            body["ttl_seconds"] = ttl_seconds
        return _sandbox_access_token_from_payload(await self._post_access_token(body))

    async def _post_access_token(self, body: JsonObject) -> TransportPayload:
        return await self._require_api().send(
            RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/access-token", json_body=body)
        )

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
            if _mint_route_unavailable(exc):
                self._sandbox_token_mint_unsupported = _mint_route_unsupported(exc)
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
            if _mint_route_unavailable(exc):
                self._sandbox_token_mint_unsupported = _mint_route_unsupported(exc)
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
            self._access_token = None
            self._access_token_expires_at = None
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
        # A malformed expiry reads as absent here (see `_access_token_parts`).
        access_token, _, expires_at, _, _ = _access_token_parts(
            await self._post_access_token({}), expiry_required=False
        )
        self._access_token = access_token
        self._access_token_expires_at = expires_at
        return access_token

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
            if _mint_route_unavailable(exc):
                self._sandbox_token_mint_unsupported = _mint_route_unsupported(exc)
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


async def _wait_without_raising_for_its_end(
    sandbox: AsyncSandbox, states: Sequence[SandboxWaitState], timeout: float
) -> None:
    """Wait for ``states``; however the wait ends, the handle is kept.

    The create and fork contract: once the create or fork request succeeded,
    the sandbox exists, so a spent budget, a terminal state or a failed read
    during the wait (any :class:`DaytonaError`) is not an error. How the wait
    ended is the caller's to read from ``sandbox.state``. Cancellation still
    propagates.
    """
    with contextlib.suppress(DaytonaError):
        await sandbox._wait_for_state(states, timeout=timeout)


def _json_number(number: float) -> Union[int, float]:
    return int(number) if float(number).is_integer() else number


def _artifact_json(artifact_id: str, name: Optional[str]) -> ArtifactRefJSON:
    return {"id": artifact_id} if name is None else {"id": artifact_id, "name": name}


def sandbox_info_from_id(sandbox_id: str) -> _SandboxInfo:
    return _SandboxInfo(id=sandbox_id)


def _sandbox_info_from_wire(data: Mapping[str, JsonValue]) -> _SandboxInfo:
    """Handle metadata from a sandbox API object (detail, list or search item)."""
    sandbox_id = str(data.get("id"))
    raw_status = _wire_text(data.get("status")) or _wire_text(data.get("state")) or None
    raw_class = _wire_text(data.get("class")) or _wire_text(data.get("var_class"))
    region = _wire_text(data.get("region_id")) or None
    lifecycle = data.get("lifecycle_policy")
    persistent = data.get("persistent")
    spot = data.get("spot")
    return _SandboxInfo(
        id=sandbox_id,
        name=_wire_text(data.get("name")),
        state=sandbox_state(raw_status),
        raw_state=raw_status,
        image=_image_ref(data.get("image")),
        snapshot=_snapshot_ref(data.get("snapshot")),
        image_build_id=_wire_text(data.get("image_build_id")),
        region=region,
        labels=_sandbox_labels(data.get("labels")),
        cpu=_optional_number(data.get("cpu")),
        cpu_max=_optional_number(data.get("cores_max")),
        memory=mib_to_gib(data.get("memory_mib")),
        memory_max=mib_to_gib(data.get("memory_max_mib")),
        disk=mib_to_gib(data.get("disk_mib")),
        disk_max=mib_to_gib(data.get("disk_max_mib")),
        # Every API sandbox object carries `class` (validated at the boundary);
        # partial internal data leaves it unknown until it is read.
        sandbox_class=_reported_class(raw_class) if raw_class is not None else None,
        raw_sandbox_class=raw_class,
        mac_boot_mode=_wire_text(data.get("mac_boot_mode")),
        persistent=persistent if isinstance(persistent, bool) else None,
        spot=spot if isinstance(spot, bool) else None,
        spot_evicted_at=parse_timestamp(data.get("spot_evicted_at")),
        lifecycle_policy=parse_lifecycle(lifecycle) if isinstance(lifecycle, dict) else None,
        forked_from=_wire_text(data.get("forked_from")),
        reservation_id=_wire_text(data.get("reservation_id")),
        user=_wire_text(data.get("user")),
        error=_wire_text(data.get("error")),
        wire_timestamps={
            **({"spotEvictedAt": evicted} if isinstance(evicted := data.get("spot_evicted_at"), str) else {}),
            **(_lifecycle_wire_timestamps(lifecycle) if isinstance(lifecycle, dict) else {}),
        },
    )


_LIFECYCLE_TIMESTAMPS = (
    ("timeoutAt", "timeout_at"),
    ("idleTimeoutAt", "idle_timeout_at"),
    ("lastActivityAt", "last_activity_at"),
)


def _lifecycle_wire_timestamps(data: Mapping[str, JsonValue]) -> Dict[str, str]:
    """The lifecycle policy's timestamp strings as the server sent them."""
    nested = data.get("lifecycle_policy")
    policy = nested if isinstance(nested, dict) else data
    return {key: value for key, wire in _LIFECYCLE_TIMESTAMPS if isinstance(value := policy.get(wire), str)}


def _wire_text(value: object) -> Optional[str]:
    if isinstance(value, Enum):
        return enum_str(value)
    return value if isinstance(value, str) else None


def _image_ref(value: object) -> Optional[ImageRef]:
    ref = artifact_ref_from_wire(value)
    return ImageRef(id=ref.id, name=ref.name) if ref is not None else None


def _snapshot_ref(value: object) -> Optional[SnapshotRef]:
    ref = artifact_ref_from_wire(value)
    return SnapshotRef(id=ref.id, name=ref.name) if ref is not None else None


def _reported_class(raw: str) -> Literal["linux", "macos", "unknown"]:
    if raw == "linux" or raw == "macos":
        return raw
    return "unknown"


def _parse_activity_clock(value: Optional[datetime]) -> Optional[datetime]:
    # Aware UTC: comparing a naive and an aware datetime raises TypeError, which
    # would turn an already-applied reset into an SDK error.
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _sandbox_toolbox_base(sandbox_data: Mapping[str, JsonValue]) -> Optional[str]:
    value = sandbox_data.get("toolbox_proxy_url") or sandbox_data.get("toolboxProxyUrl")
    return str(value).rstrip("/") if isinstance(value, str) and value else None


def _optional_number(value: object) -> Optional[float]:
    # The model check at the boundary refuses a non-finite number; this
    # conversion never raises OverflowError either way.
    return float(value) if finite_number(value) else None


def _sandbox_labels(value: object) -> Dict[str, str]:
    """A sandbox object's labels: absent or null is none; anything but a map of
    strings is a malformed response naming the label, as in TypeScript."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise create_error("sandbox response has a labels that is not an object", 400, "BAD_REQUEST", "SDK")
    for key, label in value.items():
        if not isinstance(label, str):
            raise create_error(
                f"sandbox response has a labels.{key} that is not a string", 400, "BAD_REQUEST", "SDK"
            )
    return dict(value)


def _response_labels(value: object) -> Dict[str, str]:
    def malformed(detail: str) -> DaytonaError:
        return create_error(f"sandbox labels response {detail}", 400, "BAD_REQUEST", "SDK")

    if value is None:
        raise malformed("is missing its labels")
    if not isinstance(value, dict):
        raise malformed("has a labels that is not an object")
    for key, label in value.items():
        if not isinstance(label, str):
            raise malformed(f"has a labels.{key} that is not a string")
    return dict(value)


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
    check_label_keys(validated)
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


def _sandbox_lifecycle_handle_json(value: SandboxLifecycle, wire: Mapping[str, str]) -> SandboxLifecycleJSON:
    payload: SandboxLifecycleJSON = {}
    if value.timeout_seconds is not None:
        payload["timeoutSeconds"] = value.timeout_seconds
    if value.on_timeout is not None:
        payload["onTimeout"] = value.on_timeout
    if value.timeout_at is not None:
        payload["timeoutAt"] = wire.get("timeoutAt") or rfc3339(value.timeout_at)
    if value.idle_expiry_seconds is not None:
        payload["idleExpirySeconds"] = value.idle_expiry_seconds
    if value.on_idle_timeout is not None:
        payload["onIdleTimeout"] = value.on_idle_timeout
    if value.idle_timeout_at is not None:
        payload["idleTimeoutAt"] = wire.get("idleTimeoutAt") or rfc3339(value.idle_timeout_at)
    if value.last_activity_at is not None:
        payload["lastActivityAt"] = wire.get("lastActivityAt") or rfc3339(value.last_activity_at)
    if value.last_activity_source is not None:
        payload["lastActivitySource"] = value.last_activity_source
    return payload


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


def _validate_timeout(timeout: float) -> float:
    """The checked wait budget: ``0.0`` (no deadline) for ``0`` or a value
    above the timer maximum, an integer too large for a float included."""
    return wait_budget_seconds(timeout)


_WAIT_STATES: Final[Mapping[str, SandboxWaitState]] = {state: state for state in get_args(SandboxWaitState)}


def _wait_states(until: object) -> list[SandboxWaitState]:
    """``until`` as a checked list: every member a wait state, at least one (as in TypeScript)."""
    if isinstance(until, str):
        members: list[object] = [until]
    elif isinstance(until, (list, tuple)):
        members = list(until)
    else:
        raise create_error("until must be a sandbox wait state or a list of them", 400, "BAD_REQUEST", "SDK")
    if not members:
        raise create_error("wait requires at least one target state", 400, "BAD_REQUEST", "SDK")
    states: list[SandboxWaitState] = []
    for member in members:
        if not isinstance(member, str) or member not in _WAIT_STATES:
            raise create_error(f"invalid sandbox wait state: {member}", 400, "BAD_REQUEST", "SDK")
        states.append(_WAIT_STATES[member])
    return states


def _validate_wait_seconds(wait_seconds: float) -> Optional[float]:
    """The checked fork budget: ``0.0`` returns once the fork is accepted, and
    ``None`` (a value above the timer maximum, about 24.8 days) waits with no
    deadline, as every wait budget in both SDKs does."""
    if wait_seconds == 0 and not isinstance(wait_seconds, bool):
        return 0.0
    budget = wait_budget_seconds(wait_seconds, name="wait_seconds")
    return None if budget == 0 else budget


def _wait_deadline(timeout: Optional[float]) -> Optional[float]:
    if timeout is None:
        return None
    budget = _validate_timeout(timeout)
    return None if budget == 0 else _clock.monotonic() + budget


def _hub_wait_budget(deadline: Optional[float]) -> Optional[float]:
    """The events hub's share of a wait ending at ``deadline``: all but the final-read reserve."""
    if deadline is None:
        return None
    remaining = deadline - _clock.monotonic()
    if remaining <= 0:
        # The hub reads 0 as no deadline: a spent budget is a timeout here.
        raise _wait_timed_out()
    return remaining - min(WAIT_FINAL_READ_RESERVE_S, remaining / 4)


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


def _terminal_error_with_reason(
    sandbox_id: str,
    error: SandboxTerminalStateError,
    seen: Optional[tuple[Optional[SandboxState], str, Optional[str]]],
) -> SandboxTerminalStateError:
    """The terminal error a hub report raises when no read confirms it.

    ``seen`` is the state, wire status and reason of the full read the
    reported state came from (``None`` when it came from a socket frame).
    When that read shows the reported state and wire status, name the
    server's explanation for it (why a deletion did not finish, say).
    `failed` and `lost` share the public `error`; matching the wire status
    too keeps one's explanation from labelling the other.
    """
    if seen is None or error.reason is not None:
        return error
    state, raw_state, reason = seen
    if not reason or state != error.state or (error.raw_state is not None and raw_state != error.raw_state):
        return error
    return SandboxTerminalStateError(
        sandbox_id, error.state, raw_state=error.raw_state or raw_state or None, reason=reason
    )


def _sandbox_terminal_state_error(
    sandbox_id: str, state: SandboxState, *, raw_state: Optional[str] = None, reason: Optional[str] = None
) -> Exception:
    return SandboxTerminalStateError(sandbox_id, state, raw_state=raw_state, reason=reason)


def _sandbox_access_token_from_payload(raw: object) -> SandboxAccessToken:
    access_token, token_type, expires_at, operations, ports = _access_token_parts(raw, expiry_required=True)
    if expires_at is None:  # expiry_required refuses it first; kept for the type
        raise create_error(
            "sandbox access token response is missing its expires_at", 400, "BAD_REQUEST", "SDK"
        )
    return SandboxAccessToken(
        access_token=access_token,
        token_type=token_type,
        expires_at=expires_at,
        operations=operations,
        ports=ports,
    )


def _access_token_parts(
    raw: object, *, expiry_required: bool
) -> Tuple[str, str, Optional[datetime], Tuple[str, ...], Optional[Tuple[int, ...]]]:
    """The contract's access token, every field checked; a violation names the
    field (TypeScript's ``checkResponse`` messages for the same body).

    ``expires_at`` is an RFC 3339 timestamp (``rfc3339_timestamp``). The
    handle's own mint passes ``expiry_required=False``: there a malformed
    expiry reads as absent, as a create response's token expiry does, and the
    token is used until a 401 re-mints it, so a working call is not failed
    over metadata it only uses for a proactive refresh.
    """

    def malformed(detail: str) -> DaytonaError:
        return create_error(f"sandbox access token response {detail}", 400, "BAD_REQUEST", "SDK")

    if not isinstance(raw, dict):
        raise malformed("is not a JSON object")
    payload: JsonObject = raw

    def required_str(field: str) -> str:
        value = payload.get(field)
        if value is None:
            raise malformed(f"is missing its {field}")
        if not isinstance(value, str):
            raise malformed(f"has a {field} that is not a string")
        return value

    access_token = required_str("access_token")
    if not access_token:
        raise malformed("has an empty access_token")
    token_type = required_str("token_type")
    # An expiry that does not parse would read as never expiring and skip the proactive refresh.
    raw_expires_at = required_str("expires_at")
    expires_at: Optional[datetime] = None
    if rfc3339_timestamp(raw_expires_at):
        expires_at = parse_timestamp(raw_expires_at)
    elif expiry_required:
        raise malformed("has an expires_at that is not an RFC 3339 timestamp")
    raw_operations = payload.get("operations")
    if raw_operations is None:
        raise malformed("is missing its operations")
    if not isinstance(raw_operations, list):
        raise malformed("has a operations that is not an array")
    for index, operation in enumerate(raw_operations):
        if not isinstance(operation, str):
            raise malformed(f"has a operations[{index}] that is not a string")
    raw_ports = payload.get("ports")
    if raw_ports is not None and not isinstance(raw_ports, list):
        raise malformed("has a ports that is not an array")
    for index, port in enumerate(raw_ports or ()):
        if not isinstance(port, int) or not finite_number(port):
            raise malformed(f"has a ports[{index}] that is not an integer")
    return (
        access_token,
        token_type,
        expires_at,
        tuple(raw_operations),
        tuple(raw_ports) if raw_ports is not None else None,
    )


def _rfc3339_epoch(value: Optional[datetime]) -> Optional[float]:
    return value.timestamp() if value is not None else None


def _wait_timed_out() -> DaytonaError:
    return create_error(
        "sandbox failed to reach the requested state before timeout", 408, "REQUEST_TIMEOUT", "SDK"
    )


def _require_if_match(if_match: object) -> None:
    if not isinstance(if_match, str) or not if_match:
        raise create_error(
            "if_match must be a non-empty string (the etag of the last read)", 400, "BAD_REQUEST", "SDK"
        )
    header_text(if_match, "if_match")


def _mint_route_unsupported(exc: Exception) -> bool:
    """A mint failure proving the access-token route is absent (405/501).

    The handle then keeps ``access_token or api key`` behavior for its
    lifetime instead of failing every sandbox service call.
    """
    return getattr(exc, "status_code", None) in (405, 501)


def _mint_route_unavailable(exc: Exception) -> bool:
    """A mint failure that falls back to the API credential for this request.

    A 404 is not proof the route is absent (the sandbox may be missing), so it
    falls back once without disabling minting; 405/501 also disable it.
    """
    return _mint_route_unsupported(exc) or getattr(exc, "status_code", None) == 404


def _isoformat_or_none(value: datetime | None) -> Optional[str]:
    if value is not None:
        return value.isoformat().replace("+00:00", "Z")
    return None
