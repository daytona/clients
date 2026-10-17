# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=protected-access

# The SDK 1.x sync root client. Imports only the sync v1 client chain (no async modules).

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Callable, overload

from typing_extensions import deprecated

from ._sync.daytona import Daytona as _DaytonaV1
from ._sync.sandbox import Sandbox
from ._sync.secret import SecretService
from ._sync.snapshot import SnapshotService
from ._sync.volume import VolumeService
from ._sync.warm_pool import WarmPoolService
from ._versioning import warn_root_namespace_once
from .common.daytona import CodeLanguage, CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams, DaytonaConfig
from .common.sandbox import ListSandboxesQuery


class Daytona:
    """The SDK 1.x root client.

    It owns one v1 client, returned by `v1` (the same instance on every access); use
    `daytona.v1.<member>`, the form SDK 2.x keeps. `close()` and the context manager are not
    deprecated. Every other member forwards to `v1` and is deprecated: the first use in a process
    emits one `DeprecationWarning`.

    Example:
        ```python
        from daytona import Daytona

        with Daytona() as daytona:
            sandbox = daytona.v1.create()
        ```
    """

    _v1: _DaytonaV1

    def __init__(self, config: DaytonaConfig | None = None):
        """Create the root client and its v1 client from `config` (or the environment)."""
        self._v1 = _DaytonaV1(config)

    @property
    def v1(self) -> _DaytonaV1:
        """The v1 client (`daytona.v1.Daytona`); the same instance on every access."""
        return self._v1

    def __enter__(self) -> "Daytona":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the v1 client. Not deprecated."""
        self.v1.close()

    @property
    @deprecated("Use `daytona.v1.default_language` instead. The root form is removed in SDK 2.x.", category=None)
    def default_language(self) -> CodeLanguage:
        warn_root_namespace_once("default_language")
        return self.v1.default_language

    @default_language.setter
    @deprecated("Use `daytona.v1.default_language` instead. The root form is removed in SDK 2.x.", category=None)
    def default_language(self, value: CodeLanguage) -> None:
        warn_root_namespace_once("default_language")
        self.v1.default_language = value

    @property
    @deprecated("Use `daytona.v1.volume` instead. The root form is removed in SDK 2.x.", category=None)
    def volume(self) -> VolumeService:
        warn_root_namespace_once("volume")
        return self.v1.volume

    @volume.setter
    @deprecated("Use `daytona.v1.volume` instead. The root form is removed in SDK 2.x.", category=None)
    def volume(self, value: VolumeService) -> None:
        warn_root_namespace_once("volume")
        self.v1.volume = value

    @property
    @deprecated("Use `daytona.v1.snapshot` instead. The root form is removed in SDK 2.x.", category=None)
    def snapshot(self) -> SnapshotService:
        warn_root_namespace_once("snapshot")
        return self.v1.snapshot

    @snapshot.setter
    @deprecated("Use `daytona.v1.snapshot` instead. The root form is removed in SDK 2.x.", category=None)
    def snapshot(self, value: SnapshotService) -> None:
        warn_root_namespace_once("snapshot")
        self.v1.snapshot = value

    @property
    @deprecated("Use `daytona.v1.secret` instead. The root form is removed in SDK 2.x.", category=None)
    def secret(self) -> SecretService:
        warn_root_namespace_once("secret")
        return self.v1.secret

    @secret.setter
    @deprecated("Use `daytona.v1.secret` instead. The root form is removed in SDK 2.x.", category=None)
    def secret(self, value: SecretService) -> None:
        warn_root_namespace_once("secret")
        self.v1.secret = value

    @property
    @deprecated("Use `daytona.v1.warm_pool` instead. The root form is removed in SDK 2.x.", category=None)
    def warm_pool(self) -> WarmPoolService:
        warn_root_namespace_once("warm_pool")
        return self.v1.warm_pool

    @warm_pool.setter
    @deprecated("Use `daytona.v1.warm_pool` instead. The root form is removed in SDK 2.x.", category=None)
    def warm_pool(self, value: WarmPoolService) -> None:
        warn_root_namespace_once("warm_pool")
        self.v1.warm_pool = value

    @overload
    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    def create(
        self,
        params: CreateSandboxFromSnapshotParams | None = None,
        *,
        timeout: float = 60,
    ) -> Sandbox: ...

    @overload
    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    def create(
        self,
        params: CreateSandboxFromImageParams | None = None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs: Callable[[str], None] | None = None,
    ) -> Sandbox: ...

    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    def create(
        self,
        params: CreateSandboxFromSnapshotParams | CreateSandboxFromImageParams | None = None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs: Callable[[str], None] | None = None,
    ) -> Sandbox:
        warn_root_namespace_once("create")
        # Forward exactly what the implementation accepts (snapshot or image params, and the log callback
        # with either); its public overloads cannot express that union, so call it untyped.
        create = getattr(self.v1, "create")
        return create(params, timeout=timeout, on_snapshot_create_logs=on_snapshot_create_logs)

    @deprecated("Use `daytona.v1.delete` instead. The root form is removed in SDK 2.x.", category=None)
    def delete(self, sandbox: Sandbox, timeout: float = 60, wait: bool = False) -> None:
        warn_root_namespace_once("delete")
        self.v1.delete(sandbox, timeout, wait=wait)

    @deprecated("Use `daytona.v1.get` instead. The root form is removed in SDK 2.x.", category=None)
    def get(self, sandbox_id_or_name: str, request_timeout: float | None = None) -> Sandbox:
        warn_root_namespace_once("get")
        return self.v1.get(sandbox_id_or_name, request_timeout=request_timeout)

    @deprecated("Use `daytona.v1.list` instead. The root form is removed in SDK 2.x.", category=None)
    def list(
        self,
        query: ListSandboxesQuery | None = None,
        request_timeout: float | None = None,
    ) -> Iterator[Sandbox]:
        warn_root_namespace_once("list")
        return self.v1.list(query, request_timeout=request_timeout)

    @deprecated("Use `daytona.v1.start` instead. The root form is removed in SDK 2.x.", category=None)
    def start(self, sandbox: Sandbox, timeout: float = 60) -> None:
        warn_root_namespace_once("start")
        self.v1.start(sandbox, timeout)

    @deprecated("Use `daytona.v1.stop` instead. The root form is removed in SDK 2.x.", category=None)
    def stop(self, sandbox: Sandbox, timeout: float = 60) -> None:
        warn_root_namespace_once("stop")
        self.v1.stop(sandbox, timeout)
