# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""No object the SDK keeps a credential in shows it in ``repr()`` / ``str()``.

The TypeScript SDK's tests/credential-inspection.test.ts holds its objects to
the same rule (util.inspect, JSON).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2._auth import ProjectTokenAuth, _CachedProjectToken, _TokenExchange
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import _resolve_config
from daytona.v2.types import DaytonaConfig as V2DaytonaConfig
from daytona.v2.types import PreviewLink, SandboxAccessToken, SignedPreviewLink

_EXPIRES = datetime(2030, 1, 1, tzinfo=timezone.utc)

SECRET = "s3cret-credential-value"


def _objects() -> list[object]:
    return [
        _TokenExchange(access_token=SECRET, expires_in=60),
        _CachedProjectToken(access_token=SECRET, expires_at_monotonic=1.0, refresh_skew_seconds=1.0),
        DaytonaConfig(api_key=SECRET),
        DaytonaConfig(jwt_token=SECRET, project_id="p"),
        V2DaytonaConfig(api_key=SECRET),
        V2DaytonaConfig(jwt_token=SECRET, project_id="p"),
        _resolve_config(V2DaytonaConfig(api_key=SECRET)),
        SandboxAccessToken(
            access_token=SECRET, token_type="Bearer", expires_at="2030-01-01T00:00:00Z", operations=[]
        ),
        PreviewLink(url="https://preview.invalid", token=SECRET),
        SignedPreviewLink(url=f"https://preview.invalid/{SECRET}", token=SECRET, expires_at=_EXPIRES),
        ProjectTokenAuth(
            api_url="http://api.invalid", jwt_token=SECRET, project_id="p", source="sdk-python-async"
        ),
        AsyncTransport("http://api.invalid", SECRET),
    ]


@pytest.mark.parametrize("value", _objects(), ids=lambda value: type(value).__name__)
def test_repr_and_str_never_show_the_credential(value: object) -> None:
    assert SECRET not in repr(value)
    assert SECRET not in str(value)


@pytest.mark.anyio
async def test_the_clients_never_show_the_credential() -> None:
    async_client = AsyncDaytona(DaytonaConfig(api_key=SECRET))
    try:
        for value in (async_client, async_client.v2):
            assert SECRET not in repr(value)
            assert SECRET not in str(value)
    finally:
        await async_client.close()
    with Daytona(DaytonaConfig(api_key=SECRET)) as client:
        for value in (client, client.v2):
            assert SECRET not in repr(value)
            assert SECRET not in str(value)
