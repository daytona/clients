# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The client's ``max_response_bytes`` reaches every transport the client
builds: the control-plane API, and the toolbox transport of a sandbox handle
the client created. A buffered response above it is ``PayloadTooLargeError``
(413, ``PAYLOAD_TOO_LARGE``, ``SDK``). The TypeScript twin is
``tests/client-response-cap.test.ts``."""

from __future__ import annotations

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import PayloadTooLargeError
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_CAP = 2048


def _detail(url: str, **extra: str) -> JsonObject:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "sbx",
        "status": "running",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": f"{url}/toolbox",
        **extra,
    }


def _refused(error: PayloadTooLargeError) -> tuple[object, object, object]:
    return error.status_code, error.code, error.source


async def test_the_cap_reaches_the_control_plane_and_the_handles_toolbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with FakeApi([]) as fake:
        fake._responses.extend(
            [
                # get(): a detail padded past the cap.
                PlannedResponse(200, _detail(fake.url, padding="x" * (_CAP * 2))),
                # get(): a detail under it, for the handle.
                PlannedResponse(200, _detail(fake.url)),
                # The handle's toolbox read, past the cap.
                PlannedResponse(200, b"y" * (_CAP * 2)),
            ]
        )
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key", max_response_bytes=_CAP)) as root:
            with pytest.raises(PayloadTooLargeError) as control_plane:
                await root.v2.get("sbx")
            sandbox = await root.v2.get("sbx", access_token="tok")
            with pytest.raises(PayloadTooLargeError) as toolbox:
                await sandbox.fs.read_bytes("/big")
    assert _refused(control_plane.value) == _refused(toolbox.value) == (413, "PAYLOAD_TOO_LARGE", "SDK")
    assert [request.path.split("?")[0] for request in fake.requests] == [
        "/sandboxes/sbx",
        "/sandboxes/sbx",
        "/toolbox/sbx/files/download",
    ]


async def test_two_clients_with_different_caps_get_their_own_shared_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same credentials, same loop, shared hubs on: the hub is keyed on the
    # client's cap, so each client's hub requests (here the long-poll
    # fallback) enforce that client's own cap.
    from daytona.v2._events import _TargetPredicate

    predicate = _TargetPredicate(
        states=frozenset({"started"}), terminal_states=frozenset({"destroying", "destroyed", "error"})
    )
    with FakeApi([]) as fake:
        padded = _detail(fake.url, padding="x" * (_CAP * 2))
        fake._responses.extend([PlannedResponse(200, padded), PlannedResponse(200, padded)])
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with (
            AsyncDaytona(DaytonaConfig(api_key="key", max_response_bytes=_CAP)) as small,
            AsyncDaytona(DaytonaConfig(api_key="key", max_response_bytes=0)) as unlimited,
        ):
            small_hub = small.v2._acquire_hub()
            unlimited_hub = unlimited.v2._acquire_hub()
            assert small_hub is not None and unlimited_hub is not None and small_hub is not unlimited_hub
            with pytest.raises(PayloadTooLargeError):
                await small_hub._fallback_wait("sbx", predicate, timeout=5)
            outcome = await unlimited_hub._fallback_wait("sbx", predicate, timeout=5)
    assert outcome.state == "started"
    assert len(fake.requests) == 2
