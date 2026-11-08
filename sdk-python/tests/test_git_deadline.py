# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A git clone / pull / push timeout is one total deadline for the whole request,
replacing the client's request timeout, against a real HTTP server (the same
as the TypeScript SDK's client-config test)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import ClientTimeoutError
from daytona.v2.git import AsyncGitService


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path.endswith("/git/pull"):
            # Every byte arrives well inside a per-read timeout; only a total deadline ends it.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "20")
            self.end_headers()
            for _ in range(20):
                time.sleep(0.05)
                self.wfile.write(b" ")
                self.wfile.flush()
            return
        time.sleep(1.3)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture
def base() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
async def test_a_git_remote_timeout_is_a_total_deadline_replacing_the_request_timeout(base: str) -> None:
    transport = AsyncTransport(base, request_timeout=1.0)
    git = AsyncGitService(transport)
    try:
        with pytest.raises(ClientTimeoutError):
            await git.pull("/repo", timeout=0.2)
        # Longer than the 1 s request timeout, inside its own 3 s deadline.
        await git.push("/repo", timeout=3)
    finally:
        await transport.close()
