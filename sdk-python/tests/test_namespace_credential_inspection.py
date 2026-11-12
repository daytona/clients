# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Custom-client namespace diagnostics mirror the TypeScript constructor cases."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from json import dumps, loads
from pprint import pformat
from typing import Final, Protocol, TypeVar

import httpx
import pytest

from daytona.v2._bridge import SyncBridge
from daytona.v2._sync._runtime import UNBOUND
from daytona.v2._sync.ports import PortsService
from daytona.v2._sync.services import (
    ImagesService,
    MacModelsService,
    ReservationsService,
    SecretsService,
    SnapshotsService,
)
from daytona.v2._transport import AsyncTransport
from daytona.v2.ports import AsyncPortsService
from daytona.v2.sandbox import AsyncSandbox, sandbox_info_from_id
from daytona.v2.services import (
    AsyncImagesService,
    AsyncMacModelsService,
    AsyncReservationsService,
    AsyncSecretsService,
    AsyncSnapshotsService,
)
from daytona.v2.types import PreviewLink, SandboxAccessToken, SignedPreviewLink

_EXPIRES = datetime(2030, 1, 1, tzinfo=timezone.utc)

KEY: Final = "dtn_namespace_caller_secret"
ACCESS: Final = "opaque_namespace_provider_secret"


class DiagnosticNamespace(Protocol):
    def __repr__(self) -> str: ...


_Service = TypeVar("_Service", bound=DiagnosticNamespace)


@dataclass(frozen=True, slots=True)
class CallerOwnedProvider:
    """Deliberately unredacted caller value that wrappers must not traverse."""

    token: str

    async def bearer(self) -> str:
        return self.token

    async def on_unauthorized(self, failed_token: str | None) -> str:
        return self.token


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
@pytest.mark.parametrize("with_provider", [False, True], ids=["static-token", "custom-provider"])
@pytest.mark.parametrize(
    "constructors",
    [
        (AsyncImagesService, ImagesService),
        (AsyncSnapshotsService, SnapshotsService),
        (AsyncReservationsService, ReservationsService),
        (AsyncMacModelsService, MacModelsService),
        (AsyncSecretsService, SecretsService),
    ],
    ids=["images", "snapshots", "reservations", "mac-models", "secrets"],
)
async def test_namespace_repr_when_transport_retains_caller_credentials(
    constructors: tuple[
        Callable[[AsyncTransport], _Service], Callable[[_Service, SyncBridge], DiagnosticNamespace]
    ],
    sync: bool,
    with_provider: bool,
) -> None:
    # Given: Python image configuration is retained by its transport, not a
    # separate ImagesService API-config argument as in TypeScript.
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}) as caller:
        headers = caller.headers
        original_headers = list(headers.raw)
        original_auth = caller.auth
        provider = CallerOwnedProvider(ACCESS)
        original_provider = repr(provider)
        transport = AsyncTransport("https://api.invalid", KEY, client=caller)
        if with_provider:
            transport.auth_provider = provider
        # When: construct the actual namespace and, when requested, its
        # generated synchronous twin. No bridge thread or I/O is needed for repr.
        async_service, sync_service = constructors
        namespace = async_service(transport)
        holder = sync_service(namespace, UNBOUND) if sync else namespace
        # Then: neither direct nor nested diagnostics traverse caller state.
        for output in (repr(holder), str(holder), pformat({"namespace": [holder]})):
            assert KEY not in output
            assert ACCESS not in output
        assert caller.headers is headers
        assert list(caller.headers.raw) == original_headers
        assert caller.auth is original_auth
        assert not caller.is_closed
        assert repr(provider) == original_provider
        assert ACCESS in original_provider


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
@pytest.mark.parametrize("with_provider", [False, True], ids=["static-token", "custom-provider"])
async def test_sandbox_ports_repr_when_api_retains_caller_credentials(
    sync: bool, with_provider: bool
) -> None:
    # Given
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}) as caller:
        headers = caller.headers
        original_headers = list(headers.raw)
        transport = AsyncTransport("https://api.invalid", KEY, client=caller)
        provider = CallerOwnedProvider(ACCESS)
        if with_provider:
            transport.auth_provider = provider
        # When
        sandbox = AsyncSandbox(sandbox_info_from_id("sandbox"), transport, None, _access_token=ACCESS)
        ports = PortsService(sandbox.ports, UNBOUND) if sync else sandbox.ports
        direct_ports = AsyncPortsService(None, transport, _sandbox_id="sandbox")
        # Then: cover both direct ports construction and the parent's namespace.
        for namespace in (direct_ports, ports):
            for output in (repr(namespace), pformat({"ports": [namespace]})):
                assert KEY not in output
                assert ACCESS not in output
        assert caller.headers is headers
        assert list(caller.headers.raw) == original_headers
        assert not caller.is_closed
        assert provider.token == ACCESS


@pytest.mark.parametrize(
    ("holder", "credential_field", "expected_url"),
    [
        (
            SandboxAccessToken(
                access_token=ACCESS,
                token_type="Bearer",
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                operations=(),
            ),
            "access_token",
            None,
        ),
        (PreviewLink(url="https://preview.invalid", token=ACCESS), "token", "https://preview.invalid"),
        (
            SignedPreviewLink(url=f"https://{ACCESS}.preview.invalid", token=ACCESS, expires_at=_EXPIRES),
            "token",
            f"https://{ACCESS}.preview.invalid",
        ),
    ],
    ids=["access-token", "preview", "signed-preview"],
)
def test_explicit_dto_json_when_credentials_are_requested(
    holder: SandboxAccessToken | PreviewLink | SignedPreviewLink,
    credential_field: str,
    expected_url: str | None,
) -> None:
    # Given: these credential-return dataclasses are shared by async/sync APIs.
    # When
    payload = loads(dumps(asdict(holder), default=str))
    # Then: diagnostic safety must not suppress explicitly requested credentials.
    assert payload[credential_field] == ACCESS
    assert payload.get("url") == expected_url
