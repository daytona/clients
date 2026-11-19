# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

import httpx
import pytest

from daytona.v2._transport import AsyncTransport
from daytona.v2.computer_use import AsyncComputerUseService
from daytona.v2.computer_use_types import ComputerUseResolution
from daytona.v2.errors import UnprocessableEntityError, UnsupportedOperationError


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.parametrize("reported", [None, "xfce", "future-desktop"])
@pytest.mark.parametrize("with_resolution", [False, True])
@pytest.mark.anyio
async def test_environment_is_refused_when_guest_does_not_confirm_it(
    verb: Literal["start", "restart"], reported: str | None, with_resolution: bool
) -> None:
    # Given a guest that accepts the body but does not apply GNOME.
    requests: list[str] = []

    def guest(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "message": "started",
                    "status": {} if verb == "start" else "active",
                    "desktop": "ready",
                },
            )
        status = {"status": "active", "desktop": "ready", "resolution": {"width": 1920, "height": 1080}}
        if reported is not None:
            status["environment"] = reported
        return httpx.Response(200, json=status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(guest)) as client:
        service = AsyncComputerUseService(AsyncTransport("http://guest.invalid", client=client))

        # When selecting GNOME, then success is refused with the existing SDK error.
        with pytest.raises(UnsupportedOperationError) as raised:
            await getattr(service, verb)(
                environment="gnome",
                resolution=ComputerUseResolution(width=1920, height=1080) if with_resolution else None,
            )

    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        400,
        "UNSUPPORTED_OPERATION",
        "SDK",
    )
    assert requests == [f"/computeruse/{verb}", "/computeruse/status"]


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.parametrize("environment", ["xfce", "gnome"])
@pytest.mark.parametrize("with_resolution", [False, True])
@pytest.mark.anyio
async def test_environment_succeeds_when_one_status_confirms_the_settings(
    verb: Literal["start", "restart"], environment: Literal["xfce", "gnome"], with_resolution: bool
) -> None:
    # Given a guest that confirms the requested environment and resolution.
    requests: list[str] = []

    def guest(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "message": "started",
                    "status": {} if verb == "start" else "active",
                    "desktop": "ready",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "active",
                "desktop": "ready",
                "environment": environment,
                "resolution": {"width": 1920, "height": 1080},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(guest)) as client:
        service = AsyncComputerUseService(AsyncTransport("http://guest.invalid", client=client))

        # When selecting the settings.
        result = await getattr(service, verb)(
            environment=environment,
            resolution=ComputerUseResolution(width=1920, height=1080) if with_resolution else None,
        )

    # Then the lifecycle result succeeds with exactly one status request.
    assert result.message == "started"
    assert requests == [f"/computeruse/{verb}", "/computeruse/status"]


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.parametrize("with_resolution", [False, True])
@pytest.mark.anyio
async def test_guest_422_is_preserved_when_environment_is_unknown(
    verb: Literal["start", "restart"], with_resolution: bool
) -> None:
    # Given a Phase-1 guest that refuses the unknown field.
    requests: list[str] = []

    def guest(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            422,
            json={
                "message": "unknown field",
                "code": "UNPROCESSABLE_ENTITY",
                "source": "DAYTONA_DAEMON",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(guest)) as client:
        service = AsyncComputerUseService(AsyncTransport("http://guest.invalid", client=client))

        # When selecting GNOME, then the original daemon refusal surfaces.
        with pytest.raises(UnprocessableEntityError) as raised:
            await getattr(service, verb)(
                environment="gnome",
                resolution=ComputerUseResolution(width=1920, height=1080) if with_resolution else None,
            )

    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        422,
        "UNPROCESSABLE_ENTITY",
        "DAYTONA_DAEMON",
    )
    assert requests == [f"/computeruse/{verb}"]
