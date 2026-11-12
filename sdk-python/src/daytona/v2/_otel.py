# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Callable, Dict, Final, Optional, Protocol
from urllib.parse import urlsplit

from .errors import create_error


class _SpanLike(Protocol):
    """The subset of ``opentelemetry.trace.Span`` the SDK calls."""

    def set_attribute(self, key: str, value: object) -> None: ...

    def set_status(self, status: object) -> None: ...

    def record_exception(self, exception: BaseException) -> None: ...


class _TracerLike(Protocol):
    def start_as_current_span(self, name: str, *, kind: object) -> AbstractContextManager[_SpanLike]: ...


@dataclass(frozen=True, slots=True)
class _TraceContext:
    route_template: str
    sandbox_id: Optional[str]
    server_address: Optional[str]


@dataclass(frozen=True, slots=True)
class _TraceApi:
    get_tracer: Callable[[str, str], _TracerLike]
    inject: Callable[[Dict[str, str]], None]
    status_factory: Callable[[object], object]
    ok_status: object
    error_status: object
    span_kind_client: object


class _ActiveRequestSpan:
    def __init__(
        self,
        *,
        context_manager: AbstractContextManager[_SpanLike],
        span: _SpanLike,
        status_factory: Callable[[object], object],
        ok_status: object,
        error_status: object,
    ) -> None:
        self._context_manager = context_manager
        self._span = span
        self._status_factory = status_factory
        self._ok_status = ok_status
        self._error_status = error_status
        self._closed = False

    def finish(self, *, status_code: Optional[int] = None, error: Optional[BaseException] = None) -> None:
        if self._closed:
            return
        self._closed = True
        if status_code is not None:
            self._span.set_attribute("http.response.status_code", status_code)
            # A redirect fails too: the SDK follows none.
            if status_code >= 300:
                self._span.set_status(self._status_factory(self._error_status))
        if error is not None:
            self._span.record_exception(error)
            self._span.set_status(self._status_factory(self._error_status))
        elif status_code is None or status_code < 300:
            self._span.set_status(self._status_factory(self._ok_status))
        self._context_manager.__exit__(None, None, None)


class OpenTelemetrySupport:
    def __init__(self, otel_module: _TraceApi) -> None:
        sdk_version = _sdk_version()
        self._inject = otel_module.inject
        self._status = otel_module.status_factory
        self._ok_status = otel_module.ok_status
        self._status_code_error = otel_module.error_status
        self._span_kind_client = otel_module.span_kind_client
        self._tracer = otel_module.get_tracer("daytona", sdk_version)

    def start_request_span(self, *, method: str, url: str, headers: Dict[str, str]) -> _ActiveRequestSpan:
        context = _trace_context(url)
        context_manager = self._tracer.start_as_current_span(
            f"daytona.{method.lower()} {context.route_template}", kind=self._span_kind_client
        )
        span = context_manager.__enter__()
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.template", context.route_template)
        if context.server_address is not None:
            span.set_attribute("server.address", context.server_address)
        if context.sandbox_id is not None:
            span.set_attribute("daytona.sandbox_id", context.sandbox_id)
        self._inject(headers)
        return _ActiveRequestSpan(
            context_manager=context_manager,
            span=span,
            status_factory=self._status,
            ok_status=self._ok_status,
            error_status=self._status_code_error,
        )


def load_opentelemetry_support() -> OpenTelemetrySupport:
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.trace import SpanKind
        from opentelemetry.trace.status import Status, StatusCode
    except ModuleNotFoundError as error:
        raise create_error(
            "otel_enabled=True requires the optional daytona[otel] extra (missing opentelemetry-api)",
            400,
            "BAD_REQUEST",
            "SDK",
        ) from error

    return OpenTelemetrySupport(
        _TraceApi(
            get_tracer=trace.get_tracer,
            inject=propagate.inject,
            status_factory=Status,
            ok_status=StatusCode.OK,
            error_status=StatusCode.ERROR,
            span_kind_client=SpanKind.CLIENT,
        )
    )


def _sdk_version() -> str:
    try:
        return version("daytona")
    except PackageNotFoundError:
        return "0"


def _trace_context(url: str) -> _TraceContext:
    parsed = urlsplit(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    sandbox_id = _sandbox_id_from_segments(segments)
    return _TraceContext(
        route_template=_route_template(segments),
        sandbox_id=sandbox_id,
        server_address=parsed.hostname,
    )


def _route_template(segments: list[str]) -> str:
    if not segments:
        return "/"
    sandbox_id = _sandbox_id_from_segments(segments)
    normalized: list[str] = []
    for index, segment in enumerate(segments):
        if index > 0 and segments[index - 1] in {"port", "ports"}:
            normalized.append("{port}")
            continue
        if sandbox_id is not None and segment == sandbox_id:
            normalized.append("{id}")
            continue
        if _is_resource_id_segment(index, segments):
            normalized.append("{id}")
            continue
        normalized.append(segment)
    return "/" + "/".join(normalized)


def _sandbox_id_from_segments(segments: list[str]) -> Optional[str]:
    for index, segment in enumerate(segments):
        if segment in _SANDBOX_COLLECTION_SEGMENTS and index + 1 < len(segments):
            sandbox_id = segments[index + 1]
            if sandbox_id not in _static_segments_for_collection(segment):
                return sandbox_id
    return None


# Sandbox collection paths carry the sandbox id.
_SANDBOX_COLLECTION_SEGMENTS: Final[frozenset[str]] = frozenset({"sandboxes"})


def _is_resource_id_segment(index: int, segments: list[str]) -> bool:
    if index == 0:
        return False
    segment = segments[index]
    parent = segments[index - 1]
    if parent in _SANDBOX_COLLECTION_SEGMENTS:
        # Collection entries carry a sandbox id unless they name a static operation.
        return segment not in _static_segments_for_collection(parent)
    # `/computeruse/process/{name}/...`: `computeruse` itself is a namespace
    # (`mouse`, `keyboard`, `recordings`, ...), only `process/<name>` carries an id.
    if parent == "process" and index >= 2 and segments[index - 2] == "computeruse":
        return True
    if parent in _RESOURCE_ID_COLLECTION_SEGMENTS:
        return segment not in _static_segments_for_collection(parent)
    return False


def _static_segments_for_collection(collection: str) -> frozenset[str]:
    return _COLLECTION_STATIC_SEGMENTS.get(collection, frozenset())


_RESOURCE_ID_COLLECTION_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "api-keys",
        "charges",
        "images",
        "invitations",
        "invoices",
        "members",
        "orgs",
        "processes",
        "projects",
        "recordings",
        "registries",
        "reservations",
        "secrets",
        "service-accounts",
        "sessions",
        "snapshots",
    }
)


_COLLECTION_STATIC_SEGMENTS: Final[dict[str, frozenset[str]]] = {
    "api-keys": frozenset({"current"}),
    "invitations": frozenset({"accept", "decline", "lookup"}),
    "sandboxes": frozenset({"from-snapshot", "macos", "search"}),
}
