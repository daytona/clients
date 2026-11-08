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
from enum import Enum
from types import MappingProxyType
from typing import Callable, ClassVar, Dict, Final, Literal, Mapping, Optional, Sequence
from urllib.parse import quote, urlsplit

import anyio
from typing_extensions import Self

from . import _clock
from ._api_core import (
    expect_create_image_resp,
    expect_create_snapshot_resp,
    expect_fork_resp,
    sandbox_detail_wire,
)
from ._auth import ProjectTokenAuth
from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    TransportPayload,
    _expect_model_from_dict,
    artifact_ref_from_wire,
    assert_lifecycle_class_support,
    enum_str,
    expect_object,
    lifecycle_body,
    mib_to_gib,
    parse_image,
    parse_lifecycle,
    parse_snapshot,
    parse_timestamp,
    rfc3339,
    sandbox_state,
)
from ._events import WaitHub, WaitOutcome, _TargetPredicate, endpoint_from_headers, region_of
from ._gen.api import (
    Sandbox as SandboxDto,
)
from ._gen.api import (
    SandboxListItem,
    SignSandboxFileUrlRequest,
)
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
    ClientTimeoutError,
    DaytonaError,
    NotFoundError,
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
    AsyncInterpreterContext,
    Image,
    ImageRef,
    InterpreterResult,
    JsonObject,
    JsonValue,
    KeepLogs,
    LifecycleAction,
    LifecyclePolicyOptions,
    MacBootMode,
    NetworkPolicyInfo,
    NetworkPolicyInput,
    ProcessResult,
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
    TerminalSize,
    _SandboxInfo,
)

CODE_TOOLBOX_LANGUAGE_LABEL = "code-toolbox-language"
_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE = (
    "pre-signed URLs require the API-provided toolbox URL or a sandbox-scoped URL template"
)
WAIT_FOR_STATE_SLICE_MS = 30_000
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
        # with a project key mints and refreshes sandbox access tokens
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

    def _update(self, **changes: object) -> None:
        """Replace observed metadata fields (internal: the SDK's own reads)."""
        if "lifecycle_policy" in changes and "wire_timestamps" not in changes:
            # A policy read without its wire strings must not keep the old ones.
            changes["wire_timestamps"] = {
                key: value for key, value in self._info.wire_timestamps.items() if key == "spotEvictedAt"
            }
        self._info = dataclasses.replace(self._info, **changes)  # type: ignore[arg-type]

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
    def raw_state(self) -> str:
        """Wire status string exactly as the server sent it (``""`` before any read).

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
    def labels(self) -> Mapping[str, str]:
        """The sandbox labels (a read-only view); replace them with :meth:`set_labels`."""
        return MappingProxyType(dict(self._info.labels))

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
            self._fs = AsyncFileSystemService(self._require_toolbox(), file_url_signer=self._sign_file_url)
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
        return _expect_model_from_dict(
            await self._require_toolbox().send(RequestSpec("GET", "/system/metrics")),
            SystemMetrics,
            "system metrics",
            SystemMetrics.from_dict,
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
        terminal: Optional[TerminalSize] = None,
        keep_logs: Optional[KeepLogs] = None,
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
        :returns: :class:`~daytona.v2.types.ProcessResult` with ``stdout``, ``stderr``,
            ``output``, ``exit_code``, and ``reason``.
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
            terminal=terminal,
            keep_logs=keep_logs,
        )

    async def run_code(
        self,
        code: str,
        *,
        language: str = "python",
        context: Optional[AsyncInterpreterContext] = None,
        isolated: bool = False,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> InterpreterResult:
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
            payload["image"] = {"id": info.image.id, "name": info.image.name}
        if info.snapshot is not None:
            payload["snapshot"] = {"id": info.snapshot.id, "name": info.snapshot.name}
        if info.region is not None:
            payload["region"] = info.region
        if info.labels:
            payload["labels"] = dict(info.labels)
        for key, number in (
            ("cpu", info.cpu),
            ("cpuMax", info.cpu_max),
            ("memory", info.memory),
            ("memoryMax", info.memory_max),
            ("disk", info.disk),
            ("diskMax", info.disk_max),
        ):
            if number is not None:
                # An integral size is written as an integer, as JavaScript's JSON does.
                payload[key] = int(number) if float(number).is_integer() else number  # type: ignore[literal-required]
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
        applied = _string_map_or_empty(response.get("labels"))
        self._update(labels=applied)
        return dict(applied)

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
    ) -> None:
        """Read the sandbox's metadata; with ``wait``, as one long-poll slice.

        ``deadline`` (a ``_clock.monotonic()`` instant) is the caller's wait
        deadline: the read never outlives it, and a read the deadline cut is the
        wait running out (:class:`RequestTimeoutError`, source ``SDK``), as in
        TypeScript's ``refreshDataWithin``.
        """
        api = self._require_api()
        params: Dict[str, JsonValue] = {}
        if wait is not None:
            params["wait"] = _wait_query_state(wait)
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
    ) -> None:
        """Wait until the sandbox reaches one of ``states`` (the engine behind :meth:`wait`).

        ``timeout`` is the caller wait budget in seconds; ``None`` and ``0``
        wait unbounded. If the sandbox reaches a terminal state before any
        target state, a typed conflict error is raised with the observed public
        sandbox state attached as ``error.state``.
        """
        if not states:
            raise create_error("wait requires at least one target state", 400, "BAD_REQUEST", "SDK")

        deadline = _wait_deadline(timeout)
        target_states = frozenset(states)
        terminal_state_set = frozenset(terminal_states)
        wait_target = states[0] if len(target_states) == 1 else None
        effective_terminal = terminal_state_set - ({"destroying"} if "destroyed" in target_states else set())

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
                await self._refresh_data_within(
                    timeout=None if remaining is None else ms / 1000,
                    wait=wait_target,
                    wait_timeout_ms=ms if wait_target is not None else None,
                    deadline=deadline,
                )
                last_poll_end = _clock.monotonic()
                refreshed_by_poll = True
                return WaitOutcome(self.state, self.raw_state)

            def _observe_outcome(outcome: WaitOutcome) -> None:
                self._update(state=outcome.state, raw_state=outcome.raw_state)

            def _on_observed(outcome: WaitOutcome) -> None:
                nonlocal refreshed_by_poll
                refreshed_by_poll = False
                _observe_outcome(outcome)

            try:
                resolved = await hub.wait(
                    self.id,
                    predicate,
                    timeout=timeout,
                    # `local` names no concrete region.
                    region_hint=None if (self.region or "").strip().lower() == "local" else self.region,
                    sandbox_poll_fn=_sandbox_poll_fn,
                    on_observed=_on_observed,
                )
            except SandboxTerminalStateError as error:
                self._update(state=error.state)
                if error.raw_state is not None:
                    self._update(raw_state=error.raw_state)
                # Fill in the error reason within the wait's budget. A destroyed
                # sandbox has nothing to fill in (the GET would 404), and a
                # spent budget skips it: the terminal state is the outcome
                # either way, and a failed or timed-out refresh must not
                # replace it.
                remaining = None if deadline is None else deadline - _clock.monotonic()
                if (
                    not refreshed_by_poll
                    and error.state != "destroyed"
                    and (remaining is None or remaining > 0)
                ):
                    with contextlib.suppress(DaytonaError):
                        await self._refresh_data_within(timeout=remaining, deadline=deadline)
                raise
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

        fresh = False
        while True:
            if fresh:
                if self.state in target_states:
                    return
                if self.state in effective_terminal:
                    raise _sandbox_terminal_state_error(self.id, self.state)
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
            )
            fresh = True
            if self.state in target_states:
                return
            if self.state in effective_terminal:
                raise _sandbox_terminal_state_error(self.id, self.state)
            elapsed = _clock.monotonic() - poll_started
            if elapsed < WAIT_FOR_STATE_MIN_INTERVAL_S:
                pause = WAIT_FOR_STATE_MIN_INTERVAL_S - elapsed
                if deadline is not None:
                    pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                if pause > 0:
                    await asyncio.sleep(pause)

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

    async def wait(
        self,
        *,
        until: SandboxWaitState | Sequence[SandboxWaitState] = "started",
        timeout: float = 60,
    ) -> Self:
        """Wait until the sandbox reaches ``until`` (one state or any of several) and return ``self``.

        ``timeout`` is the wait budget in seconds (default 60; ``0`` waits
        without a deadline). Running out raises :class:`RequestTimeoutError`;
        reaching ``destroying``, ``destroyed`` or ``error`` first raises
        :class:`SandboxTerminalStateError`.
        """
        _validate_timeout(timeout)
        states: Sequence[SandboxWaitState] = [until] if isinstance(until, str) else list(until)
        await self._wait_for_state(states, timeout=timeout)
        return self

    async def start(self, *, timeout: float = 60) -> None:
        """Start a stopped sandbox, or resume a paused one, and wait until it is ``started``.

        ``timeout`` is the wait budget in seconds (default 60; ``0`` = no
        deadline); running out raises :class:`RequestTimeoutError`, and a
        terminal state raises :class:`SandboxTerminalStateError`. Whether the
        sandbox can be started is the API's decision: a refusal surfaces as its
        typed error.
        """
        _validate_timeout(timeout)
        await self.refresh_data()
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

        ``timeout`` is the wait budget in seconds (default 60; ``0`` = no
        deadline); running out raises :class:`RequestTimeoutError`, and a
        terminal state raises :class:`SandboxTerminalStateError`. Which
        sandboxes can stop (today: persistent macOS sandboxes) is the API's
        decision; a refusal surfaces as its typed error.
        """
        _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/stop"))
        await self._wait_for_state(["stopped"], timeout=timeout)

    async def pause(self, *, timeout: float = 60) -> None:
        """Pause the sandbox with its memory and wait until it is ``paused``.

        ``timeout`` is the wait budget in seconds (default 60; ``0`` = no
        deadline). A pause that fails returns the sandbox to ``started``; this
        then raises :class:`SandboxTerminalStateError`. A paused macOS sandbox
        keeps its Mac slot but does not keep its reservation renewing. Which
        sandboxes can pause is the API's decision.
        """
        _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/pause"))
        await self._wait_for_state(["paused"], _PAUSE_TERMINAL_STATES, timeout=timeout)

    async def resume(self, *, timeout: float = 60) -> None:
        """Resume a paused sandbox and wait until it is ``started``.

        ``timeout`` is the wait budget in seconds (default 60; ``0`` = no
        deadline). A resume that cannot finish ends ``stopped``; this then
        raises :class:`SandboxTerminalStateError`.
        """
        _validate_timeout(timeout)
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
        with a fresh machine identity. A fork needs available capacity for
        its size; insufficient capacity can answer ``NO_FREE_SLOT`` or
        ``MAC_DISK_FULL``.

        ``wait_seconds`` (default 60; ``0`` returns as soon as the API accepts
        the fork) bounds the wait for the state the fork settles in: a fork of
        a running Linux sandbox ``started``; otherwise it rests — ``paused``
        for a memory fork of a paused source, else ``stopped`` (call
        ``start()``). A macOS fork settles ``started`` or ``stopped``. Like
        ``create()``, ``fork()`` never raises because of how the wait ended:
        when the budget runs out or the fork fails, it returns the handle —
        check ``fork.state``.
        """
        _validate_wait_seconds(wait_seconds)
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise create_error("fork name must be a non-empty string when given", 400, "BAD_REQUEST", "SDK")
        if memory is not None and not isinstance(memory, bool):
            raise create_error("fork memory must be a boolean", 400, "BAD_REQUEST", "SDK")
        class_known = self.sandbox_class is not None
        sandbox_class = await self._require_sandbox_class()
        targets: list[SandboxWaitState] = ["started", "stopped"]
        if sandbox_class == "linux" and wait_seconds > 0:
            # Only a fork of a RUNNING source comes up by itself; the others rest.
            # Read the source's state now (unless reading its class just did).
            if class_known:
                await self.refresh_data()
            if self.state == "started":
                targets = ["started"]
            elif self.state == "paused" and memory is not False:
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
        # The fork's detail read counts against the wait budget.
        deadline = _clock.monotonic() + wait_seconds if wait_seconds > 0 else None
        detail: Optional[Mapping[str, JsonValue]] = None
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
        # The fork gets its own toolbox URL from the API; inheriting the
        # parent's override could send its file and process calls to the parent.
        fork = AsyncSandbox._from_api(
            detail
            if detail is not None
            else {
                "id": fork_id,
                "status": accepted.get("status"),
                "forked_from": accepted.get("forked_from") or self.id,
                "class": sandbox_class,
            },
            self._api,
            None,
            toolbox_owner=self._toolbox_owner,
            toolbox_base_url=data.toolbox_proxy_url,
            toolbox_token=api_token,
            request_timeout=self._request_timeout,
            # The events hub IS inherited: it is per process and identity, not
            # per sandbox (unlike the toolbox override above).
            hub=self._events_hub(),
            hub_provider=self._hub_provider,
            toolbox_url_from_api=True,
        )
        for endpoint in endpoints:
            # Source and fork share the regional API endpoint that accepted the fork.
            self._learn_endpoint(endpoint)
            fork._learn_endpoint(endpoint)
        if detail is None or deadline is None or fork.state in targets:
            return fork
        remaining = deadline - _clock.monotonic()
        if remaining > 0:
            await _wait_without_raising_for_its_end(fork, targets, remaining)
        return fork

    async def delete(self, *, timeout: float = 60) -> None:
        """Delete the sandbox and wait until it is ``destroyed``.

        ``timeout`` is the wait budget in seconds (default 60; ``0`` = no
        deadline); running out raises :class:`RequestTimeoutError`, and an
        ``error`` state raises :class:`SandboxTerminalStateError`. A 404 while
        waiting counts as destroyed. A destroyed sandbox does not guarantee
        that its image can already be deleted: image deletion may still
        answer 409 for a while.
        """
        _validate_timeout(timeout)
        await self._lifecycle_mutation(RequestSpec("DELETE", f"/sandboxes/{quote(self.id, safe='')}"))
        try:
            await self._wait_for_state(["destroyed"], timeout=timeout)
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
        ``delete:sandboxes`` in addition to ``write:sandboxes``.
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
        if self.sandbox_class is None or self.persistent is None:
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
        sandbox must be persistent and stopped; it cannot start until the
        snapshot is ``ready``.

        ``name`` makes the image named and permanent. ``idle_expiry_seconds`` is
        the idle-retention window of an ANONYMOUS capture: it is deleted once
        that long has passed without a sandbox being created from it (every
        create restarts the clock). Same rule as ``images.build``: omitted or
        ``-1`` ⇒ the server maximum (7 days by default); above it ⇒ 400.
        Promote the image to keep it. ``idle_expiry_seconds`` with ``name`` is
        a 400. Returns once the API accepts the capture; wait for readiness
        with ``images.get(image.id, wait="ready")``.
        """
        body: JsonObject = {}
        if name is not None:
            body["name"] = name
        if idle_expiry_seconds is not None:
            body["idle_expiry_seconds"] = idle_expiry_seconds
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
        payload = expect_object(
            await self._require_api().send(
                RequestSpec("POST", f"/sandboxes/{quote(self.id, safe='')}/access-token", json_body=body)
            )
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
        minted = await self.create_access_token()
        self._access_token = minted.access_token
        self._access_token_expires_at = minted.expires_at
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


def sandbox_info_from_id(sandbox_id: str) -> _SandboxInfo:
    return _SandboxInfo(id=sandbox_id)


def _sandbox_info_from_wire(data: Mapping[str, JsonValue]) -> _SandboxInfo:
    """Handle metadata from a sandbox API object (detail, list or search item)."""
    sandbox_id = str(data.get("id"))
    raw_status = _wire_text(data.get("status")) or _wire_text(data.get("state")) or ""
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
        labels=_string_map_or_empty(data.get("labels")),
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


def _assert_port(port: int) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise create_error(f"port must be an integer in 1..65535, got {port!r}", 400, "BAD_REQUEST", "SDK")


def _sandbox_toolbox_base(sandbox_data: Mapping[str, JsonValue]) -> Optional[str]:
    value = sandbox_data.get("toolbox_proxy_url") or sandbox_data.get("toolboxProxyUrl")
    return str(value).rstrip("/") if isinstance(value, str) and value else None


def _optional_number(value: object) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise create_error(
            "timeout must be a non-negative finite number of seconds", 400, "BAD_REQUEST", "SDK"
        )


def _validate_wait_seconds(wait_seconds: float) -> None:
    if (
        isinstance(wait_seconds, bool)
        or not isinstance(wait_seconds, (int, float))
        or not math.isfinite(wait_seconds)
        or wait_seconds < 0
    ):
        raise create_error(
            "wait_seconds must be a non-negative finite number of seconds", 400, "BAD_REQUEST", "SDK"
        )


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


def _sandbox_terminal_state_error(sandbox_id: str, state: SandboxState) -> Exception:
    return SandboxTerminalStateError(sandbox_id, state)


def _sandbox_access_token_from_payload(payload: JsonObject) -> SandboxAccessToken:
    access_token = payload.get("access_token")
    expires_at = parse_timestamp(payload.get("expires_at"))
    if not isinstance(access_token, str) or not access_token or expires_at is None:
        raise create_error("API returned an invalid access token payload", 500, None, "SDK")
    token_type = payload.get("token_type")
    raw_operations = payload.get("operations")
    operations = (
        tuple(operation for operation in raw_operations if isinstance(operation, str))
        if isinstance(raw_operations, list)
        else ()
    )
    raw_ports = payload.get("ports")
    granted_ports = (
        tuple(port for port in raw_ports if isinstance(port, int) and not isinstance(port, bool))
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
