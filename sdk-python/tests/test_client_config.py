# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client configuration: the environment, the request timeout and its meaning.

The TypeScript SDK's tests/client-config.test.ts asserts the same behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import threading
import time
from typing import AsyncIterator, Iterator, Optional

import anyio
import httpx
import pytest
import respx

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import AuthenticationError, BadRequestError, ClientTimeoutError, ConnectionError
from daytona.v2._core import RequestSpec, file_download_spec
from daytona.v2._transport import DEFAULT_REQUEST_TIMEOUT_SECONDS, AsyncTransport

BASE = "http://api.invalid"


def _v2(config: DaytonaConfig) -> object:
    return AsyncDaytona(config).v2


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("DAYTONA_API_KEY", "DAYTONA_JWT_TOKEN", "DAYTONA_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_ambient_credentials_are_trimmed(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DAYTONA_API_KEY", "  dtn_key \n")
    v2 = _v2(DaytonaConfig())
    assert v2._api_key == "dtn_key"  # type: ignore[attr-defined]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_ambient_api_key_is_unset(clean_env: pytest.MonkeyPatch, blank: str) -> None:
    clean_env.setenv("DAYTONA_API_KEY", blank)
    with pytest.raises(AuthenticationError, match="api_key or jwt_token is required"):
        _v2(DaytonaConfig())


def test_a_blank_ambient_api_key_does_not_collide_with_a_jwt(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DAYTONA_API_KEY", "  ")
    clean_env.setenv("DAYTONA_JWT_TOKEN", " jwt-token ")
    clean_env.setenv("DAYTONA_PROJECT_ID", " project-1 ")
    v2 = _v2(DaytonaConfig())
    assert v2._api_key is None  # type: ignore[attr-defined]
    assert v2._auth._jwt_token == "jwt-token"  # type: ignore[attr-defined]
    assert v2._auth._project_id == "project-1"  # type: ignore[attr-defined]


def test_a_blank_ambient_project_is_unset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DAYTONA_PROJECT_ID", "   ")
    with pytest.raises(BadRequestError, match="jwt_token requires project_id"):
        _v2(DaytonaConfig(jwt_token="jwt-token"))


def test_an_explicitly_empty_project_id_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DAYTONA_PROJECT_ID", "project-from-env")
    with pytest.raises(BadRequestError, match="project_id must be a non-empty string") as excinfo:
        _v2(DaytonaConfig(api_key="dtn_key", project_id=""))
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


@pytest.mark.parametrize("name", ["api_key", "jwt_token", "project_id"])
@pytest.mark.parametrize("value", [5, b"key", True, ["key"]])
def test_a_non_string_credential_field_is_rejected_like_typescript(
    clean_env: pytest.MonkeyPatch, name: str, value: object
) -> None:
    fields: dict[str, object] = {"api_key": "dtn_key"} if name == "project_id" else {}
    fields[name] = value
    with pytest.raises(BadRequestError, match=f"{name} must be a non-empty string when provided") as excinfo:
        _v2(DaytonaConfig(**fields))  # type: ignore[arg-type]
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


def test_explicit_values_are_used_as_given(clean_env: pytest.MonkeyPatch) -> None:
    v2 = _v2(DaytonaConfig(jwt_token=" jwt ", project_id=" p "))
    assert v2._auth._jwt_token == " jwt "  # type: ignore[attr-defined]
    assert v2._auth._project_id == " p "  # type: ignore[attr-defined]


def test_v2_never_reads_daytona_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_V2_API_URL")
    monkeypatch.setenv("DAYTONA_API_URL", "http://v0-sdk.invalid")
    v2 = _v2(DaytonaConfig(api_key="k"))
    assert v2._api_url == "https://api.daytona.com"  # type: ignore[attr-defined]


def test_the_request_timeout_defaults_to_sixty_seconds() -> None:
    assert DEFAULT_REQUEST_TIMEOUT_SECONDS == 60.0
    v2 = _v2(DaytonaConfig(api_key="k"))
    assert v2._api.request_timeout_seconds == 60.0  # type: ignore[attr-defined]
    assert v2._toolbox.request_timeout_seconds == 60.0  # type: ignore[attr-defined]
    assert AsyncTransport(BASE).request_timeout_seconds == 60.0


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True, "60"])
def test_an_invalid_request_timeout_is_rejected(bad: object) -> None:
    with pytest.raises(BadRequestError, match="request_timeout must be a non-negative number") as excinfo:
        _v2(DaytonaConfig(api_key="k", request_timeout=bad))  # type: ignore[arg-type]
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


def _trickle(chunks: int, every: float) -> httpx.Response:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(chunks):
            await asyncio.sleep(every)
            yield b"x"

    return httpx.Response(200, content=body())


async def _late(delay: float) -> httpx.Response:
    await asyncio.sleep(delay)
    return httpx.Response(200, json={"ok": True})


@pytest.mark.anyio
@respx.mock
async def test_the_request_timeout_bounds_the_whole_ordinary_request() -> None:
    # Every chunk arrives well inside the timeout, so a per-read timeout would
    # never fire: the request timeout is the total time.
    respx.get(f"{BASE}/slow-body").mock(side_effect=lambda _request: _trickle(chunks=8, every=0.05))
    transport = AsyncTransport(BASE, "k", request_timeout=0.15)
    # The body finishes in 0.4 s: only a total bound raises.
    with pytest.raises(ClientTimeoutError):
        await transport.send(RequestSpec("GET", "/slow-body"))
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_the_request_timeout_bounds_the_raw_request_path() -> None:
    respx.put(f"{BASE}/slow").mock(side_effect=lambda _request: _late(0.4))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    with pytest.raises(ClientTimeoutError):
        await transport.put("/slow")
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_zero_disables_the_request_timeout() -> None:
    respx.get(f"{BASE}/slow-body").mock(side_effect=lambda _request: _trickle(chunks=4, every=0.05))
    transport = AsyncTransport(BASE, "k", request_timeout=0)
    assert await transport.send(RequestSpec("GET", "/slow-body")) == b"xxxx"
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_waits_with_their_own_budget_are_not_cut_by_the_request_timeout() -> None:
    respx.get(f"{BASE}/processes/p/wait").mock(side_effect=lambda _request: _late(0.3))
    respx.get(f"{BASE}/hold").mock(side_effect=lambda _request: _late(0.3))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    assert await transport.send(RequestSpec("GET", "/processes/p/wait")) == {"ok": True}
    assert await transport.send(RequestSpec("GET", "/hold", timeout=5.0)) == {"ok": True}
    assert await transport.send(RequestSpec("GET", "/hold", unbounded_read=True)) == {"ok": True}
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_a_per_call_timeout_is_a_total_bound_like_the_request_timeout() -> None:
    # It replaces the request timeout for its request, with the same meaning:
    # every chunk arrives well inside 0.15 s, the body takes 0.4 s.
    respx.get(f"{BASE}/slow-body").mock(side_effect=lambda _request: _trickle(chunks=8, every=0.05))
    transport = AsyncTransport(BASE, "k", request_timeout=0)
    with pytest.raises(ClientTimeoutError) as excinfo:
        await transport.send(RequestSpec("GET", "/slow-body", timeout=0.15))
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    with pytest.raises(ClientTimeoutError):
        await transport.send(RequestSpec("GET", "/slow-body", timeout=0.15, unbounded_read=True))
    with pytest.raises(ClientTimeoutError):
        await transport.request("GET", "/slow-body", timeout=0.15)
    # 0 and a value above the timer maximum mean no limit, as for the request timeout.
    assert await transport.send(RequestSpec("GET", "/slow-body", timeout=0)) == b"xxxxxxxx"
    assert await transport.send(RequestSpec("GET", "/slow-body", timeout=3_000_000.0)) == b"xxxxxxxx"
    await transport.close()


def _exchange_then(exchange_delay: float, thing_delay: float):
    async def respond(request: httpx.Request) -> httpx.Response:
        exchange = request.url.path == "/auth/token"
        await asyncio.sleep(exchange_delay if exchange else thing_delay)
        return httpx.Response(
            200, json={"access_token": "t", "expires_in": 3600} if exchange else {"ok": True}
        )

    return respond


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("request_timeout", "per_call", "exchange_delay", "thing_delay", "succeeds"),
    [
        # A shorter per-call timeout covers the exchange.
        (5.0, 0.1, 0.4, 0.0, False),
        # A longer one does not lengthen the exchange past the request timeout,
        # which bounds the exchange request itself (concurrent calls share it).
        (0.2, 5.0, 1.0, 0.0, False),
        # A longer one lets the request itself outlast the request timeout.
        (0.5, 5.0, 0.05, 0.8, True),
    ],
)
async def test_a_per_call_timeout_starts_before_credential_resolution(
    monkeypatch: pytest.MonkeyPatch,
    request_timeout: float,
    per_call: float,
    exchange_delay: float,
    thing_delay: float,
    succeeds: bool,
) -> None:
    # The TypeScript SDK's client-config.test.ts checks the same three cases.
    monkeypatch.setenv("DAYTONA_V2_API_URL", BASE)
    with respx.mock(assert_all_called=False) as router:
        router.route(url__startswith=BASE).mock(side_effect=_exchange_then(exchange_delay, thing_delay))
        client = AsyncDaytona(
            DaytonaConfig(jwt_token="jwt", project_id="p", request_timeout=request_timeout)
        ).v2
        try:
            call = client._api.send(RequestSpec("GET", "/thing", timeout=per_call))  # type: ignore[attr-defined]
            if succeeds:
                assert await call == {"ok": True}
            else:
                with pytest.raises(ClientTimeoutError):
                    await call
        finally:
            await client.close()


@pytest.mark.anyio
async def test_image_submissions_are_bounded_by_the_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # POST /images is an ordinary request (no hold): build, pull and
    # create_from_build submit it under the request timeout.
    from daytona.v2 import ImageBuilder

    monkeypatch.setenv("DAYTONA_V2_API_URL", BASE)
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/images").mock(side_effect=lambda _request: _late(0.4))
        client = AsyncDaytona(DaytonaConfig(api_key="k", request_timeout=0.1)).v2
        try:
            with pytest.raises(ClientTimeoutError):
                await client.images.build(ImageBuilder.from_dockerfile("FROM alpine"))
            with pytest.raises(ClientTimeoutError):
                await client.images.pull("alpine", wait=False)
            with pytest.raises(ClientTimeoutError):
                await client.images.pull("alpine")
        finally:
            await client.close()


@pytest.mark.anyio
@respx.mock
async def test_a_transfer_per_call_timeout_replaces_its_idle_bound_never_a_total_deadline() -> None:
    # 6 bytes, one every 50 ms (300 ms in all): a 0.1 s per-call timeout is
    # no total deadline (test_transfer_bounds.py shows it is the idle bound).
    respx.get(f"{BASE}/files/download").mock(side_effect=lambda _request: _trickle(chunks=6, every=0.05))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    assert await transport.raw(file_download_spec("/a", timeout=0.1)) == b"xxxxxx"
    response = await transport.request("GET", "/files/download", transfer=True, timeout=0.1)
    assert response.content == b"xxxxxx"
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_file_transfers_are_not_cut_by_the_request_timeout() -> None:
    respx.get(f"{BASE}/files/download").mock(side_effect=lambda _request: _trickle(chunks=6, every=0.05))
    respx.post(f"{BASE}/files/upload").mock(side_effect=lambda _request: _late(0.3))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    assert await transport.raw(file_download_spec("/a", timeout=None)) == b"xxxxxx"
    response = await transport.request("POST", "/files/upload", transfer=True, content=b"data")
    assert response.status_code == 200
    stream = await transport.open_stream(file_download_spec("/a", timeout=None))
    assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxxxxx"
    await stream.close()
    await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_streams_are_bounded_until_their_headers_only() -> None:
    respx.get(f"{BASE}/logs").mock(side_effect=lambda _request: _trickle(chunks=6, every=0.05))
    respx.get(f"{BASE}/stalled").mock(side_effect=lambda _request: _late(0.4))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    stream = await transport.open_stream(RequestSpec("GET", "/logs"))
    assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxxxxx"
    await stream.close()
    with pytest.raises(ClientTimeoutError):
        await transport.open_stream(RequestSpec("GET", "/stalled"))
    await transport.close()


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("opener", ["open_stream", "open_exclusive_stream"])
async def test_a_per_call_timeout_bounds_opening_a_stream_in_place_of_the_request_timeout(
    opener: str,
) -> None:
    # Headers after 0.4 s, then a body over 0.3 s.
    async def late_stream(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.4)
        return _trickle(chunks=6, every=0.05)

    respx.get(f"{BASE}/logs").mock(side_effect=late_stream)
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    open_ = getattr(transport, opener)
    # Longer than the request timeout: it replaces it, and the body is never bounded.
    stream = await open_(RequestSpec("GET", "/logs", timeout=5.0))
    assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxxxxx"
    await stream.close()
    # 0 means no limit, as for the request timeout.
    stream = await open_(RequestSpec("GET", "/logs", timeout=0))
    await stream.close()
    # Shorter than the headers: a total bound on opening.
    roomy = AsyncTransport(BASE, "k", request_timeout=10)
    with pytest.raises(ClientTimeoutError):
        await getattr(roomy, opener)(RequestSpec("GET", "/logs", timeout=0.15))
    # Without one, the request timeout bounds opening the exclusive stream too.
    with pytest.raises(ClientTimeoutError):
        await open_(RequestSpec("GET", "/logs"))
    await transport.close()
    await roomy.close()


@contextlib.contextmanager
def _gapped_body_server(gap: float, chunks: int) -> Iterator[str]:
    """A real HTTP server: headers at once, then ``chunks`` bytes ``gap`` seconds apart.

    respx never applies httpx's per-read timeouts; a real socket does.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(chunks))
            self.end_headers()
            self.wfile.flush()
            try:
                for _ in range(chunks):
                    time.sleep(gap)
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
async def test_a_stream_body_outlasts_a_per_call_timeout_on_a_real_socket() -> None:
    # Headers at once, then 3 bytes 0.3 s apart under a 0.15 s per-call
    # timeout: every gap is longer than the timeout.
    with _gapped_body_server(gap=0.3, chunks=3) as base:
        transport = AsyncTransport(base, "k", request_timeout=10)
        try:
            # open_stream: the per-call timeout bounds opening it only; the
            # body has no bound at all.
            stream = await transport.open_stream(RequestSpec("GET", "/logs", timeout=0.15))
            assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxx"
            await stream.close()
            # open_exclusive_stream: a live stream (unbounded_read) is not
            # bounded either...
            stream = await transport.open_exclusive_stream(
                RequestSpec("GET", "/logs", timeout=0.15, unbounded_read=True)
            )
            assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxx"
            await stream.close()
            # ...while a finite body keeps an idle-read bound of that timeout.
            stream = await transport.open_exclusive_stream(RequestSpec("GET", "/logs", timeout=0.15))
            try:
                with pytest.raises(ClientTimeoutError):
                    b"".join([chunk async for chunk in stream.iter_bytes()])
            finally:
                await stream.close()
            # With no per-call timeout the read bound is cleared: the request
            # timeout (here 0.15 s) bounds opening only, not the gaps after.
            short = AsyncTransport(base, "k", request_timeout=0.15)
            try:
                stream = await short.open_exclusive_stream(RequestSpec("GET", "/logs"))
                assert b"".join([chunk async for chunk in stream.iter_bytes()]) == b"xxx"
                await stream.close()
            finally:
                await short.close()
        finally:
            await transport.close()


def test_the_synchronous_client_enforces_the_same_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # The synchronous surface bridges onto the async transport, so the
    # request timeout means the same there.
    monkeypatch.setenv("DAYTONA_V2_API_URL", BASE)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/sandboxes.*").mock(
            side_effect=lambda _request: _trickle(chunks=8, every=0.05)
        )
        with (
            Daytona(DaytonaConfig(api_key="k", request_timeout=0.15)) as client,
            pytest.raises(ClientTimeoutError),
        ):
            client.v2.list()


_TIMEOUT_SHAPE = ("operation timed out", 408, "REQUEST_TIMEOUT", "SDK")


def _shape(error: ClientTimeoutError) -> tuple[object, ...]:
    return (error.message, error.status_code, error.code, error.source)


@pytest.mark.anyio
@respx.mock
async def test_the_client_timeout_has_one_shape_on_every_path() -> None:
    respx.get(f"{BASE}/slow").mock(side_effect=lambda _request: _late(0.4))
    respx.get(f"{BASE}/slow-body").mock(side_effect=lambda _request: _trickle(chunks=8, every=0.05))
    respx.get(f"{BASE}/stalled").mock(side_effect=lambda _request: _late(0.4))
    transport = AsyncTransport(BASE, "k", request_timeout=0.1)
    with pytest.raises(ClientTimeoutError) as ordinary:
        await transport.send(RequestSpec("GET", "/slow"))
    with pytest.raises(ClientTimeoutError) as body:
        await transport.send(RequestSpec("GET", "/slow-body"))
    with pytest.raises(ClientTimeoutError) as stream:
        await transport.open_stream(RequestSpec("GET", "/stalled"))
    with pytest.raises(ClientTimeoutError) as raw:
        await transport.get("/slow")
    for caught in (ordinary, body, stream, raw):
        assert _shape(caught.value) == _TIMEOUT_SHAPE
    assert _shape(ClientTimeoutError()) == _TIMEOUT_SHAPE
    refused = ConnectionError("refused")
    assert (refused.source, refused.status_code, refused.code) == ("SDK", None, None)
    await transport.close()


@pytest.mark.parametrize("huge", [2_147_484, 30 * 24 * 3600, 10**400, 1e300])
def test_a_request_timeout_above_the_timer_maximum_means_no_limit(huge: float) -> None:
    v2 = _v2(DaytonaConfig(api_key="k", request_timeout=huge))
    assert v2._api.request_timeout_seconds == 0  # type: ignore[attr-defined]
    assert (
        _v2(DaytonaConfig(api_key="k", request_timeout=2_147_483))._api.request_timeout_seconds == 2_147_483
    )  # type: ignore[attr-defined]


@pytest.mark.parametrize("field", ["reconcile_interval", "idle_close"])
@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), "5", True])
def test_invalid_events_durations_are_rejected(field: str, bad: object) -> None:
    from daytona.v2 import SandboxEventsConfig

    events = SandboxEventsConfig(**{field: bad})  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match=f"events.{field} must be a non-negative number") as excinfo:
        _v2(DaytonaConfig(api_key="k", events=events))
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


def test_missing_credentials_carry_the_catalog_status_and_code(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthenticationError) as excinfo:
        _v2(DaytonaConfig())
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        401,
        "UNAUTHORIZED",
        "SDK",
    )


class _SlowRefreshingAuth:
    """A bearer source whose resolution and 401 refresh each take ``delay`` seconds."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def bearer(self) -> Optional[str]:
        await asyncio.sleep(self.delay)
        return "stale"

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
        await asyncio.sleep(self.delay)
        return "fresh"


async def _unauthorized_then_ok(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(0.05)
    if request.headers.get("authorization") == "Bearer stale":
        return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "expired"})
    return httpx.Response(200, json={"ok": True})


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("path", ["send", "request", "open_stream"])
async def test_the_bearer_a_401_refresh_and_the_retry_share_one_deadline(path: str) -> None:
    # Each step fits the 0.15 s timeout on its own; together (about 0.26 s)
    # they do not, so a fresh window per step would let the call succeed.
    respx.get(f"{BASE}/thing").mock(side_effect=_unauthorized_then_ok)
    transport = AsyncTransport(BASE, None, request_timeout=0.15)
    transport.auth_provider = _SlowRefreshingAuth(0.08)
    # The rejection alone proves it (a fresh window per step would succeed);
    # no wall-clock upper bound, which a loaded runner could break.
    with pytest.raises(ClientTimeoutError):
        if path == "send":
            await transport.send(RequestSpec("GET", "/thing"))
        elif path == "request":
            await transport.get("/thing")
        else:
            await transport.open_stream(RequestSpec("GET", "/thing"))
    await transport.close()
    # The same steps complete under a budget far above them.
    roomy = AsyncTransport(BASE, None, request_timeout=10)
    roomy.auth_provider = _SlowRefreshingAuth(0.08)
    assert await roomy.send(RequestSpec("GET", "/thing")) == {"ok": True}
    await roomy.close()


@pytest.mark.anyio
async def test_the_hub_auth_keeps_the_client_request_timeout() -> None:
    client = AsyncDaytona(DaytonaConfig(jwt_token="jwt", project_id="p", request_timeout=7)).v2
    try:
        hub = client._acquire_hub()  # type: ignore[attr-defined]
        assert hub is not None
        assert hub._api._auth._request_timeout == 7
    finally:
        await client.close()


@pytest.mark.parametrize("huge", [10**400, 1e300, 3_000_000.0])
def test_oversized_events_durations_clamp_to_the_timer_maximum_like_typescript(huge: float) -> None:
    from daytona.v2 import SandboxEventsConfig

    v2 = _v2(DaytonaConfig(api_key="k", events=SandboxEventsConfig(reconcile_interval=huge, idle_close=huge)))
    events = v2._hub_events_config  # type: ignore[attr-defined]
    # The TypeScript hub's ceilings: Math.floor(2**31 - 1) / 1.1 ms and 2**31 - 1 ms.
    assert events.reconcile_interval == 1_952_257.86
    assert events.idle_close == 2_147_483.647
    small = _v2(DaytonaConfig(api_key="k", events=SandboxEventsConfig(reconcile_interval=5, idle_close=7)))
    assert (small._hub_events_config.reconcile_interval, small._hub_events_config.idle_close) == (5, 7)  # type: ignore[attr-defined]


class _StalledAuth:
    """A bearer source that never answers (``step="bearer"``) or answers the
    401 refresh never (``step="refresh"``)."""

    def __init__(self, step: str) -> None:
        self.step = step

    async def bearer(self) -> Optional[str]:
        if self.step == "bearer":
            await asyncio.sleep(3600)
        return "stale"

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
        await asyncio.sleep(3600)
        return "fresh"


async def _unauthorized_for_stale(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") == "Bearer stale":
        return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "expired"})
    return httpx.Response(200, json={"ok": True})


# A transfer has no total deadline, but resolving its bearer and a 401 refresh
# are each bounded by its idle bound, the request timeout, as in TypeScript: a
# stalled credential source cannot hang a transfer.
@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("step", ["bearer", "refresh"])
@pytest.mark.parametrize("path", ["send", "request", "open_stream"])
async def test_a_transfer_bounds_a_stalled_credential_by_its_idle_bound(path: str, step: str) -> None:
    respx.get(f"{BASE}/thing").mock(side_effect=_unauthorized_for_stale)
    transport = AsyncTransport(BASE, None, request_timeout=0.2)
    transport.auth_provider = _StalledAuth(step)
    try:
        with pytest.raises(ClientTimeoutError):
            with anyio.fail_after(10):  # a hang fails the test, it does not stall the suite
                if path == "send":
                    await transport.send(RequestSpec("GET", "/thing", transfer=True))
                elif path == "request":
                    await transport.get("/thing", transfer=True)
                else:
                    await transport.open_stream(RequestSpec("GET", "/thing", transfer=True))
    finally:
        await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_a_transfer_credential_bound_is_none_with_a_request_timeout_of_zero() -> None:
    class SlowAuth:
        async def bearer(self) -> Optional[str]:
            await asyncio.sleep(0.3)
            return "fresh"

        async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
            return None

    respx.get(f"{BASE}/thing").mock(side_effect=_unauthorized_for_stale)
    transport = AsyncTransport(BASE, None, request_timeout=0)
    transport.auth_provider = SlowAuth()  # type: ignore[assignment]
    try:
        assert (await transport.get("/thing", transfer=True)).json() == {"ok": True}
    finally:
        await transport.close()


def test_a_sync_transfer_bounds_a_stalled_credential_by_its_idle_bound() -> None:
    # The synchronous client runs on the same async transport, through the bridge.
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.filesystem import FileSystemService
    from daytona.v2.filesystem import AsyncFileSystemService

    bridge = SyncBridge()
    transport = AsyncTransport(
        BASE,
        None,
        request_timeout=0.2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    transport.auth_provider = _StalledAuth("bearer")
    worker: Optional[threading.Thread] = None
    try:
        fs = FileSystemService(AsyncFileSystemService(transport), bridge)
        outcome: list[BaseException] = []

        def read() -> None:
            try:
                fs.read_bytes("/a")
            except BaseException as error:
                outcome.append(error)

        # On a worker thread, so a hang fails the test instead of stalling the suite.
        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        worker.join(10)
        assert not worker.is_alive(), "the transfer hung on its stalled credential"
        assert len(outcome) == 1 and isinstance(outcome[0], ClientTimeoutError)
    finally:
        # A hung transfer would keep the bridge busy: leave it to the daemon thread.
        if worker is None or not worker.is_alive():
            bridge.close(transport.close)
