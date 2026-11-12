# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Credential-bearing SDK objects must not disclose bearers in repr output."""

from datetime import datetime, timezone

import httpx
import pytest
from _handles import lazy_sandbox

from daytona import AsyncDaytona, Daytona
from daytona import DaytonaConfig as RootConfig
from daytona.v2._auth import _CachedProjectToken
from daytona.v2._transport import AsyncTransport, SyncTransport
from daytona.v2.daytona import _resolve_config, _ResolvedConfig
from daytona.v2.types import DaytonaConfig, PreviewLink, SandboxAccessToken, SignedPreviewLink

_EXPIRES = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "holder",
    [
        RootConfig(api_key="dtn_inspection_secret"),
        RootConfig(jwt_token="dtn_inspection_secret"),
        DaytonaConfig(api_key="dtn_inspection_secret"),
        DaytonaConfig(jwt_token="dtn_inspection_secret"),
        _CachedProjectToken("dtn_inspection_secret", 100.0, 30.0),
        PreviewLink(url="https://preview.invalid", token="dtn_inspection_secret"),
        SignedPreviewLink(
            url="https://dtn_inspection_secret.preview.invalid",
            token="dtn_inspection_secret",
            expires_at=_EXPIRES,
        ),
        SandboxAccessToken(
            access_token="dtn_inspection_secret",
            token_type="Bearer",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            operations=(),
        ),
        _resolve_config(DaytonaConfig(api_key="dtn_inspection_secret")),
    ],
)
def test_repr_omits_credentials_when_holder_contains_bearer(
    holder: RootConfig
    | DaytonaConfig
    | _CachedProjectToken
    | PreviewLink
    | SignedPreviewLink
    | SandboxAccessToken
    | _ResolvedConfig,
) -> None:
    # Given: a credential-bearing SDK value.
    # When
    output = repr({"holder": holder})
    # Then
    assert "dtn_inspection_secret" not in output
    assert "dtn_" not in output
    assert "opaque_" not in output


def test_repr_omits_credentials_when_sync_clients_and_sandbox_are_initialized() -> None:
    # Given
    with Daytona(RootConfig(api_key="dtn_client_secret")) as root:
        client = root.v2
        sandbox = lazy_sandbox(
            client, "sandbox", access_token="opaque_sandbox_secret", toolbox_url="https://toolbox.invalid"
        )
        # When
        output = repr([root, root._config, client, client._impl._api, sandbox, sandbox.process])
        # Then
        assert "dtn_client_secret" not in output
        assert "opaque_sandbox_secret" not in output
        assert "dtn_" not in output
        assert "opaque_" not in output
        assert sandbox.access_token == "opaque_sandbox_secret"


@pytest.mark.anyio
async def test_repr_omits_url_credentials_when_transport_has_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an endpoint whose userinfo, query and fragment contain credentials.
    url = "https://private-user:private-password@api.invalid/path?token=private-query#private-fragment"
    monkeypatch.setenv("DAYTONA_V2_API_URL", url)
    config = _resolve_config(DaytonaConfig(api_key="key"))
    with httpx.Client() as sync_client:
        async with httpx.AsyncClient() as async_client:
            transports = [SyncTransport(url, client=sync_client), AsyncTransport(url, client=async_client)]
            # When transport diagnostics are rendered.
            output = repr([config, *transports])
            # Then the endpoint is not traversed by repr (the same policy as TypeScript).
            for secret in ("private-user", "private-password", "private-query", "private-fragment"):
                assert secret not in output


def test_repr_omits_url_credentials_when_preview_links_have_credential_bearing_urls() -> None:
    # Given preview links whose URLs contain credentials.
    url = "https://private-user:private-password@preview.invalid/path?token=private-query#private-fragment"
    links = [
        PreviewLink(url=url, token="token"),
        SignedPreviewLink(url=url, token="token", expires_at=_EXPIRES),
    ]
    # When diagnostics are rendered.
    output = repr(links)
    # Then diagnostics omit URL credentials without changing the explicit values.
    for secret in ("private-user", "private-password", "private-query", "private-fragment"):
        assert secret not in output
    assert [link.url for link in links] == [url, url]


@pytest.mark.anyio
async def test_repr_omits_credentials_when_async_clients_and_sandbox_are_initialized() -> None:
    # Given
    async with AsyncDaytona(
        RootConfig(jwt_token="eyJ.client_secret.signature", project_id="project")
    ) as root:
        client = root.v2
        sandbox = lazy_sandbox(
            client, "sandbox", access_token="opaque_sandbox_secret", toolbox_url="https://toolbox.invalid"
        )
        # When
        output = repr([root, root._config, client, client._api, client._auth, sandbox, sandbox.process])
        # Then
        assert "eyJ.client_secret.signature" not in output
        assert "opaque_sandbox_secret" not in output
        assert "dtn_" not in output
        assert "opaque_" not in output
        assert sandbox.access_token == "opaque_sandbox_secret"
