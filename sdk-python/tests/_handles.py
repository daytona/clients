# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Test-only metadata-less sandbox handles.

A sandbox object comes from ``create*()``, ``get()`` or ``list()``. Unit tests need a
handle bound to a stub toolbox without an API round trip, so this
builds one through the client's private factory — the same one ``create()``
uses — with the test's explicit toolbox URL and token.
"""

from __future__ import annotations

from typing import Optional, Union, overload

from daytona.v2._sync import Daytona
from daytona.v2._sync.sandbox import Sandbox
from daytona.v2.daytona import AsyncDaytona, _on_event_loop
from daytona.v2.sandbox import AsyncSandbox, sandbox_info_from_id
from daytona.v2.types import _SandboxInfo as SandboxInfo


@overload
def lazy_sandbox(
    client: AsyncDaytona,
    sandbox: Union[str, SandboxInfo],
    *,
    toolbox_url: Optional[str] = ...,
    access_token: Optional[str] = ...,
    access_token_expires_at: Optional[str] = ...,
    _access_token_from_create: bool = ...,
) -> AsyncSandbox: ...
@overload
def lazy_sandbox(
    client: Daytona,
    sandbox: Union[str, SandboxInfo],
    *,
    toolbox_url: Optional[str] = ...,
    access_token: Optional[str] = ...,
    access_token_expires_at: Optional[str] = ...,
    _access_token_from_create: bool = ...,
) -> Sandbox: ...
def lazy_sandbox(
    client: Union[AsyncDaytona, Daytona],
    sandbox: Union[str, SandboxInfo],
    *,
    toolbox_url: Optional[str] = None,
    access_token: Optional[str] = None,
    access_token_expires_at: Optional[str] = None,
    _access_token_from_create: bool = False,
) -> Union[AsyncSandbox, Sandbox]:
    impl = client._impl if isinstance(client, Daytona) else client
    info = sandbox_info_from_id(sandbox) if isinstance(sandbox, str) else sandbox
    handle = impl._sandbox_handle(
        info,
        direct_toolbox_url=toolbox_url,
        access_token=access_token,
        access_token_expires_at=access_token_expires_at if _access_token_from_create else None,
        access_token_caller_supplied=access_token is not None and not _access_token_from_create,
        hub=impl._acquire_hub() if _on_event_loop() else None,
        hub_provider=impl._acquire_hub,
    )
    if isinstance(client, Daytona):
        return Sandbox(handle, client._bridge)
    return handle
