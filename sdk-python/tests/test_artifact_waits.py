# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Artifact waits (``images.get`` / ``snapshots.get`` with ``wait="ready"``).

A budget that runs out before the artifact settles raises
``RequestTimeoutError`` (408, ``REQUEST_TIMEOUT``, ``SDK``), never an
unsettled observation; a settled artifact is returned; no budget is one hold
whose answer is returned; a budget above the timer maximum waits without a
deadline. The TypeScript twin is ``tests/artifact-waits-contract.test.ts``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import RequestTimeoutError

pytestmark = pytest.mark.anyio


def _image(status: str) -> dict[str, object]:
    return {
        "id": "img",
        "class": "linux",
        "status": status,
        "region_id": "region-a",
        "region_ids": ["region-a"],
        "anonymous": True,
        "retention": "idle",
        "origin": "registry",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "format_version": 2,
        "format_outdated": False,
    }


class _Server:
    """Answers each read with ``statuses`` in turn (the last repeats), after
    holding for the request's ``timeout_ms`` when ``hold`` is set."""

    def __init__(self, statuses: list[str], *, hold: bool) -> None:
        self.reads = 0
        stop = threading.Event()
        self.stop = stop
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                index = min(outer.reads, len(statuses) - 1)
                outer.reads += 1
                query = parse_qs(urlsplit(self.path).query)
                if hold and "timeout_ms" in query:
                    stop.wait(int(query["timeout_ms"][0]) / 1000)
                data = json.dumps(_image(statuses[index])).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    servers: list[_Server] = []

    def start(statuses: list[str], *, hold: bool = True) -> _Server:
        server = _Server(statuses, hold=hold)
        servers.append(server)
        monkeypatch.setenv("DAYTONA_V2_API_URL", server.url)
        return server

    yield start
    for server in servers:
        server.close()


_CONFIG = DaytonaConfig(api_key="key", request_timeout=30)


async def test_a_budget_spent_on_a_building_image_raises(serve: object) -> None:
    # The server answers "building" exactly at the end of every hold.
    serve(["building"])  # type: ignore[operator]
    async with AsyncDaytona(_CONFIG) as root:
        started = time.monotonic()
        with pytest.raises(RequestTimeoutError) as raised:
            await root.v2.images.get("img", wait="ready", timeout=1)
        elapsed = time.monotonic() - started
    assert (raised.value.code, raised.value.source) == ("REQUEST_TIMEOUT", "SDK")
    assert elapsed < 3


async def test_a_wait_spanning_two_holds_never_returns_the_first_answer(
    serve: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # For this test only: holds of 300 ms (reads paced 1 s apart), so a 2 s
    # budget spans two holds, the first answered "building".
    monkeypatch.setattr("daytona.v2.services._ARTIFACT_LONGPOLL_MAX_MS", 300)
    server = serve(["building"])  # type: ignore[operator]
    async with AsyncDaytona(_CONFIG) as root:
        with pytest.raises(RequestTimeoutError):
            await root.v2.images.get("img", wait="ready", timeout=2)
    assert server.reads >= 2


async def test_ready_within_the_budget_is_returned(serve: object) -> None:
    serve(["ready"], hold=False)  # type: ignore[operator]
    async with AsyncDaytona(_CONFIG) as root:
        image = await root.v2.images.get("img", wait="ready", timeout=5)
    assert image.status == "ready"


async def test_without_a_budget_the_servers_answer_is_returned(serve: object) -> None:
    serve(["building"], hold=False)  # type: ignore[operator]
    async with AsyncDaytona(_CONFIG) as root:
        image = await root.v2.images.get("img", wait="ready")
    assert image.status == "building"


async def test_a_budget_above_the_timer_maximum_keeps_waiting(serve: object) -> None:
    # Above about 24.8 days is no deadline: holds repeat (paced 1 s apart
    # when the server answers early) until the image settles.
    server = serve(["pending"], hold=False)  # type: ignore[operator]
    async with AsyncDaytona(_CONFIG) as root:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(root.v2.images.get("img", wait="ready", timeout=3_000_000), 2.5)
    assert server.reads >= 3
