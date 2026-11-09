# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import contextlib
import importlib.util
import json
import socket
from http.cookiejar import CookieJar, DefaultCookiePolicy
from importlib.metadata import PackageNotFoundError, version
from typing import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Callable,
    Final,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Protocol,
    TypeGuard,
    Union,
)
from urllib.parse import urlsplit

import anyio
import httpx
from typing_extensions import TypedDict, Unpack

from ._auth import ProjectTokenAuth
from ._body_limit import (
    ACCEPT_ENCODING,
    DEFAULT_MAX_RESPONSE_BYTES,
    decoded_chunks,
    read_body,
    read_body_sync,
    resolve_limit,
    send_buffered,
    send_buffered_sync,
)
from ._core import MultipartPart, RequestSpec, error_from_response, is_json_media_type, parse_json_bytes
from ._otel import OpenTelemetrySupport
from .errors import ClientTimeoutError, ConnectionError, create_error
from .types import JsonValue, QueryParamScalar, QueryParamValue

_HTTP2_AVAILABLE = importlib.util.find_spec("h2") is not None


def _installed_version() -> str:
    try:
        return version("daytona")
    except PackageNotFoundError:
        return "unknown"


SDK_VERSION = _installed_version()


def client_identity_headers(source: str) -> dict[str, str]:
    """Client-identity headers shared with the v0 Daytona SDKs, for usage tracking."""
    return {
        "X-Daytona-Source": source,
        "X-Daytona-SDK-Version": SDK_VERSION,
        "User-Agent": f"{source}/{SDK_VERSION}",
    }


_STREAM_RELEASE_GRACE_SECONDS = 5.0

#: The client's default request timeout (``request_timeout``), in seconds: the
#: total time one ordinary request may take, from sending it to reading the
#: last byte of the response. ``0`` disables it. Requests governed by another
#: budget are not cut by it: waits and long polls carry their own deadline,
#: streams are bounded only until their response headers arrive, and file
#: transfers have no total bound: the request timeout bounds each of their idle
#: phases instead (connecting, every write and read gap).
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 60.0
# The largest request or per-call timeout that is still a bound, in seconds
# (about 24.8 days): the TypeScript SDK's timers cannot arm a longer one, so
# both SDKs read a larger value as no limit, like 0.
_MAX_BOUNDED_TIMEOUT_SECONDS: Final = 2_147_483.647
_RELATIVE_PATH_ERROR = "absolute request URLs are not allowed; pass a relative SDK path"


class _SpanFinisher(Protocol):
    def finish(self, *, status_code: int | None = None, error: BaseException | None = None) -> None: ...


class _BlockAllCookiesPolicy(DefaultCookiePolicy):
    def set_ok(self, cookie, request) -> bool:  # type: ignore[override]
        return False

    def return_ok(self, cookie, request) -> bool:  # type: ignore[override]
        return False

    def domain_return_ok(self, domain, request) -> bool:  # type: ignore[override]
        return False

    def path_return_ok(self, path, request) -> bool:  # type: ignore[override]
        return False


def _cookie_jar() -> CookieJar:
    return CookieJar(policy=_BlockAllCookiesPolicy())


def _refuse_cookies(client: Union[httpx.Client, httpx.AsyncClient]) -> None:
    # Also covers an injected client: httpx rebuilds the jar with its DEFAULT
    # policy whenever cookies arrive as an httpx.Cookies rather than a raw
    # CookieJar, silently dropping the block-all policy.
    client.cookies.jar.set_policy(_BlockAllCookiesPolicy())


class SandboxTokenAuth(Protocol):
    """Per-request bearer resolution for a sandbox service transport.

    Attached by a sandbox handle to its scoped toolbox transport.
    ``bearer()`` resolves the token to send (minting
    or refreshing a short-lived sandbox access token when needed), and
    ``on_unauthorized(failed_token)`` is called once after a 401 — returning a
    fresh token retries the request exactly once, returning ``None`` surfaces
    the original error. Transports without a provider keep their static token.
    """

    async def bearer(self) -> Optional[str]: ...

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]: ...


class _ResponseStreamContext(Protocol):
    def __enter__(self) -> httpx.Response: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool | None: ...


class _AsyncResponseStreamContext(Protocol):
    async def __aenter__(self) -> httpx.Response: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool | None: ...


class SyncResponseStream:
    def __init__(self, stream: _ResponseStreamContext, response: httpx.Response) -> None:
        self._stream = stream
        self._response = response
        self._closed = False

    def iter_lines(self) -> Iterator[str]:
        try:
            yield from self._response.iter_lines()
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # httpx's close() releases the connection but does not interrupt a
        # reader blocked in recv() on another thread; without this the reader
        # only wakes when the server closes. Shut the socket down first so the
        # reader observes EOF immediately. Best effort: mock transports expose
        # no network stream.
        network_stream = self._response.extensions.get("network_stream")
        if network_stream is not None:
            try:
                sock = network_stream.get_extra_info("socket")
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._stream.__exit__(None, None, None)


class _RequestKwargs(TypedDict, total=False):
    params: Mapping[str, QueryParamValue] | None
    json: JsonValue | None
    content: str | bytes | Iterable[bytes] | AsyncIterable[bytes] | None
    data: str | bytes | Mapping[str, str] | None
    files: Mapping[str, MultipartPart] | None
    headers: Mapping[str, str] | None
    timeout: float | httpx.Timeout


_ReplayableContent = str | bytes | Iterable[bytes] | AsyncIterable[bytes]
_ReplayableContentFactory = Callable[[int], _ReplayableContent]


def _bind_auth_client(auth: ProjectTokenAuth | None, client: httpx.AsyncClient) -> None:
    if auth is None:
        return
    bind_client = getattr(auth, "bind_client", None)
    if callable(bind_client):
        bind_client(client)


class SyncTransport:
    """A minimal blocking transport for tests and internal tooling (not public API).

    The SDK clients do not use it: the synchronous surface bridges onto
    :class:`AsyncTransport`, which enforces the request timeout as a total-time
    bound. Here ``request_timeout`` is httpx's per-phase timeout.
    """

    def __init__(
        self,
        base_url: Optional[str],
        token: Optional[str] = None,
        *,
        source: str = "sdk-python",
        request_timeout: Optional[float] = None,
        connection_pool_maxsize: Optional[int] = None,
        client: Optional[httpx.Client] = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self._max_response_bytes = max_response_bytes
        self._source = source
        self._request_timeout_seconds = (
            request_timeout if request_timeout is not None else DEFAULT_REQUEST_TIMEOUT_SECONDS
        )
        self._connection_pool_maxsize = connection_pool_maxsize
        self._owns_client = client is None
        if client is None:
            kwargs: dict[str, object] = {
                "headers": {**client_identity_headers(source), "Accept-Encoding": ACCEPT_ENCODING},
                "timeout": _client_timeout(self._request_timeout_seconds),
                "http2": _HTTP2_AVAILABLE,
                "cookies": _cookie_jar(),
            }
            limits = _client_limits(connection_pool_maxsize)
            if limits is not None:
                kwargs["limits"] = limits
            self._client = httpx.Client(**kwargs)
        else:
            self._client = client
        _refuse_cookies(self._client)

    @classmethod
    def shared(
        cls,
        *,
        source: str = "sdk-python",
        request_timeout: Optional[float] = None,
        connection_pool_maxsize: Optional[int] = None,
    ) -> SyncTransport:
        return cls(
            None,
            None,
            source=source,
            request_timeout=request_timeout,
            connection_pool_maxsize=connection_pool_maxsize,
        )

    def scoped(self, base_url: str, token: Optional[str] = None) -> SyncTransport:
        return SyncTransport(
            base_url,
            token,
            source=self._source,
            request_timeout=self._request_timeout_seconds,
            connection_pool_maxsize=self._connection_pool_maxsize,
            client=self._client,
            max_response_bytes=self._max_response_bytes,
        )

    @property
    def request_timeout_seconds(self) -> float:
        return self._request_timeout_seconds

    def send(self, spec: RequestSpec) -> Union[JsonValue, bytes]:
        try:
            kwargs = _request_kwargs(spec, self._request_timeout_seconds)
            kwargs["headers"] = _request_headers(self._source, self.token, spec.headers)
            response = send_buffered_sync(
                self._client,
                self._client.build_request(spec.method, _request_url(self.base_url, spec.path), **kwargs),
                resolve_limit(spec.max_response_bytes, self._max_response_bytes),
            )
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc
        if spec.on_headers is not None and response.status_code < 400:
            spec.on_headers(response.headers)
        return _decode_response(response)

    def raw(self, spec: RequestSpec) -> bytes:
        value = self.send(spec)
        if isinstance(value, bytes):
            return value
        return json.dumps(value).encode("utf-8")

    def stream_lines(self, spec: RequestSpec) -> Iterator[str]:
        stream = self.open_stream(spec)
        try:
            yield from stream.iter_lines()
        finally:
            stream.close()

    def open_stream(self, spec: RequestSpec) -> SyncResponseStream:
        try:
            kwargs = _request_kwargs(spec, self._request_timeout_seconds)
            kwargs["timeout"] = _stream_timeout(spec, self._request_timeout_seconds)
            kwargs["headers"] = _request_headers(self._source, self.token, spec.headers)
            stream = self._client.stream(
                spec.method,
                _request_url(self.base_url, spec.path),
                **kwargs,
            )
            response = stream.__enter__()
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc
        if response.status_code >= 400:
            try:
                body = read_body_sync(
                    response, resolve_limit(spec.max_response_bytes, self._max_response_bytes)
                )
            except httpx.TimeoutException as exc:
                raise ClientTimeoutError() from exc
            except httpx.HTTPError as exc:
                raise ConnectionError(str(exc)) from exc
            finally:
                stream.__exit__(None, None, None)
            raise error_from_response(
                response.status_code, body, content_type=response.headers.get("content-type")
            )
        return SyncResponseStream(stream, response)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request(self, method: str, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        """Return the raw httpx.Response after envelope-error handling.

        Contract: any status >= 400 raises the typed SDK error; callers that
        need undecoded bodies (e.g. multipart bulk reads) rely on this.
        """
        try:
            kwargs["headers"] = _request_headers(self._source, self.token, kwargs.get("headers"))
            response = send_buffered_sync(
                self._client,
                self._client.build_request(method, _request_url(self.base_url, path), **kwargs),
                self._max_response_bytes,
            )
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc
        if response.status_code >= 400:
            raise error_from_response(
                response.status_code, response.content, content_type=response.headers.get("content-type")
            )
        return response

    def get(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)


class AsyncResponseStream:
    def __init__(
        self,
        stream: _AsyncResponseStreamContext,
        response: httpx.Response,
        *,
        owned_client: httpx.AsyncClient | None = None,
        on_close: Callable[[AsyncResponseStream], None] | None = None,
        span: _SpanFinisher | None = None,
    ) -> None:
        self._stream = stream
        self._response = response
        self._closed = False
        # True once a release has completed. Separate from _closed, which an
        # owned stream sets before its release, so that a concurrent close()
        # waits for that release instead of returning while it runs.
        self._released = False
        self._close_lock = anyio.Lock()
        # A client created for this response alone (open_exclusive_stream).
        # Closing the stream closes it; a borrowed client is never closed here.
        self._owned_client = owned_client
        self._on_close = on_close
        self._span = span

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    async def iter_bytes(self) -> AsyncGenerator[bytes, None]:
        """The decoded body as it arrives; unlike iter_lines, never buffers an
        unbounded line. A gzip or deflate body is decoded here in bounded
        steps (``decoded_chunks``), never by httpx, whose decoders can expand
        one small chunk without bound; another encoding is refused."""
        try:
            async for chunk in decoded_chunks(self._response):
                yield chunk
            self._finish_span_ok()
        except httpx.TimeoutException as exc:
            error = ClientTimeoutError()
            self._finish_span_error(error)
            raise error from exc
        except httpx.HTTPError as exc:
            error = ConnectionError(str(exc))
            self._finish_span_error(error)
            raise error from exc
        except GeneratorExit:
            # The consumer stopped reading early: not a failed request.
            self._finish_span_ok()
            raise
        except BaseException as exc:
            if isinstance(exc, Exception) and self._closed:
                error = ConnectionError("response stream closed")
                self._finish_span_error(error)
                raise error from exc
            self._finish_span_error(exc)
            raise

    async def iter_lines(self) -> AsyncIterator[str]:
        try:
            async for line in self._response.aiter_lines():
                yield line
            self._finish_span_ok()
        except httpx.TimeoutException as exc:
            error = ClientTimeoutError()
            self._finish_span_error(error)
            raise error from exc
        except httpx.HTTPError as exc:
            error = ConnectionError(str(exc))
            self._finish_span_error(error)
            raise error from exc
        except GeneratorExit:
            # The consumer stopped reading early: not a failed request.
            self._finish_span_ok()
            raise
        except BaseException as exc:
            if isinstance(exc, Exception) and self._closed:
                error = ConnectionError("response stream closed")
                self._finish_span_error(error)
                raise error from exc
            self._finish_span_error(exc)
            raise

    async def close(self) -> None:
        if self._released:
            return
        # Single-flight: a second close() (a stop() racing the reader's own
        # close) waits for the release in progress instead of re-entering the
        # response's context manager, which raises RuntimeError. Shielded like
        # the release itself (each bounded by its grace window), so a
        # cancelled reader still reaches the release.
        with anyio.CancelScope(shield=True):
            async with self._close_lock:
                if not self._released:
                    await self._close_once()

    async def _close_once(self) -> None:
        if self._owned_client is not None:
            await self._close_owned()
            return
        # Deliberately NOT the sync path's socket shutdown. That exists to wake a
        # thread parked in recv(), and it costs the whole connection: on a
        # negotiated HTTP/2 link it tears down sibling streams too.
        # Async has no parked thread — the reader is a suspended coroutine that
        # awaits this finalizer — so releasing the response is enough, and one
        # sandbox ending its stream cannot disconnect the others multiplexed
        # onto the shared client.
        #
        # Shielded because this runs from the reader's `finally`: when the
        # reader is cancelled (Ctrl-C, a task group unwinding), an unshielded
        # await here is itself cancelled and the response is never released,
        # leaking the connection back into the shared pool still open.
        with anyio.move_on_after(_STREAM_RELEASE_GRACE_SECONDS, shield=True) as scope:
            await self._stream.__aexit__(None, None, None)
        # Only a completed release counts as closed. A release that raised or
        # ran out its grace window leaves the stream re-closable, so a later
        # attempt (the reader's finally, a caller's close) can still free it.
        self._closed = not scope.cancel_called
        self._released = self._closed
        if self._closed:
            self._finish_span_ok()
            self._notify_closed()

    async def _close_owned(self) -> None:
        # The response owns its whole client, so nothing else can share the
        # connection: closing the client after the response is always safe and
        # also frees a release that ran out of its grace window. Marked closed
        # first so a reader woken by the teardown reports closure, not an
        # httpcore detail. Shielded for the same reason as the shared path.
        self._closed = True
        try:
            with anyio.move_on_after(_STREAM_RELEASE_GRACE_SECONDS, shield=True):
                try:
                    await self._stream.__aexit__(None, None, None)
                finally:
                    if self._owned_client is not None:
                        await self._owned_client.aclose()
        finally:
            self._released = True
            self._finish_span_ok()
            self._notify_closed()

    def _notify_closed(self) -> None:
        if self._on_close is not None:
            on_close, self._on_close = self._on_close, None
            on_close(self)

    def _finish_span_ok(self) -> None:
        if self._span is not None:
            self._span.finish(status_code=self._response.status_code)

    def _finish_span_error(self, error: BaseException) -> None:
        if self._span is not None:
            self._span.finish(status_code=self._response.status_code, error=error)


class _StreamOwners:
    """Root-transport lifetime of exclusive streams, shared by every scope.

    A scoped transport never owns the pool, so closing it closes nothing; the
    root's close() closes every exclusive stream still open under it and
    refuses new ones. Loop-confined: no lock is needed.
    """

    def __init__(self) -> None:
        self._streams: set[AsyncResponseStream] = set()
        self._closed = False

    def ensure_open(self) -> None:
        if self._closed:
            raise ConnectionError("transport is closed")

    def add(self, stream: AsyncResponseStream) -> None:
        if self._closed:
            raise ConnectionError("transport closed while opening a stream")
        self._streams.add(stream)

    def discard(self, stream: AsyncResponseStream) -> None:
        self._streams.discard(stream)

    async def close(self) -> None:
        self._closed = True
        first_error: BaseException | None = None
        for stream in tuple(self._streams):
            try:
                await stream.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


class AsyncTransport:
    # Cookies are refused by policy in _refuse_cookies() rather than cleared per
    # request: one client is shared by every sandbox handle under a root, so
    # touching client.cookies mid-flight mutates state concurrent requests read.
    def __init__(
        self,
        base_url: Optional[str],
        token: Optional[str] = None,
        *,
        source: str = "sdk-python-async",
        request_timeout: Optional[float] = None,
        connection_pool_maxsize: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
        stream_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        auth: ProjectTokenAuth | None = None,
        otel: OpenTelemetrySupport | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        _stream_owners: _StreamOwners | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        # The cap on a buffered response body (0: none); see _body_limit.
        self._max_response_bytes = max_response_bytes
        # Optional per-request bearer resolution (sandbox access tokens).
        # Set by the sandbox handle after construction; None keeps the static
        # `token` behavior.  ProjectTokenAuth satisfies SandboxTokenAuth, so
        # passing auth= routes the project-token refresh through the same
        # single 401-retry loop; the sandbox handle overwrites this with its
        # own _SandboxTokenAuth when the handle can mint sandbox tokens.
        self.auth_provider: Optional[SandboxTokenAuth] = None
        self._source = source
        self._request_timeout_seconds = (
            request_timeout if request_timeout is not None else DEFAULT_REQUEST_TIMEOUT_SECONDS
        )
        self._connection_pool_maxsize = connection_pool_maxsize
        self._owns_client = client is None
        self._owns_streams = _stream_owners is None
        self._stream_owners = _stream_owners if _stream_owners is not None else _StreamOwners()
        self._auth = auth
        self._otel = otel
        if client is None:
            self._client = self._new_stream_client()
        else:
            self._client = client
        _bind_auth_client(self._auth, self._client)
        _refuse_cookies(self._client)
        # Exclusive streams (open_exclusive_stream) are long-lived observations
        # that must not occupy the pool every other request of this root uses.
        # An SDK-created transport gives each one a fresh client of its own; a
        # factory, when supplied, must do the same. An injected client is the
        # caller's pool: without a factory it is borrowed, never closed here.
        self._stream_client_factory = stream_client_factory or (
            self._new_stream_client if client is None else None
        )

    def _new_stream_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, object] = {
            "headers": {**client_identity_headers(self._source), "Accept-Encoding": ACCEPT_ENCODING},
            "timeout": _client_timeout(self._request_timeout_seconds),
            "http2": _HTTP2_AVAILABLE,
            "cookies": _cookie_jar(),
        }
        limits = _client_limits(self._connection_pool_maxsize)
        if limits is not None:
            kwargs["limits"] = limits
        return httpx.AsyncClient(**kwargs)

    @classmethod
    def shared(
        cls,
        *,
        source: str = "sdk-python-async",
        request_timeout: Optional[float] = None,
        connection_pool_maxsize: Optional[int] = None,
        auth: ProjectTokenAuth | None = None,
        otel: OpenTelemetrySupport | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> AsyncTransport:
        return cls(
            None,
            None,
            source=source,
            request_timeout=request_timeout,
            connection_pool_maxsize=connection_pool_maxsize,
            auth=auth,
            otel=otel,
            max_response_bytes=max_response_bytes,
        )

    def scoped(self, base_url: str, token: Optional[str] = None) -> AsyncTransport:
        """A view on the same connection pool, bound to another origin/credential.

        The returned transport never owns the client, so closing one sandbox
        handle cannot close the pool its siblings are still using.
        """
        return AsyncTransport(
            base_url,
            token,
            source=self._source,
            request_timeout=self._request_timeout_seconds,
            connection_pool_maxsize=self._connection_pool_maxsize,
            client=self._client,
            stream_client_factory=self._stream_client_factory,
            auth=self._auth if token is None else None,
            otel=self._otel,
            max_response_bytes=self._max_response_bytes,
            _stream_owners=self._stream_owners,
        )

    @property
    def request_timeout_seconds(self) -> float:
        return self._request_timeout_seconds

    @property
    def max_response_bytes(self) -> int:
        """The cap on a buffered response body, in bytes (``0``: no limit)."""
        return self._max_response_bytes

    async def send(self, spec: RequestSpec) -> Union[JsonValue, bytes]:
        # One total deadline for the whole call: resolving the bearer (a token
        # exchange), the request, a 401 refresh and its retry share it.
        with _request_deadline(_total_seconds(spec, self._request_timeout_seconds)):
            token = await self._request_token()
            try:
                return await self._send_once(spec, token)
            except Exception as exc:
                retry_token = await self._retry_token_after_401(exc, token)
                if retry_token is None:
                    raise
                return await self._send_once(spec, retry_token)

    async def _send_once(self, spec: RequestSpec, token: Optional[str]) -> Union[JsonValue, bytes]:
        try:
            kwargs = _request_kwargs(spec, self._request_timeout_seconds)
            built_headers = _request_headers(self._source, token, spec.headers)
            kwargs["headers"] = built_headers
            url = _request_url(self.base_url, spec.path)
            span = (
                self._otel.start_request_span(method=spec.method, url=url, headers=built_headers)
                if self._otel
                else None
            )
            try:
                response = await send_buffered(
                    self._client,
                    self._client.build_request(spec.method, url, **kwargs),
                    resolve_limit(spec.max_response_bytes, self._max_response_bytes),
                )
            except BaseException as error:
                if span is not None:
                    span.finish(error=error)
                raise
            if span is not None:
                span.finish(status_code=response.status_code)
        except httpx.TimeoutException as exc:
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc
        # Every public client runs on THIS transport (the sync surface bridges
        # onto it), so this is where the events hub learns a sandbox's service endpoint
        # from the response's endpoint header.
        if spec.on_headers is not None and response.status_code < 400:
            spec.on_headers(response.headers)
        return _decode_response(response)

    async def _request_token(self) -> Optional[str]:
        if self.auth_provider is not None:
            return await self.auth_provider.bearer()
        if self._auth is not None:
            return await self._auth.get_token()
        return self.token

    async def _retry_token_after_401(
        self, status_or_error: object, failed_token: Optional[str]
    ) -> Optional[str]:
        """The fresh bearer to retry with after a 401, or ``None`` to surface it.

        At most one retry per request: the caller retries with the returned
        token and never calls this again for the same request.
        """
        status_code = (
            status_or_error
            if isinstance(status_or_error, int)
            else getattr(status_or_error, "status_code", None)
        )
        if status_code != 401:
            return None
        if self.auth_provider is not None:
            fresh = await self.auth_provider.on_unauthorized(failed_token)
            if not fresh or fresh == failed_token:
                return None
            return fresh
        if self._auth is not None:
            fresh = await self._auth.get_token(force_refresh=True, current_token=failed_token)
            if not fresh or fresh == failed_token:
                return None
            return fresh
        return None

    async def raw(self, spec: RequestSpec) -> bytes:
        value = await self.send(spec)
        if isinstance(value, bytes):
            return value
        return json.dumps(value).encode("utf-8")

    async def stream_bytes(self, spec: RequestSpec) -> AsyncIterator[bytes]:
        """A streamed body's bytes as they arrive (never a buffered line)."""
        stream = await self.open_stream(spec)
        try:
            async for chunk in stream.iter_bytes():
                yield chunk
        finally:
            await stream.close()

    async def stream_lines(self, spec: RequestSpec) -> AsyncIterator[str]:
        stream = await self.open_stream(spec)
        try:
            async for line in stream.iter_lines():
                yield line
        finally:
            await stream.close()

    async def open_stream(self, spec: RequestSpec) -> AsyncResponseStream:
        # The request timeout, or the spec's own timeout in its place, bounds
        # opening the stream (the bearer, the request, a 401 refresh and its
        # retry, until the response headers arrive), never the stream itself;
        # a transfer has no total bound (it is idle-bounded per phase).
        with _request_deadline(_stream_open_seconds(spec, self._request_timeout_seconds)):
            token = await self._request_token()
            try:
                return await self._open_stream_once(spec, token)
            except Exception as exc:
                retry_token = await self._retry_token_after_401(exc, token)
                if retry_token is None:
                    raise
                return await self._open_stream_once(spec, retry_token)

    async def _open_stream_once(self, spec: RequestSpec, token: Optional[str]) -> AsyncResponseStream:
        url = _request_url(self.base_url, spec.path)
        span: _SpanFinisher | None = None
        try:
            kwargs = _request_kwargs(spec, self._request_timeout_seconds)
            kwargs["timeout"] = _stream_timeout(spec, self._request_timeout_seconds)
            built_headers = _request_headers(self._source, token, spec.headers)
            span = (
                self._otel.start_request_span(method=spec.method, url=url, headers=built_headers)
                if self._otel
                else None
            )
            kwargs["headers"] = built_headers
            stream = self._client.stream(spec.method, url, **kwargs)
            response = await self._stream_enter(stream, span)
        except httpx.TimeoutException as exc:
            if span is not None:
                span.finish(error=exc)
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            if span is not None:
                span.finish(error=exc)
            raise ConnectionError(str(exc)) from exc
        except BaseException as exc:
            if span is not None:
                span.finish(error=exc)
            raise
        if response.status_code >= 400:
            typed_error: BaseException | None = None
            try:
                body = await read_body(
                    response, resolve_limit(spec.max_response_bytes, self._max_response_bytes)
                )
            except httpx.TimeoutException as exc:
                if span is not None:
                    span.finish(status_code=response.status_code, error=ClientTimeoutError())
                raise ClientTimeoutError() from exc
            except httpx.HTTPError as exc:
                if span is not None:
                    span.finish(status_code=response.status_code, error=ConnectionError(str(exc)))
                raise ConnectionError(str(exc)) from exc
            except BaseException as exc:
                if span is not None:
                    span.finish(status_code=response.status_code, error=exc)
                raise
            finally:
                with anyio.move_on_after(_STREAM_RELEASE_GRACE_SECONDS, shield=True):
                    await stream.__aexit__(None, None, None)
            typed_error = error_from_response(
                response.status_code, body, content_type=response.headers.get("content-type")
            )
            if span is not None:
                span.finish(status_code=response.status_code, error=typed_error)
            raise typed_error
        return AsyncResponseStream(stream, response, span=span)

    async def open_exclusive_stream(self, spec: RequestSpec) -> AsyncResponseStream:
        """Open a long-lived response on a client of its own; status is NOT checked.

        The caller inspects ``status_code`` and reads (or bounds) any error body
        itself — except a 401 under a refreshable API credential, which gets the same
        single re-mint + retry as ``send``/``request``/``open_stream``, closing
        the failed stream before the retry. Redirects are never followed, so
        credentials cannot leave the requested origin. ``spec.timeout`` (else
        the request timeout) bounds opening it, until the headers, as a total
        deadline and per httpx phase; a 2xx response with ``unbounded_read`` (or no
        explicit timeout) then drops only the idle-read timeout, so a healthy
        live stream is never cut off while finite bodies keep theirs.
        Cancelling the reading task releases the response and closes its
        client — no socket shutdown is needed on the async path, and no other
        request shares the connection.
        """
        self._stream_owners.ensure_open()
        # One deadline for opening it, as for ``open_stream``: the bearer, the
        # request, a 401 refresh and its retry, until the response headers.
        with _request_deadline(_stream_open_seconds(spec, self._request_timeout_seconds)):
            token = await self._request_token()
            stream = await self._open_exclusive_once(spec, token)
            if stream.status_code != 401:
                return stream
            try:
                retry_token = await self._retry_token_after_401(stream.status_code, token)
            except BaseException:
                # A raising re-mint (e.g. a FORBIDDEN mint) surfaces, but never
                # leaks the failed response.
                await stream.close()
                raise
            if retry_token is None:
                return stream
            await stream.close()
            return await self._open_exclusive_once(spec, retry_token)

    async def _open_exclusive_once(self, spec: RequestSpec, token: Optional[str]) -> AsyncResponseStream:
        owned_client = self._stream_client_factory() if self._stream_client_factory is not None else None
        if owned_client is not None and owned_client is self._client:
            raise ValueError("stream_client_factory must return a fresh, nonshared client")
        if owned_client is not None:
            _refuse_cookies(owned_client)
        client = owned_client if owned_client is not None else self._client
        url = _request_url(self.base_url, spec.path)
        span: _SpanFinisher | None = None
        try:
            kwargs = _request_kwargs(spec, self._request_timeout_seconds)
            kwargs["timeout"] = (
                _client_timeout(self._request_timeout_seconds)
                if spec.timeout is None
                else spec.timeout
                if _per_call_seconds(spec.timeout)
                else httpx.Timeout(None)
            )
            built_headers = _request_headers(self._source, token, spec.headers)
            span = (
                self._otel.start_request_span(method=spec.method, url=url, headers=built_headers)
                if self._otel
                else None
            )
            kwargs["headers"] = built_headers
            stream = client.stream(spec.method, url, follow_redirects=False, **kwargs)
            response = await self._stream_enter(stream, span)
        except BaseException as exc:
            if span is not None:
                span.finish(error=exc)
            if owned_client is not None:
                with anyio.move_on_after(_STREAM_RELEASE_GRACE_SECONDS, shield=True):
                    await owned_client.aclose()
            if isinstance(exc, httpx.TimeoutException):
                raise ClientTimeoutError() from exc
            if isinstance(exc, httpx.HTTPError):
                raise ConnectionError(str(exc)) from exc
            raise
        if 200 <= response.status_code < 300 and (spec.unbounded_read or spec.timeout is None):
            timeouts: object = response.request.extensions.get("timeout")
            if isinstance(timeouts, dict):
                timeouts["read"] = None
        result = AsyncResponseStream(
            stream, response, owned_client=owned_client, on_close=self._stream_owners.discard, span=span
        )
        try:
            self._stream_owners.add(result)
        except BaseException:
            await result.close()
            raise
        return result

    async def close(self) -> None:
        try:
            if self._owns_streams:
                await self._stream_owners.close()
        finally:
            if self._owns_client:
                await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        content_factory: _ReplayableContentFactory | None = None,
        transfer: bool = False,
        max_response_bytes: Optional[int] = None,
        **kwargs: Unpack[_RequestKwargs],
    ) -> httpx.Response:
        """Return the raw httpx.Response after envelope-error handling.

        Contract: any status >= 400 raises the typed SDK error; callers that
        need undecoded bodies (e.g. multipart bulk reads) rely on this.
        ``transfer=True`` marks a file transfer: no total deadline, whatever
        timeout it carries, but the request timeout bounds every idle phase
        (connecting, each write and read gap). An explicit ``timeout`` in
        seconds replaces the request timeout as a total bound (``0`` or above
        the timer maximum: none). ``max_response_bytes`` replaces the client's
        cap on the buffered body for this call.
        """
        limit = resolve_limit(max_response_bytes, self._max_response_bytes)
        explicit = kwargs.get("timeout")
        if transfer:
            # No total deadline, whatever timeout it carries; idle-bounded.
            kwargs["timeout"] = _client_timeout(self._request_timeout_seconds)
            total = 0.0
        elif isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
            # A per-call timeout in seconds: a total bound, like the request timeout.
            total = _per_call_seconds(explicit)
            kwargs["timeout"] = explicit if total else httpx.Timeout(None)
        else:
            total = 0 if "timeout" in kwargs else self._request_timeout_seconds
        caller_headers = kwargs.get("headers")
        # A one-shot iterator body (no factory) is exhausted by the first send:
        # replaying it on the 401 retry would silently send an empty body, so
        # such requests surface the 401 instead (the TS SDK does the same).
        one_shot_body = content_factory is None and _is_one_shot_content(kwargs.get("content"))
        if content_factory is not None:
            kwargs["content"] = content_factory(0)
        # One total deadline for the bearer, the request, a 401 refresh and its retry.
        with _request_deadline(total):
            token = await self._request_token()
            try:
                return await self._request_once(method, path, token, caller_headers, kwargs, limit)
            except Exception as exc:
                if one_shot_body:
                    raise
                retry_token = await self._retry_token_after_401(exc, token)
                if retry_token is None:
                    raise
                if content_factory is not None:
                    kwargs["content"] = content_factory(1)
                return await self._request_once(method, path, retry_token, caller_headers, kwargs, limit)

    async def _request_once(
        self,
        method: str,
        path: str,
        token: Optional[str],
        caller_headers: Optional[Mapping[str, str]],
        kwargs: _RequestKwargs,
        limit: int,
    ) -> httpx.Response:
        url = _request_url(self.base_url, path)
        built_headers = _request_headers(self._source, token, caller_headers)
        request_kwargs = dict(kwargs)
        request_kwargs["headers"] = built_headers
        span = (
            self._otel.start_request_span(method=method, url=url, headers=built_headers)
            if self._otel
            else None
        )
        try:
            response = await send_buffered(
                self._client, self._client.build_request(method, url, **request_kwargs), limit
            )
        except httpx.TimeoutException as exc:
            if span is not None:
                span.finish(error=exc)
            raise ClientTimeoutError() from exc
        except httpx.HTTPError as exc:
            if span is not None:
                span.finish(error=exc)
            raise ConnectionError(str(exc)) from exc
        except BaseException as error:
            if span is not None:
                span.finish(error=error)
            raise
        if span is not None:
            span.finish(status_code=response.status_code)
        if response.status_code >= 400:
            raise error_from_response(
                response.status_code, response.content, content_type=response.headers.get("content-type")
            )
        return response

    async def _stream_enter(
        self,
        stream: _AsyncResponseStreamContext,
        span: _SpanFinisher | None,
    ) -> httpx.Response:
        try:
            response = await stream.__aenter__()
        except BaseException as error:
            if span is not None:
                span.finish(error=error)
            raise
        return response

    async def get(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Unpack[_RequestKwargs]) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)


def _client_timeout(request_timeout_seconds: float) -> httpx.Timeout:
    if request_timeout_seconds == 0:
        return httpx.Timeout(None)
    return httpx.Timeout(request_timeout_seconds)


def _timeout_with_unbounded_read(request_timeout_seconds: float) -> httpx.Timeout:
    if request_timeout_seconds == 0:
        return httpx.Timeout(None)
    return httpx.Timeout(request_timeout_seconds, read=None)


def _is_one_shot_content(content: object) -> bool:
    """True for iterator/async-iterator bodies that cannot be re-sent."""
    if content is None or isinstance(content, (bytes, bytearray, memoryview, str)):
        return False
    return hasattr(content, "__anext__") or hasattr(content, "__next__")


def _client_limits(connection_pool_maxsize: Optional[int]) -> Optional[httpx.Limits]:
    if connection_pool_maxsize is None:
        return None
    return httpx.Limits(
        max_connections=connection_pool_maxsize,
        max_keepalive_connections=connection_pool_maxsize,
    )


def _total_seconds(spec: RequestSpec, request_timeout_seconds: float) -> float:
    """The total-time bound of one request, in seconds (``0``: none).

    An ordinary request gets the client's request timeout. A per-call
    ``timeout`` (a wait's budget plus its slack, a git remote call, ...)
    replaces it for that request with the same meaning: total time from
    sending to the last byte, ``0`` or a value above the timer maximum meaning
    no limit. A hold that reads without a deadline (``unbounded_read``,
    ``/wait`` with no budget) and a file transfer (idle-bounded per phase
    instead) have no total bound.
    """
    if spec.transfer:
        return 0.0
    if spec.timeout is not None:
        return _per_call_seconds(spec.timeout)
    if spec.unbounded_read or spec.path.endswith("/wait"):
        return 0.0
    return request_timeout_seconds


def _per_call_seconds(seconds: float) -> float:
    """A per-call timeout as a total bound: ``0`` or above the timer maximum is none."""
    return 0.0 if seconds <= 0 or seconds > _MAX_BOUNDED_TIMEOUT_SECONDS else float(seconds)


def _stream_open_seconds(spec: RequestSpec, request_timeout_seconds: float) -> float:
    """The total-time bound on opening a stream, until its headers (``0``: none).

    The spec's own ``timeout`` replaces the request timeout, as for an ordinary
    request; a transfer has no total bound (it is idle-bounded per phase). The
    stream's body never has one.
    """
    if spec.transfer:
        return 0.0
    if spec.timeout is not None:
        return _per_call_seconds(spec.timeout)
    return request_timeout_seconds


def _stream_timeout(spec: RequestSpec, request_timeout_seconds: float) -> httpx.Timeout:
    """httpx's per-phase timeouts for a stream: the opening bound, no idle-read bound.

    A streamed file transfer is the exception: the request timeout bounds each
    idle phase (connecting, each write and read gap), never its total time.
    """
    if spec.transfer:
        return _client_timeout(request_timeout_seconds)
    return _timeout_with_unbounded_read(_stream_open_seconds(spec, request_timeout_seconds))


@contextlib.contextmanager
def _request_deadline(seconds: float) -> Iterator[None]:
    """Bound the enclosed request by ``seconds`` of wall-clock time (``0``: no bound).

    httpx's own timeouts apply per phase (connect, each read, each write); this
    is the total-time bound, the same meaning the TypeScript SDK gives its
    request timeout.
    """
    if seconds <= 0:
        yield
        return
    try:
        with anyio.fail_after(seconds):
            yield
    except TimeoutError as exc:
        raise ClientTimeoutError() from exc


def _request_kwargs(spec: RequestSpec, request_timeout_seconds: float) -> _RequestKwargs:
    kwargs: _RequestKwargs = {
        "params": _normalize_params(spec.params),
        "json": spec.json_body,
        "data": spec.data,
        "files": spec.files,
    }
    if spec.transfer:
        # No total deadline (``_total_seconds``), but every idle phase is
        # bounded by the request timeout: a transfer that keeps moving bytes
        # runs on, a stalled one fails.
        kwargs["timeout"] = _client_timeout(request_timeout_seconds)
    elif spec.timeout is not None:
        # httpx's per-phase bound; ``_total_seconds`` holds the whole request to it.
        kwargs["timeout"] = spec.timeout if _per_call_seconds(spec.timeout) else httpx.Timeout(None)
    elif spec.unbounded_read or spec.path.endswith("/wait"):
        kwargs["timeout"] = _timeout_with_unbounded_read(request_timeout_seconds)
    return kwargs


def _normalize_params(params: Mapping[str, JsonValue]) -> Mapping[str, QueryParamValue] | None:
    if not params:
        return None
    normalized: dict[str, QueryParamValue] = {}
    for key, value in params.items():
        if isinstance(value, list):
            scalars = [item for item in value if _is_query_param_scalar(item)]
            if len(scalars) == len(value):
                normalized[key] = scalars
            continue
        if _is_query_param_scalar(value):
            normalized[key] = value
    return normalized or None


def _is_query_param_scalar(value: JsonValue) -> TypeGuard[QueryParamScalar]:
    return isinstance(value, (str, int, float, bool)) or value is None


def _validated_relative_path(path: str) -> str:
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise create_error(_RELATIVE_PATH_ERROR, 400, "BAD_REQUEST", "SDK")
    return path


def _request_url(base_url: Optional[str], path: str) -> str:
    relative_path = _validated_relative_path(path)
    if base_url is None:
        return relative_path
    return f"{base_url}{relative_path}"


def _request_headers(
    source: str,
    token: Optional[str],
    headers: Optional[Mapping[str, str]],
) -> Mapping[str, str]:
    request_headers: dict[str, str] = client_identity_headers(source)
    # Only the encodings the capped readers decode in bounded steps.
    request_headers["Accept-Encoding"] = ACCEPT_ENCODING
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)
    return request_headers


def _decode_response(response: httpx.Response) -> Union[JsonValue, bytes]:
    if response.status_code >= 400:
        raise error_from_response(
            response.status_code, response.content, content_type=response.headers.get("content-type")
        )
    if is_json_media_type(response.headers.get("content-type")):
        try:
            return parse_json_bytes(response.content)
        except ValueError:
            # Invalid JSON, invalid UTF-8 (UnicodeDecodeError is a ValueError)
            # or a non-standard constant: malformed, whether or not the caller
            # reads the body.
            raise create_error(
                "expected JSON response: the body is not valid JSON", 400, "BAD_REQUEST", "SDK"
            ) from None
    if not response.content:
        return b""
    return response.content
