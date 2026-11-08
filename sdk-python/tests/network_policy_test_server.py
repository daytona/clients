# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Deque

from typing_extensions import Self

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
ResponseBody = JsonValue | str | bytes


@dataclass(frozen=True)
class PlannedResponse:
    status: int
    body: ResponseBody
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


class FakeApi:
    def __init__(self, responses: list[PlannedResponse]) -> None:
        self._responses: Deque[PlannedResponse] = deque(responses)
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def do_PUT(self) -> None:
                self._handle()

            def do_PATCH(self) -> None:
                self._handle()

            def log_message(self, format: str, *args: object) -> None:
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length)
                with outer._lock:
                    outer.requests.append(
                        RecordedRequest(
                            method=self.command,
                            path=self.path,
                            headers={key: value for key, value in self.headers.items()},
                            body=body,
                        )
                    )
                    if not outer._responses:
                        raise AssertionError(f"unexpected request: {self.command} {self.path}")
                    response = outer._responses.popleft()
                encoded = _encode_response_body(response.body)
                self.send_response(response.status)
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(encoded)))
                if "Content-Type" not in response.headers:
                    self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(encoded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def encode_response_body(body: ResponseBody) -> bytes:
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body).encode("utf-8")


def json_body(request: RecordedRequest) -> JsonValue:
    return json.loads(request.body.decode("utf-8"))


_encode_response_body = encode_response_body
