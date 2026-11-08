# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from collections.abc import AsyncIterator

import httpx
import pytest

from daytona.v2 import _clock
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import BadRequestError, ImageBuildFailedError, RequestTimeoutError
from daytona.v2.services import AsyncImagesService

ImageFixture = tuple[AsyncImagesService, list[str], list[httpx.Request]]


@pytest.fixture
async def image_service() -> AsyncIterator[ImageFixture]:
    statuses = ["pending", "building", "ready"]
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"id": "img-pull", "status": "pending"})
        return httpx.Response(
            200,
            json={
                "id": "img-pull",
                "class": "linux",
                "updated_at": "2026-01-01T00:00:00Z",
                "retention": "permanent",
                "region_ids": [],
                "format_version": 1,
                "format_outdated": False,
                "platform": False,
                "default": False,
                "deprecated": False,
                "status": statuses.pop(0),
                "name": None,
                "anonymous": True,
                "region_id": "us",
                "origin": "registry",
                "error": "registry denied",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        yield AsyncImagesService(AsyncTransport("https://api.test", client=client)), statuses, requests


@pytest.mark.anyio
async def test_pull_returns_ready_when_import_is_pending(image_service: ImageFixture) -> None:
    # Given an import which remains pending across a long poll.
    service, _, requests = image_service
    # When pulling with the default wait.
    image = await service.pull("python:3.12")
    # Then readiness, not submission, completes the call.
    assert image.status == "ready"
    assert [request.url.params.get("wait") for request in requests[2:]] == ["ready", "ready"]


@pytest.mark.anyio
async def test_pull_returns_pending_when_wait_is_disabled(image_service: ImageFixture) -> None:
    # Given a pending import.
    service, _, requests = image_service
    # When opting out of waiting.
    image = await service.pull("python:3.12", wait=False)
    # Then the initial handle is returned without a long poll.
    assert image.status == "pending"
    assert len(requests) == 2


@pytest.mark.anyio
async def test_pull_waits_without_deadline_when_timeout_is_zero(image_service: ImageFixture) -> None:
    # Given an import with multiple nonterminal observations.
    service, _, _ = image_service
    # When zero requests an unbounded wait.
    image = await service.pull("python:3.12", timeout=0)
    # Then readiness still completes the call.
    assert image.status == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
async def test_pull_refuses_invalid_timeout_before_submission(
    image_service: ImageFixture,
    timeout: float,
) -> None:
    # Given a caller-supplied invalid budget.
    service, _, requests = image_service
    # When pulling with that budget.
    with pytest.raises(BadRequestError):
        await service.pull("python:3.12", timeout=timeout)
    # Then the invalid request never crosses the network boundary.
    assert requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("statuses", [["failed"], ["pending", "failed"], ["error"], ["pending", "error"]])
async def test_pull_raises_server_error_when_import_fails(
    image_service: ImageFixture, statuses: list[str]
) -> None:
    # Given an import failing immediately or after submission.
    service, responses, _ = image_service
    responses[:] = statuses
    # When pulling to readiness.
    with pytest.raises(ImageBuildFailedError) as error:
        await service.pull("python:3.12")
    # Then the recorded server failure and image identity survive.
    assert error.value.image_id == "img-pull"
    assert error.value.build_error == "registry denied"
    assert str(error.value) == "registry denied"


@pytest.mark.anyio
async def test_pull_raises_timeout_when_import_exhausts_budget(
    image_service: ImageFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given repeated nonterminal long polls and a deterministic clock.
    service, _, requests = image_service
    monkeypatch.setattr(_clock, "monotonic", lambda: float(len(requests)))
    # When the submission and reads exhaust a three-second budget.
    with pytest.raises(RequestTimeoutError):
        await service.pull("python:3.12", timeout=3)
    # Then the long poll used only the remaining budget.
    assert requests[2].url.params["timeout_ms"] == "1000"


@pytest.mark.anyio
@pytest.mark.parametrize("phase", [1, 2, 3], ids=["post", "initial-get", "poll"])
@pytest.mark.parametrize("status", ["ready", "failed", "error"])
@pytest.mark.parametrize("arrival", [0.099, 0.1, 0.101])
async def test_pull_rejects_late_terminal_results_when_clock_crosses_deadline(
    monkeypatch: pytest.MonkeyPatch,
    phase: int,
    status: str,
    arrival: float,
) -> None:
    # Given a peer delivering a terminal result at a precise deadline boundary.
    clock = [0.0]
    requests: list[httpx.Request] = []
    monkeypatch.setattr(_clock, "monotonic", lambda: clock[0])

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == phase:
            clock[0] = arrival
        return httpx.Response(
            200,
            json={
                "id": "img-pull",
                "anonymous": False,
                "class": "linux",
                "updated_at": "2026-01-01T00:00:00Z",
                "retention": "permanent",
                "region_ids": [],
                "format_version": 1,
                "format_outdated": False,
                "platform": False,
                "default": False,
                "deprecated": False,
                "region_id": "region-c",
                "origin": "registry",
                "created_at": "2026-01-01T00:00:00Z",
                "status": "building" if len(requests) < phase else status,
                "error": "registry denied",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AsyncImagesService(AsyncTransport("https://api.test", client=client))
        # When the same pull budget covers submission, initial lookup, and poll.
        if arrival >= 0.1:
            with pytest.raises(RequestTimeoutError, match="^image pull did not finish before timeout$"):
                await service.pull("alpine", timeout=0.1)
        elif status in ("failed", "error"):
            with pytest.raises(ImageBuildFailedError, match="^registry denied$"):
                await service.pull("alpine", timeout=0.1)
        else:
            image = await service.pull("alpine", timeout=0.1)
            assert image.status == "ready"
        # Then every issued request stays within the remaining budget, without padding.
        timeouts = [request.extensions["timeout"]["read"] for request in requests]
        assert all(0 < value <= 0.1 for value in timeouts)
