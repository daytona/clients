# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Error responses always carry a message, the same as in the TypeScript SDK
(``tests/error-fidelity.test.ts``), and a request that got no response keeps
the network error as its cause."""

from __future__ import annotations

import socket
from typing import Optional

import pytest

from daytona.v2 import ConnectionError, NotFoundError
from daytona.v2._core import RequestSpec, error_from_response
from daytona.v2._transport import AsyncTransport

_JSON = "application/json"
_TEXT = "text/plain"


@pytest.mark.parametrize(
    ("status", "body", "content_type", "message"),
    [
        (503, b"", _JSON, "HTTP 503"),
        (500, b"", _TEXT, "HTTP 500"),
        (502, b"", None, "HTTP 502"),
        (502, b"  \n", _TEXT, "HTTP 502"),
        (400, b"null", _JSON, "null"),
        (400, b"[1,2]", _JSON, "[1,2]"),
        (400, b"{}", _JSON, "HTTP 400"),
        (400, b'{"message":""}', _JSON, "HTTP 400"),
        (400, b'{"message":"","error":"bad input"}', _JSON, "bad input"),
        (400, b'{"message":"   "}', _JSON, "HTTP 400"),
        (400, b'{"message":" ","error":"\\t"}', _JSON, "HTTP 400"),
        (400, b'{"message":"  ","error":"bad input"}', _JSON, "bad input"),
        (404, b'{"message":"no such sandbox","code":"NOT_FOUND"}', _JSON, "no such sandbox"),
        (400, b"plain words", _TEXT, "plain words"),
    ],
)
def test_error_messages_are_never_empty(
    status: int, body: bytes, content_type: Optional[str], message: str
) -> None:
    error = error_from_response(status, body, content_type=content_type)
    assert (error.message, error.status_code) == (message, status)


def test_an_envelope_with_an_empty_message_keeps_its_code() -> None:
    error = error_from_response(404, b'{"message":"","code":"NOT_FOUND"}', content_type=_JSON)
    assert isinstance(error, NotFoundError)
    assert (error.message, error.code) == ("HTTP 404", "NOT_FOUND")


def _refused_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.mark.anyio
async def test_a_refused_connection_keeps_the_network_error_as_its_cause() -> None:
    transport = AsyncTransport(_refused_url(), "dtn_secret_key_value", source="sdk-python-async")
    try:
        with pytest.raises(ConnectionError) as raised:
            await transport.send(RequestSpec("GET", "/x"))
    finally:
        await transport.close()
    assert raised.value.__cause__ is not None
    assert "dtn_secret_key_value" not in str(raised.value)
