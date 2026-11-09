# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
import threading
import time
import typing
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Iterator, Mapping, Optional

import httpx
import pytest
import respx
from _handles import lazy_sandbox
from _repo import _repo_file

from daytona.v2 import (
    AuditActionGroup,
    AuditActionList,
    DockerfileImageSource,
    FilesystemEvent,
    FilesystemEventType,
    ImageBuilder,
    ImageBuildFailedError,
    ImageSource,
    MacReservation,
    ManifestImageSource,
    NamedImageSource,
    OrganizationLimits,
    ProjectApiKey,
    ProjectApiKeyContext,
    ProjectBudget,
    RegistryImageSource,
    RequestTimeoutError,
    SandboxCreateConnectionError,
    SandboxCreateRequestTimeoutError,
    SandboxTerminalStateError,
    Secret,
    Snapshot,
    codes,
)
from daytona.v2 import _codes as code_names
from daytona.v2._api_core import expect_sandbox_detail
from daytona.v2._auth import ProjectTokenAuth
from daytona.v2._core import (
    LIFECYCLE_FIELDS,
    LIFECYCLE_MAX_SECS,
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    assert_lifecycle_class_support,
    expect_process_dto,
    file_bulk_upload_spec,
    lifecycle_body,
    logs_spec,
    parse_image,
    parse_lifecycle,
    parse_log_page,
    parse_process,
    parse_search_page,
    parse_snapshot,
    parse_sse_lines,
    parse_timestamp,
    process_create_spec,
    search_params,
    search_timestamp,
    stdin_spec,
)
from daytona.v2._events import WaitOutcome
from daytona.v2._gen.api import LifecyclePolicy, SandboxLimits
from daytona.v2._gen.api import Sandbox as SandboxDto
from daytona.v2._otel import _route_template, _sandbox_id_from_segments, load_opentelemetry_support
from daytona.v2._sync import Daytona
from daytona.v2._transport import (
    SDK_VERSION,
    AsyncTransport,
    SyncTransport,
    _decode_response,
    _request_headers,
    _request_kwargs,
    _timeout_with_unbounded_read,
)
from daytona.v2.computer_use import AsyncComputerUseService
from daytona.v2.computer_use_types import (
    ComputerUseA11yNameMatch,
    ComputerUseA11yScope,
    ComputerUsePoint,
    ComputerUseRegion,
)
from daytona.v2.daytona import AsyncDaytona, _create_body, _create_path, _idempotency_key_for_name
from daytona.v2.docker import DOCKER_WAIT_DEADLINE_GRACE_SECONDS, AsyncDockerService
from daytona.v2.errors import (
    API_ERROR_CLASSES,
    CODE_ERROR_CLASSES,
    PROXY_ERROR_CLASSES,
    ROUTER_ERROR_CLASSES,
    AddonUnavailableError,
    AuthenticationError,
    BadRequestError,
    BillingDisabledError,
    ClientTimeoutError,
    ConflictError,
    ConnectionError,
    CursorExpiredError,
    DaytonaError,
    DockerNotReadyError,
    DockerWaitTimeoutError,
    InternalServerError,
    LimitExceededError,
    NotFoundError,
    OrgSuspendedError,
    PayloadTooLargeError,
    ProcessNotFoundError,
    ProjectBudgetExceededError,
    RateLimitedError,
    RateLimitError,
    SandboxNotRunningError,
    SearchNotConfiguredError,
    SearchUnavailableError,
    ServiceUnavailableError,
    SessionNotFoundError,
    UnsupportedOperationError,
    create_error,
    error_class_from_status,
)
from daytona.v2.filesystem import AsyncFileSystemService, AsyncWatchHandle
from daytona.v2.git import AsyncGitService
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.lsp import AsyncLspService
from daytona.v2.ports import AsyncPortsService
from daytona.v2.process import (
    TIMED_OUT_LOG_PAGE_LIMIT,
    AsyncProcessHandle,
    AsyncProcessService,
    AsyncPtyService,
    _all_logs,
)
from daytona.v2.sandbox import (
    _CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE,
    AsyncSandbox,
    sandbox_info_from_id,
)
from daytona.v2.services import (
    AsyncImagesService,
    AsyncMacModelsService,
    AsyncReservationsService,
    AsyncSecretsService,
    AsyncSnapshotsService,
    AsyncVolumesService,
)
from daytona.v2.types import (
    DISABLED,
    DaytonaConfig,
    FileInfo,
    InterpreterLogs,
    InterpreterProcess,
    InterpreterResult,
    ListResult,
    ListSandboxesOptions,
    MacCatalog,
    MacosResources,
    MacReservationBatch,
    PortMapping,
    PreviewLink,
    ProcessState,
    Resources,
    SandboxLifecycle,
    SandboxSearchFilters,
    SandboxWaitState,
    SearchResult,
    SearchSandboxesOptions,
    SignedPreviewLink,
    TerminalSize,
    TransferProgressCallback,
    VolumeMount,
    WaitReason,
    WaitResult,
)


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


class _CreateSource:
    @staticmethod
    def base(ref: str) -> str:
        return ref

    @staticmethod
    def from_dockerfile(content: str) -> ImageBuilder:
        return ImageBuilder.from_dockerfile(content)


Image = _CreateSource

_TS_NAMED_EXPORT_RE = re.compile(
    r"^\s*export(?:\s+type)?\s*\{(?P<names>[^}]+)\}\s*from\s*['\"](?P<target>[^'\"]+)['\"];?\s*$",
    re.MULTILINE | re.DOTALL,
)
_TS_STAR_EXPORT_RE = re.compile(
    r"^\s*export\s+\*\s+from\s*['\"](?P<target>[^'\"]+)['\"];?\s*$",
    re.MULTILINE,
)

_HTTP_METHOD_NAMES: Final = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_TOOLBOX_ROUTE_ALLOWLIST: Final[dict[tuple[str, str], str]] = {
    ("GET", "/info"): "Not exposed by the SDK.",
    (
        "GET",
        "/processes/{id}/attach",
    ): "WebSocket attach: the Python SDK ships no websocket adapter (TypeScript opens it via a WebSocket URL, so its list has no entry).",
    (
        "GET",
        "/sessions/{id}",
    ): "Not exposed by the SDK.",
    ("GET", "/version"): "Not exposed by the SDK.",
}
_CONTRACT_ROUTE_PREFIXES: Final = (
    "/computeruse",
    "/docker",
    "/files",
    "/git",
    "/images",
    "/info",
    "/lsp",
    "/mac-catalog",
    "/mac-models",
    "/port",
    "/processes",
    "/reservations",
    "/secrets",
    "/sessions",
    "/snapshots",
    "/sandboxes",
    "/system",
    "/version",
    "/volumes",
)

_API_ROUTE_ALLOWLIST: Final[dict[tuple[str, str], str]] = {}


async def _no_sleep(_seconds: float) -> None:
    return None


def _record_sleep(into: list[float]):
    async def _sleep(seconds: float) -> None:
        into.append(seconds)

    return _sleep


@dataclass(frozen=True)
class _RouteUse:
    file: str
    line: int
    method: str
    path: str
    raw: str


def _typescript_module_path(module_path: Path, target: str) -> Path:
    candidate = (module_path.parent / target).resolve()
    if candidate.exists():
        return candidate
    if candidate.suffix == ".js":
        ts_candidate = candidate.with_suffix(".ts")
        if ts_candidate.exists():
            return ts_candidate
        tsx_candidate = candidate.with_suffix(".tsx")
        if tsx_candidate.exists():
            return tsx_candidate
    raise FileNotFoundError(candidate)


def _typescript_export_names(module_path: Path, seen: set[Path] | None = None) -> set[str]:
    resolved = module_path.resolve()
    visited = set() if seen is None else seen
    if resolved in visited:
        return set()
    visited.add(resolved)
    text = resolved.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in _TS_NAMED_EXPORT_RE.finditer(text):
        for raw_name in match.group("names").split(","):
            name = raw_name.strip().removeprefix("type ")
            if not name:
                continue
            names.add(name.split(" as ")[-1].strip())
    for match in _TS_STAR_EXPORT_RE.finditer(text):
        names.update(
            _typescript_export_names(_typescript_module_path(resolved, match.group("target")), visited)
        )
    return names


SIGNED_FILE_TEST_SIGNATURE = "v1_c2VydmVyLW1pbnRlZC1zaWduYXR1cmU"
TAGGED_SANDBOX_ID = "12drXXUifSrRnXLGbXg8E-region-c"
_DEFAULT_TIME = "2026-08-24T00:00:00Z"


def _read_contract_operations(contract_name: str) -> list[tuple[str, str]]:
    spec = json.loads(_repo_file(f"contract/{contract_name}-openapi.json").read_text())
    operations: list[tuple[str, str]] = []
    for route_path, route_item in spec["paths"].items():
        for method in route_item:
            verb = method.upper()
            if verb in _HTTP_METHOD_NAMES:
                operations.append((verb, route_path))
    return operations


# Sentinel bound to function parameters: a `RequestSpec(<param>)` forwards a
# caller-supplied route and is not itself a route literal.
_FORWARDED_PARAM = ast.Name(id="__forwarded_param__", ctx=ast.Load())

# Helpers whose HTTP method is fixed: `name -> method`. The route's positional
# slot is set per helper by _ROUTE_FORWARDING_PATH_INDEX (default: first).
# ONE registry drives both halves of the collector —
# a `RequestSpec(<param>)` inside a registered helper is skipped as a
# forwarder, and every call of a registered helper is collected at the call
# site with that method. A forwarder that is not registered is unresolvable
# and fails the test; a registered one cannot be missed at its call sites.
_ROUTE_FORWARDING_HELPERS: dict[str, str] = {
    "_with_credentials": "POST",
    "follow_build_logs": "GET",
    "read_build_logs": "GET",
    "_discover": "GET",
    "_get_artifact": "GET",
}
_ROUTE_FORWARDING_PATH_INDEX = {
    "follow_build_logs": 1,
    "read_build_logs": 1,
    "_discover": 1,
    "_get_artifact": 1,
}


def _collect_python_route_uses() -> list[_RouteUse]:
    root = Path(__file__).resolve().parents[1] / "src" / "daytona" / "v2"
    route_uses: list[_RouteUse] = []
    for path in root.glob("*.py"):
        if path.parts[-2] == "_gen":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        route_uses.extend(_collect_python_route_uses_from_tree(tree, path.relative_to(root.parent), source))
    return route_uses


def _collect_python_route_uses_from_tree(
    tree: ast.Module, relative_path: Path, source: str
) -> list[_RouteUse]:
    route_uses: list[_RouteUse] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._bindings: list[dict[str, ast.expr]] = [{}]

        def _push(self) -> None:
            self._bindings.append({})

        def _pop(self) -> None:
            self._bindings.pop()

        def _bind(self, name: str, value: ast.expr) -> None:
            self._bindings[-1][name] = value

        def _resolve(self, name: str) -> ast.expr | None:
            for scope in reversed(self._bindings):
                if name in scope:
                    return scope[name]
            return None

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._push()
            self._bind_params(node)
            self.generic_visit(node)
            self._pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._push()
            self._bind_params(node)
            self.generic_visit(node)
            self._pop()

        def _bind_params(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            # Only a REGISTERED forwarding helper's parameters are treated as
            # caller-named routes (collected at its call sites). In any other
            # function a `RequestSpec(<param>)` stays unresolvable and trips the
            # fail-loudly assertion, so an unregistered forwarder cannot rot
            # the drift test silently.
            if node.name not in _ROUTE_FORWARDING_HELPERS:
                return
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                self._bindings[-1][arg.arg] = _FORWARDED_PARAM

        def visit_Assign(self, node: ast.Assign) -> None:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self._bind(node.targets[0].id, node.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name) and node.value is not None:
                self._bind(node.target.id, node.value)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if _is_request_spec_call(node):
                if isinstance(node.args[1], ast.Name) and self._resolve(node.args[1].id) is _FORWARDED_PARAM:
                    self.generic_visit(node)
                    return
                methods = _route_variants_from_expr(node.args[0], self._resolve)
                paths = _route_variants_from_expr(node.args[1], self._resolve)
                raw = ast.get_source_segment(source, node.args[1]) or "<unknown>"
                # A RequestSpec whose route the collector cannot resolve is a
                # route the drift test never checks: fail at the call site
                # instead of letting coverage rot silently. Teach
                # `_route_variants_from_expr` the new shape, do not weaken this.
                if not paths or not [m for m in methods if m in _HTTP_METHOD_NAMES]:
                    raise AssertionError(
                        f"SDK route drift: cannot resolve the route of RequestSpec({raw}) at "
                        f"{relative_path.as_posix()}:{node.lineno} (methods={methods!r}, paths={paths!r})"
                    )
                for method in methods:
                    if method not in _HTTP_METHOD_NAMES:
                        continue
                    for path in paths:
                        normalized = _normalize_sdk_route(path)
                        if normalized is None:
                            continue
                        route_uses.append(
                            _RouteUse(
                                file=relative_path.as_posix(),
                                line=node.args[1].lineno,
                                method=method,
                                path=normalized,
                                raw=raw,
                            )
                        )
            elif (helper_method := _route_forwarding_helper_method(node)) is not None:
                helper_name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                path_arg = node.args[_ROUTE_FORWARDING_PATH_INDEX.get(helper_name, 0)]
                if isinstance(path_arg, ast.Name) and self._resolve(path_arg.id) is _FORWARDED_PARAM:
                    self.generic_visit(node)
                    return
                raw = ast.get_source_segment(source, path_arg) or "<unknown>"
                paths = _route_variants_from_expr(path_arg, self._resolve)
                if not paths:
                    raise AssertionError(
                        f"SDK route drift: cannot resolve the route passed to forwarding helper "
                        f"{raw} at {relative_path.as_posix()}:{node.lineno}"
                    )
                for path in paths:
                    normalized = _normalize_sdk_route(path)
                    if normalized is None:
                        continue
                    route_uses.append(
                        _RouteUse(
                            file=relative_path.as_posix(),
                            line=path_arg.lineno,
                            method=helper_method,
                            path=normalized,
                            raw=raw,
                        )
                    )
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    return route_uses


def _is_request_spec_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "RequestSpec" and len(node.args) >= 2


def _route_forwarding_helper_method(node: ast.Call) -> str | None:
    name = (
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr
        if isinstance(node.func, ast.Attribute)
        else None
    )
    if name is None or name not in _ROUTE_FORWARDING_HELPERS:
        return None
    index = _ROUTE_FORWARDING_PATH_INDEX.get(name, 0)
    expected_shape = ast.Name if name in _ROUTE_FORWARDING_PATH_INDEX else ast.Attribute
    if (
        not isinstance(node.func, expected_shape)
        or len(node.args) <= index
        or any(isinstance(arg, ast.Starred) for arg in node.args[: index + 1])
    ):
        raise AssertionError(
            f"SDK route drift: unsupported call shape for forwarding helper {name} "
            f"at line {node.lineno}; expected {expected_shape.__name__} with route at positional slot {index}"
        )
    return _ROUTE_FORWARDING_HELPERS[name]


@pytest.mark.parametrize("helper", ["follow_build_logs", "read_build_logs", "_discover"])
@pytest.mark.parametrize(
    "call",
    [
        'self.{helper}(api, "/images/image/build-logs-url")',
        '{helper}(api, discovery_path="/images/image/build-logs-url")',
        "{helper}(api)",
        '{helper}(*args, "/images/image/build-logs-url")',
        "{helper}(api, *paths)",
    ],
)
def test_route_forwarding_helpers_fail_loudly_on_unsupported_call_shapes(helper, call):
    source = call.format(helper=helper)
    with pytest.raises(AssertionError, match=f"unsupported call shape for forwarding helper {helper}"):
        _collect_python_route_uses_from_tree(ast.parse(source), Path("fixture.py"), source)


@pytest.mark.parametrize(
    "source",
    [
        '_with_credentials("/git/pull")',
        'self._with_credentials(path="/git/pull")',
        "self._with_credentials(*paths)",
    ],
)
def test_credentials_forwarder_also_fails_loudly_on_unsupported_call_shapes(source):
    with pytest.raises(
        AssertionError, match="unsupported call shape for forwarding helper _with_credentials"
    ):
        _collect_python_route_uses_from_tree(ast.parse(source), Path("fixture.py"), source)


@pytest.mark.parametrize("helper", ["follow_build_logs", "read_build_logs", "_discover"])
def test_route_forwarding_helpers_collect_positional_route_and_reject_unresolved_route(helper):
    source = f'{helper}(api, "/images/image/build-logs-url", timeout=1)'
    uses = _collect_python_route_uses_from_tree(ast.parse(source), Path("fixture.py"), source)
    assert [(use.method, use.path) for use in uses] == [("GET", "/images/image/build-logs-url")]
    source = f"{helper}(api, unknown_route)"
    with pytest.raises(AssertionError, match="cannot resolve the route passed to forwarding helper"):
        _collect_python_route_uses_from_tree(ast.parse(source), Path("fixture.py"), source)


def test_credentials_forwarder_collects_first_positional_route():
    source = 'self._with_credentials("/git/pull", path, username, password)'
    uses = _collect_python_route_uses_from_tree(ast.parse(source), Path("fixture.py"), source)
    assert [(use.method, use.path) for use in uses] == [("POST", "/git/pull")]


def _route_variants_from_expr(expr: ast.expr, resolver: Callable[[str], ast.expr | None]) -> list[str]:
    # Awaiting an expression does not change the route it names.
    while isinstance(expr, ast.Await):
        expr = expr.value
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, ast.JoinedStr):
        parts: list[list[str]] = []
        for index, value in enumerate(expr.values):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append([value.value])
                continue
            if isinstance(value, ast.FormattedValue):
                variants = _route_variants_from_formatted_value(
                    value.value,
                    resolver,
                    _joined_str_suffix(expr.values[index + 1 :]),
                )
                parts.append(variants if variants else ["{param}"])
        return _combine_string_parts(parts)
    if isinstance(expr, ast.Name):
        resolved = resolver(expr.id)
        return _route_variants_from_expr(resolved, resolver) if resolved is not None else []
    if isinstance(expr, ast.IfExp):
        return _unique_strings(
            _route_variants_from_expr(expr.body, resolver) + _route_variants_from_expr(expr.orelse, resolver)
        )
    if isinstance(expr, ast.Call):
        helper_routes = _known_route_helper_variants(expr, "")
        if helper_routes:
            return helper_routes
    return []


def _route_variants_from_formatted_value(
    expr: ast.expr,
    resolver: Callable[[str], ast.expr | None],
    suffix: str,
) -> list[str]:
    if isinstance(expr, ast.Name):
        resolved = resolver(expr.id)
        if resolved is not None:
            helper_routes = _known_route_helper_variants(resolved, suffix)
            if helper_routes:
                return helper_routes
    helper_routes = _known_route_helper_variants(expr, suffix)
    if helper_routes:
        return helper_routes
    variants = _route_variants_from_expr(expr, resolver)
    return variants if variants else ["{param}"]


def _known_route_helper_variants(expr: ast.expr, suffix: str) -> list[str]:
    # Resolve helper calls through their await expression.
    while isinstance(expr, ast.Await):
        expr = expr.value
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "_create_path":
        return [
            "/sandboxes",
            "/sandboxes/from-snapshot",
        ]
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr == "_ports_path":
            return ["/sandboxes/{param}/ports"]
        if expr.func.attr == "_port_path":
            return ["/sandboxes/{param}/ports/{param}"]
    return []


def _joined_str_suffix(values: list[ast.expr]) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{param}")
    return "".join(parts)


def _combine_string_parts(parts: list[list[str]]) -> list[str]:
    variants = [""]
    for part in parts:
        variants = [prefix + suffix for prefix in variants for suffix in part]
    return _unique_strings(variants)


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _normalize_sdk_route(raw: str) -> str | None:
    stripped_query = raw.split("?", 1)[0]
    indices = [
        (stripped_query.index(prefix), prefix)
        for prefix in _CONTRACT_ROUTE_PREFIXES
        if prefix in stripped_query
    ]
    path = stripped_query[min(indices)[0] :] if indices else stripped_query
    if not path.startswith("/"):
        return None
    normalized_segments = [""]
    for segment in path.split("/")[1:]:
        if not segment or segment == "{param}":
            normalized_segments.append(segment)
            continue
        if "{query}" in segment:
            normalized_segments.append(segment.split("{query}", 1)[0])
            continue
        if "{param}" in segment:
            prefix = segment.split("{param}", 1)[0]
            normalized_segments.append(prefix or "{param}")
            continue
        normalized_segments.append(segment)
    normalized = "/".join(normalized_segments).rstrip("/")
    return normalized or "/"


def _route_matches_spec(route_path: str, spec_path: str) -> bool:
    route_segments = [segment for segment in route_path.split("/") if segment]
    spec_segments = [segment for segment in spec_path.split("/") if segment]
    if len(route_segments) != len(spec_segments):
        return False
    for route_segment, spec_segment in zip(route_segments, spec_segments):
        if route_segment == "{param}":
            if not _is_spec_param(spec_segment):
                return False
            continue
        if _is_spec_param(spec_segment):
            continue
        if route_segment != spec_segment:
            return False
    return True


def _route_matches_spec_exactly(route_path: str, spec_path: str) -> bool:
    """``_route_matches_spec`` without the wildcard: every literal segment must be
    equal, and only a route ``{param}`` may stand in for a spec parameter."""
    route_segments = [segment for segment in route_path.split("/") if segment]
    spec_segments = [segment for segment in spec_path.split("/") if segment]
    if len(route_segments) != len(spec_segments):
        return False
    for route_segment, spec_segment in zip(route_segments, spec_segments):
        if route_segment == "{param}":
            if not _is_spec_param(spec_segment):
                return False
            continue
        if route_segment != spec_segment:
            return False
    return True


def _is_spec_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _nearest_spec_paths(method: str, route_path: str, operations: list[tuple[str, str]]) -> list[str]:
    route_segments = [segment for segment in route_path.split("/") if segment]
    ranked = []
    for op_method, op_path in operations:
        if op_method != method:
            continue
        spec_segments = [segment for segment in op_path.split("/") if segment]
        shared_literals = sum(
            3
            for route_segment, spec_segment in zip(route_segments, spec_segments)
            if not _is_spec_param(route_segment)
            and not _is_spec_param(spec_segment)
            and route_segment == spec_segment
        )
        ranked.append((shared_literals - abs(len(route_segments) - len(spec_segments)), op_path))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in ranked[:3]]


def _sandbox_detail_payload(
    sandbox_id: str,
    status: str,
    *,
    name: str | None = None,
    region: str = "region-b",
    toolbox_proxy_url: str = "",
    sandbox_class: str = "linux",
    persistent: bool = False,
    cores_max: float = 1,
    cpu: float = 1,
    memory_mib: int = 1024,
    disk_mib: int = 2048,
    lifecycle_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "class": sandbox_class,
        "cpu": cpu,
        "id": sandbox_id,
        "image": {"id": "img_py", "name": "python:3.12"},
        "snapshot": None,
        "memory_mib": memory_mib,
        "name": name or sandbox_id,
        "port_map": [],
        "region_id": region,
        "disk_mib": disk_mib,
        "status": status,
        "cores_max": cores_max,
        "lifecycle_policy": lifecycle_policy,
        "persistent": persistent,
        "spot": False,
        "toolbox_proxy_url": toolbox_proxy_url,
    }


def _sandbox_page_payload(
    sandbox_id: str,
    status: str,
    *,
    name: str | None = None,
    region: str = "region-b",
    toolbox_proxy_url: str | None = "http://toolbox",
    sandbox_class: str = "linux",
    cores_max: float = 1,
    cpu: float = 1,
    memory_mib: int = 1024,
    disk_mib: int = 2048,
) -> dict[str, object]:
    return {
        "class": sandbox_class,
        "cpu": cpu,
        "id": sandbox_id,
        "image": {"id": "img_py", "name": "python:3.12"},
        "snapshot": None,
        "memory_mib": memory_mib,
        "name": name or sandbox_id,
        "port_map": [],
        "region_id": region,
        "disk_mib": disk_mib,
        "status": status,
        "cores_max": cores_max,
        "lifecycle_policy": None,
        "persistent": False,
        "spot": False,
        "created_at": _DEFAULT_TIME,
        "cursor": sandbox_id,
        "toolbox_proxy_url": toolbox_proxy_url,
    }


def _process_payload(
    process_id: str,
    kind: str,
    state: str,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": process_id,
        "kind": kind,
        "state": state,
        "createdAt": _DEFAULT_TIME,
        "cwd": "/workspace",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "truncatedHead": False,
    }
    payload.update(overrides)
    return payload


def _log_frame_payload(
    cursor: str, data: str, *, channel: str = "stdout", timestamp: int = 1
) -> dict[str, object]:
    return {"cursor": cursor, "channel": channel, "data": data, "timestamp": timestamp}


def _log_page_payload(
    frames: list[dict[str, object]],
    *,
    next_cursor: str,
    eof: bool = False,
    truncated_head: bool = False,
) -> dict[str, object]:
    return {
        "frames": frames,
        "nextCursor": next_cursor,
        "eof": eof,
        "truncatedHead": truncated_head,
    }


def _session_payload(session_id: str, *, kernel: str = "shell") -> dict[str, object]:
    return {
        "id": session_id,
        "active": True,
        "createdAt": _DEFAULT_TIME,
        "cwd": "/workspace",
        "kernel": kernel,
        "kernelProcessId": "kp-1",
        "processIds": [],
    }


def _git_status_payload(branch: str) -> dict[str, object]:
    return {
        "ahead": 0,
        "behind": 0,
        "branchPublished": False,
        "currentBranch": branch,
        "fileStatus": [],
    }


def _commit_info_payload(commit_hash: str) -> dict[str, object]:
    return {
        "author": "Ada",
        "email": "ada@example.com",
        "hash": commit_hash,
        "message": "msg",
        "timestamp": _DEFAULT_TIME,
    }


def _image_operation_payload(image_id: str) -> dict[str, object]:
    return {"id": image_id, "status": "pending"}


def _image_page_payload(image_id: str) -> dict[str, object]:
    return {
        "anonymous": False,
        "region_ids": ["region-b"],
        "class": "linux",
        "created_at": _DEFAULT_TIME,
        "updated_at": _DEFAULT_TIME,
        "format_outdated": False,
        "format_version": 2,
        "id": image_id,
        "name": image_id,
        "origin": "registry",
        "region_id": "region-b",
        "retention": "permanent",
        "source": {"type": "registry", "ref": "python:3.12"},
        "status": "ready",
        "cursor": image_id,
        "platform": False,
        "default": False,
        "deprecated": False,
    }


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"type": "registry", "ref": "python:3.12"}, RegistryImageSource(ref="python:3.12")),
        (
            {"type": "dockerfile", "context_object": "ctx", "dockerfile": "Dockerfile"},
            DockerfileImageSource(context_object="ctx", dockerfile="Dockerfile"),
        ),
        ({"type": "named", "name": "base"}, NamedImageSource(name="base")),
        ({"type": "manifest", "name": "base"}, ManifestImageSource(name="base")),
    ],
)
def test_image_source_is_a_frozen_discriminated_union(source: dict[str, str], expected: ImageSource) -> None:
    payload = {**_image_page_payload("img"), "source": source}

    image = parse_image(payload)

    assert image.source == expected
    assert image.source is not None and image.source.type == source["type"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        image.source.type = "registry"  # type: ignore[misc]


def test_contract_enums_normalize_unknown_image_retention_and_snapshot_kind() -> None:
    image = parse_image({**_image_page_payload("img"), "retention": "future"})
    snapshot = parse_snapshot(
        {
            "id": "snap",
            "name": "snap",
            "status": "ready",
            "region_id": "region-b",
            "kind": "future",
            "created_at": "2026-01-01T00:00:00Z",
            "class": "linux",
            "region_ids": [],
        }
    )

    assert (image.retention, image.raw_retention) == ("unknown", "future")
    assert (snapshot.kind, snapshot.raw_kind) == ("unknown", "future")


def _secret_list_item_payload(name: str) -> dict[str, object]:
    return {
        "allowed_domains": [],
        "name": name,
        "created_at": _DEFAULT_TIME,
        "updated_at": _DEFAULT_TIME,
    }


def _secret_payload(name: str) -> dict[str, object]:
    return {
        "allowed_domains": [],
        "name": name,
        "created_at": _DEFAULT_TIME,
        "updated_at": _DEFAULT_TIME,
    }


def _snapshot_operation_payload(snapshot_id: str) -> dict[str, object]:
    return {"id": snapshot_id, "status": "pending"}


def _metrics_payload(*, cpu_count: int) -> dict[str, object]:
    return {
        "timestamp": _DEFAULT_TIME,
        "cpuCount": cpu_count,
        "cpuUtilization": 0.25,
        "memoryUsageBytes": 10,
        "memoryTotalBytes": 20,
        "diskUsageBytes": 30,
        "diskTotalBytes": 40,
    }


def bind_api(client: AsyncDaytona, api: RecordingTransport) -> None:
    client._api = api
    client.images._transport = api  # type: ignore[attr-defined]
    client.snapshots._transport = api  # type: ignore[attr-defined]
    client.volumes._transport = api  # type: ignore[attr-defined]
    client.secrets._transport = api  # type: ignore[attr-defined]


def test_there_is_no_public_sandbox_constructor() -> None:
    """A sandbox comes from create*(), get() or list(), with its toolbox URL from the API."""
    from daytona.v2._sync import Daytona
    from daytona.v2.daytona import AsyncDaytona

    assert not hasattr(AsyncDaytona, "sandbox")
    assert not hasattr(Daytona, "sandbox")


@pytest.mark.anyio
async def test_sandbox_handle_defers_missing_toolbox_until_namespace_use():
    from daytona.v2.daytona import AsyncDaytona
    from daytona.v2.types import DaytonaConfig

    sandbox = lazy_sandbox(AsyncDaytona(DaytonaConfig(api_key="key")), "sbx")
    assert sandbox.id == "sbx"
    with pytest.raises(
        Exception,
        match=re.escape(
            "sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolbox_url)"
        ),
    ):
        await sandbox.process.run("echo hi")


def test_toolbox_proxy_url_is_accepted():
    from daytona.v2.daytona import AsyncDaytona
    from daytona.v2.types import DaytonaConfig

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = client._sandbox_from_sandbox(
        _sandbox_detail_payload("sbx", "running", toolbox_proxy_url="http://toolbox")
    )
    assert sandbox._toolbox_proxy_url == "http://toolbox/sbx"


@respx.mock
@pytest.mark.anyio
async def test_daytona_create_posts_to_the_configured_base_even_for_an_explicit_remote_target() -> None:
    base_url = "https://api.example.com"
    regions_route = respx.get(f"{base_url}/regions").mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(200, json={"id": "sbx"})
    )
    get_route = respx.get(f"{base_url}/sandboxes/sbx").mock(
        return_value=httpx.Response(200, json=_sandbox_detail_payload("sbx", "running", region="region-c"))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = await client.create(Image.base("python:3.12"), region="region-c")
    finally:
        await client.close()

    assert sandbox.id == "sbx"
    assert create_route.call_count == 1
    assert create_route.calls[0].request.url == httpx.URL(f"{base_url}/sandboxes")
    assert regions_route.call_count == 0
    assert get_route.call_count == 1


@pytest.mark.parametrize(
    ("read", "path"),
    [
        (lambda client: client.list(), "/sandboxes"),
        (lambda client: client.search(name_prefix="sbx"), "/sandboxes/search"),
        (lambda client: client.search(states=["started"]), "/sandboxes/search"),
    ],
    ids=["list", "search-prefix", "search"],
)
@respx.mock
@pytest.mark.anyio
async def test_listed_handles_reach_each_sandboxs_regional_toolbox_without_a_refresh(read, path) -> None:
    base_url = "https://api.example.com"
    endpoint_a, endpoint_b = (
        "https://proxy.region-a.example/sandboxes",
        "https://proxy.region-b.example/sandboxes",
    )
    sandboxes = [
        _sandbox_page_payload("sbx-a", "running", region="region-a", toolbox_proxy_url=endpoint_a),
        _sandbox_page_payload("sbx-b", "running", region="region-b", toolbox_proxy_url=endpoint_b),
    ]
    page_route = respx.get(f"{base_url}{path}").mock(
        return_value=httpx.Response(
            200, json={"sandboxes": sandboxes, "next_cursor": None, "total": 2, "total_relation": "eq"}
        )
    )
    detail_route = respx.get(url__regex=rf"^{base_url}/sandboxes/sbx-[ab]$").mock(
        return_value=httpx.Response(500)
    )
    respx.post(url__regex=rf"^{base_url}/sandboxes/sbx-[ab]/access-token$").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "jwt",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
        )
    )
    metrics = {
        "timestamp": "2026-10-08T00:00:00Z",
        "cpuCount": 1,
        "cpuUtilization": 0,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }
    route_a = respx.get(f"{endpoint_a}/sbx-a/system/metrics").mock(
        return_value=httpx.Response(200, json=metrics)
    )
    route_b = respx.get(f"{endpoint_b}/sbx-b/system/metrics").mock(
        return_value=httpx.Response(200, json=metrics)
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        page = await read(client)
        for sandbox in page.items:
            await sandbox.metrics()
    finally:
        await client.close()

    assert page_route.call_count == 1
    assert detail_route.call_count == 0
    assert route_a.call_count == 1
    assert route_b.call_count == 1
    assert route_a.calls[0].request.headers["authorization"] == "Bearer jwt"


@respx.mock
@pytest.mark.anyio
async def test_create_handle_uses_the_create_response_toolbox_url_without_a_detail_fetch() -> None:
    base_url = "https://api.example.com"
    create_base = "https://proxy.create.example/sandboxes"
    create_route = respx.post(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "create-token",
                "status": "running",
                "toolbox_proxy_url": create_base,
                "id": "sbx",
                "wait_cap_seconds": 60,
            },
        )
    )
    detail_route = respx.get(f"{base_url}/sandboxes/sbx").mock(return_value=httpx.Response(500))
    metrics_route = respx.get(f"{create_base}/sbx/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=7))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = await client.create(Image.base("python:3.12"), wait_seconds=60)
        metrics = await sandbox.metrics()
    finally:
        await client.close()

    assert metrics.cpu_count == 7
    assert create_route.call_count == 1
    assert detail_route.call_count == 0
    assert metrics_route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_fork_handle_takes_metadata_from_the_detail_and_toolbox_from_the_fork_response() -> None:
    base_url = "https://api.example.com"
    fork_base = "https://proxy.fork.example/sandboxes"
    fork_route = respx.post(f"{base_url}/sandboxes/parent/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "forked_from": "parent",
                "status": "running",
                "toolbox_proxy_url": fork_base,
                "id": "forked",
            },
        )
    )
    # A waiting fork reads its source's state first, then the fork's detail.
    respx.get(f"{base_url}/sandboxes/parent").mock(
        return_value=httpx.Response(200, json=_sandbox_detail_payload("parent", "running"))
    )
    detail_route = respx.get(f"{base_url}/sandboxes/forked").mock(
        return_value=httpx.Response(200, json=_sandbox_detail_payload("forked", "running", name="the-fork"))
    )
    token_route = respx.post(f"{base_url}/sandboxes/forked/access-token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "jwt",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
        )
    )
    metrics_route = respx.get(f"{fork_base}/forked/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=8))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = lazy_sandbox(client, "parent", toolbox_url="http://10.0.0.7:2280")
        sandbox._update(sandbox_class="linux")
        forked = await sandbox.fork(wait_seconds=5)
        metrics = await forked.metrics()
    finally:
        await client.close()

    assert metrics.cpu_count == 8
    assert forked.name == "the-fork"
    assert fork_route.call_count == 1
    assert detail_route.call_count == 1
    assert token_route.call_count == 1
    assert metrics_route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_sandboxes_without_toolbox_proxy_url_resolve_their_details_concurrently() -> None:
    base_url = "https://api.example.com"
    ids = ["a", "b", "c"]
    respx.get(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [_sandbox_page_payload(i, "running", toolbox_proxy_url=None) for i in ids],
                "next_cursor": None,
                "total": 0,
                "total_relation": "eq",
            },
        )
    )
    in_flight: set[str] = set()
    all_in_flight = asyncio.Event()

    async def detail(request: httpx.Request) -> httpx.Response:
        sandbox_id = request.url.path.rsplit("/", 1)[-1]
        in_flight.add(sandbox_id)
        if len(in_flight) == len(ids):
            all_in_flight.set()
        # A sequential resolver never has a second read in flight: this wait
        # would time out on the first item.
        await asyncio.wait_for(all_in_flight.wait(), timeout=2)
        return httpx.Response(
            200,
            json=_sandbox_detail_payload(
                sandbox_id, "running", toolbox_proxy_url="https://p.example/sandboxes"
            ),
        )

    respx.get(url__regex=rf"^{base_url}/sandboxes/[abc]$").mock(side_effect=detail)
    client = AsyncDaytona(_config_at(base_url, api_key="key"))
    try:
        page = await client.list()
    finally:
        await client.close()

    assert [s._toolbox_proxy_url for s in page.items] == [f"https://p.example/sandboxes/{i}" for i in ids]


@respx.mock
@pytest.mark.anyio
async def test_list_without_toolbox_proxy_url_fetches_detail_once_before_building_the_handle() -> None:
    base_url = "https://api.example.com"
    detail_base = "https://proxy.detail.example/sandboxes"
    page_route = respx.get(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [_sandbox_page_payload("sbx", "running", toolbox_proxy_url=None)],
                "next_cursor": None,
                "total": 0,
                "total_relation": "eq",
            },
        )
    )
    detail_route = respx.get(f"{base_url}/sandboxes/sbx").mock(
        return_value=httpx.Response(
            200, json=_sandbox_detail_payload("sbx", "running", toolbox_proxy_url=detail_base)
        )
    )
    token_route = respx.post(f"{base_url}/sandboxes/sbx/access-token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "jwt",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
        )
    )
    metrics_route = respx.get(f"{detail_base}/sbx/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=6))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        page = await client.list()
        metrics = await page.items[0].metrics()
    finally:
        await client.close()

    assert metrics.cpu_count == 6
    assert page_route.call_count == 1
    assert detail_route.call_count == 1
    assert token_route.call_count == 1
    assert metrics_route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_get_ignores_daytona_toolbox_url_env_and_lazy_handles_still_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url = "https://api.example.com"
    detail_base = "https://proxy.detail.example/sandboxes"
    monkeypatch.setenv("DAYTONA_TOOLBOX_URL", "https://ignored.example/sandboxes")
    get_route = respx.get(f"{base_url}/sandboxes/sbx").mock(
        return_value=httpx.Response(
            200, json=_sandbox_detail_payload("sbx", "running", toolbox_proxy_url=detail_base)
        )
    )
    token_route = respx.post(f"{base_url}/sandboxes/sbx/access-token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "jwt",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
        )
    )
    metrics_route = respx.get(f"{detail_base}/sbx/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=5))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = await client.get("sbx")
        metrics = await sandbox.metrics()
        assert metrics.cpu_count == 5
        with pytest.raises(
            DaytonaError,
            match=re.escape(
                "sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolbox_url)"
            ),
        ):
            await lazy_sandbox(client, "lazy").metrics()
    finally:
        await client.close()

    assert get_route.call_count == 1
    assert token_route.call_count == 1
    assert metrics_route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_daytona_get_uses_the_configured_base_for_region_tagged_ids() -> None:
    base_url = "https://api.example.com"
    regions_route = respx.get(f"{base_url}/regions").mock(return_value=httpx.Response(200, json=[]))
    get_route = respx.get(f"{base_url}/sandboxes/{TAGGED_SANDBOX_ID}").mock(
        return_value=httpx.Response(
            200, json=_sandbox_detail_payload(TAGGED_SANDBOX_ID, "running", region="region-c")
        )
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = await client.get(TAGGED_SANDBOX_ID)
    finally:
        await client.close()

    assert sandbox.id == TAGGED_SANDBOX_ID
    assert get_route.call_count == 1
    assert get_route.calls[0].request.url == httpx.URL(f"{base_url}/sandboxes/{TAGGED_SANDBOX_ID}")
    assert regions_route.call_count == 0


@respx.mock
@pytest.mark.anyio
async def test_daytona_get_quotes_reserved_path_characters_in_sandbox_ids() -> None:
    base_url = "https://api.example.com"
    sandbox_id = "sbx!'()*/slash"
    encoded = "sbx%21%27%28%29%2A%2Fslash"
    get_route = respx.get(f"{base_url}/sandboxes/{encoded}").mock(
        return_value=httpx.Response(200, json=_sandbox_detail_payload(sandbox_id, "running"))
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        sandbox = await client.get(sandbox_id)
    finally:
        await client.close()

    assert sandbox.id == sandbox_id
    assert get_route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_daytona_list_with_cursor_preserves_the_server_cursor_without_catalog_fanout() -> None:
    base_url = "https://api.example.com"
    region_c_url = "https://api.region-c.example.com"
    regions_route = respx.get(f"{base_url}/regions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "region-b", "status": "active"},
                {"id": "region-c", "status": "active", "api_base_url": region_c_url},
            ],
        )
    )
    base_list = respx.get(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [_sandbox_page_payload("sbx", "running")],
                "next_cursor": "next",
                "total": 0,
                "total_relation": "eq",
            },
        )
    )
    region_c_list = respx.get(f"{region_c_url}/sandboxes").mock(
        return_value=httpx.Response(
            200, json={"sandboxes": [], "next_cursor": "remote-next", "total": 0, "total_relation": "eq"}
        )
    )
    client = AsyncDaytona(_config_at(base_url, api_key="key"))

    try:
        page = await client.list(cursor="cursor-1", limit=10)
    finally:
        await client.close()

    assert [sandbox.id for sandbox in page.items] == ["sbx"]
    assert page.next_cursor == "next"
    assert base_list.call_count == 1
    assert base_list.calls[0].request.url == httpx.URL(f"{base_url}/sandboxes?cursor=cursor-1&limit=10")
    assert regions_route.call_count == 0
    assert region_c_list.call_count == 0


def test_package_all_symbols_resolve_and_computer_use_names_match_typescript_exports():
    module = importlib.import_module("daytona.v2")
    exported = set(module.__all__)
    assert "SandboxEventsConfig" in exported
    for name in exported:
        assert getattr(module, name) is not None

    hidden_suffixes = ("Request", "Options")

    typescript_exports = {
        name
        for name in _typescript_export_names(_repo_file("sdk-typescript/src/v2/index.ts"))
        if name.startswith("ComputerUse")
        and name not in {"ComputerUse", "ComputerUseTransport"}
        and not name.endswith(hidden_suffixes)
    }
    python_exports = {name for name in exported if name.startswith("ComputerUse")}
    assert python_exports == typescript_exports
    assert module.ProcessState == ProcessState
    assert module.SandboxWaitState == SandboxWaitState
    assert module.TransferProgressCallback == TransferProgressCallback
    assert module.WaitReason == WaitReason


@pytest.mark.anyio
async def test_sandbox_metrics_reads_system_metrics_from_toolbox():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://toolbox.invalid/sandboxes/sbx")
    transport = RecordingTransport(
        [
            {
                "timestamp": "2026-08-23T00:00:00Z",
                "cpuCount": 4,
                "cpuUtilization": 0.25,
                "memoryUsageBytes": 10,
                "memoryTotalBytes": 20,
                "diskUsageBytes": 30,
                "diskTotalBytes": 40,
            }
        ]
    )
    sandbox._toolbox = transport  # type: ignore[assignment]

    metrics = await sandbox.metrics()

    assert metrics.cpu_count == 4
    assert metrics.cpu_utilization == 0.25
    assert transport.specs[0].method == "GET"
    assert transport.specs[0].path == "/system/metrics"


@pytest.mark.anyio
async def test_refresh_data_marks_an_id_only_handle_as_metadata_bearing():
    """A handle built from a bare id serializes only what it knows; after
    refresh_data() applied full sandbox details, to_json() carries the metadata
    (labels, resources, state, ...) so a round-trip keeps it."""
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    payload = _sandbox_detail_payload("sbx", "running", toolbox_proxy_url="http://region-toolbox")
    payload["labels"] = {"team": "sdk"}
    client._api = RecordingTransport([payload])  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx")
    assert "labels" not in sandbox.to_json()

    await sandbox.refresh_data()

    serialized = sandbox.to_json()
    assert serialized["labels"] == {"team": "sdk"}
    assert serialized["state"] == "started"


@pytest.mark.anyio
async def test_direct_scoped_toolbox_url_is_not_scoped_again_on_refresh():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [_sandbox_detail_payload("sbx", "running", toolbox_proxy_url="http://region-toolbox")]
    )
    client._api = api  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://toolbox.invalid/sandboxes/sbx")

    await sandbox.refresh_data()

    assert sandbox._toolbox_proxy_url == "http://toolbox.invalid/sandboxes/sbx"


def test_empty_binary_downloads_remain_empty_bytes():
    response = httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"")
    assert _decode_response(response) == b""


@pytest.mark.anyio
async def test_filesystem_write_many_shapes_indexed_multipart_request():
    transport = RecordingTransport(
        [
            {
                "files": [{"path": "/tmp/a.txt", "bytes": 3}, {"path": "/tmp/b.txt", "bytes": 2}],
                "errors": ["file[2]: missing .path metadata"],
            }
        ]
    )
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]

    result = await fs.write_many({"/tmp/a.txt": b"AAA", "/tmp/b.txt": b"BB"})

    assert [(item.path, item.bytes) for item in result.files] == [("/tmp/a.txt", 3), ("/tmp/b.txt", 2)]
    assert result.errors == ["file[2]: missing .path metadata"]
    assert transport.specs[0].method == "POST"
    assert transport.specs[0].path == "/files/bulk-upload"
    assert transport.specs[0].timeout is None
    assert transport.specs[0].files == {
        "files[0].path": (None, "/tmp/a.txt"),
        "files[0].file": ("a.txt", b"AAA"),
        "files[1].path": (None, "/tmp/b.txt"),
        "files[1].file": ("b.txt", b"BB"),
    }


@pytest.mark.anyio
async def test_filesystem_write_many_rejects_binary_transport_payloads():
    transport = RecordingTransport([b"raw"])
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]

    with pytest.raises(Exception, match="expected JSON object"):
        await fs.write_many({"/tmp/a.txt": b"AAA"})


# `POST …/files/sign` answers; the SDK composes its own URL and reads only
# `signature` / `expires_at` — `url`, `method` and `path` are echoes it ignores.
def _sign_response(
    expires: int = 1_781_234_567, *, signature: str = SIGNED_FILE_TEST_SIGNATURE
) -> dict[str, object]:
    return {
        "url": "https://server.example/sandboxes/ignored/files/download?path=ignored",
        "method": "GET",
        "path": "/ignored",
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "signature": signature,
    }


def _presigned_sandbox(
    monkeypatch,
    responses: list[object],
    *,
    sandbox_class: str = "linux",
    sandbox_id: str = "sbx",
    toolbox_url: str | None = None,
):
    monkeypatch.setattr("daytona.v2.filesystem.time.time", lambda: 1_781_230_967.75)
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(
        client, sandbox_id, toolbox_url=toolbox_url or f"http://toolbox.invalid/sandboxes/{sandbox_id}"
    )
    sandbox._update(sandbox_class=sandbox_class)
    api = RecordingTransport(responses)
    sandbox._api = api  # type: ignore[attr-defined]
    return sandbox, api


@pytest.mark.anyio
async def test_presigned_download_url_posts_files_sign_and_composes_from_the_sdk_toolbox_base(monkeypatch):
    sandbox, api = _presigned_sandbox(monkeypatch, [_sign_response()])

    url = await sandbox.fs.presigned_download_url("/home/user/report.pdf", expires_in_seconds=3600)

    # The link lives on the handle's toolbox base, never on the server's `url` echo.
    assert url == (
        "http://toolbox.invalid/sandboxes/sbx/files/download?path=%2Fhome%2Fuser%2Freport.pdf"
        f"&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/sandboxes/sbx/files/sign")]
    # expires_at = floor(now) + expires_in_seconds; the path travels raw in the body.
    assert api.specs[0].json_body == {
        "method": "GET",
        "path": "/home/user/report.pdf",
        "expires_at": "2026-06-12T03:22:47Z",
    }


@pytest.mark.anyio
async def test_presigned_upload_url_signs_with_method_post_and_the_upload_route(monkeypatch):
    sandbox, api = _presigned_sandbox(monkeypatch, [_sign_response()])

    url = await sandbox.fs.presigned_upload_url("/tmp/incoming/data.bin")

    assert url == (
        "http://toolbox.invalid/sandboxes/sbx/files/upload?path=%2Ftmp%2Fincoming%2Fdata.bin"
        f"&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    # Default TTL is 3600 seconds.
    assert api.specs[0].json_body == {
        "method": "POST",
        "path": "/tmp/incoming/data.bin",
        "expires_at": "2026-06-12T03:22:47Z",
    }


@pytest.mark.anyio
async def test_presigned_urls_percent_encode_the_path_in_the_url_but_send_it_raw(monkeypatch):
    special = "/tmp/it's (a) file!*.txt"
    sandbox, api = _presigned_sandbox(monkeypatch, [_sign_response(), _sign_response()])

    assert await sandbox.fs.presigned_download_url("/tmp/a b/c.txt") == (
        "http://toolbox.invalid/sandboxes/sbx/files/download?path=%2Ftmp%2Fa%20b%2Fc.txt"
        f"&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert await sandbox.fs.presigned_download_url(special) == (
        "http://toolbox.invalid/sandboxes/sbx/files/download?path=%2Ftmp%2Fit%27s%20%28a%29%20file%21%2A.txt"
        f"&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert [spec.json_body["path"] for spec in api.specs] == ["/tmp/a b/c.txt", special]  # type: ignore[index]


@pytest.mark.anyio
async def test_presigned_urls_echo_the_server_expires_and_signature(monkeypatch):
    # The signature covers the server's `expires`; a link must carry the value
    # that was signed, not the one the SDK asked for.
    sandbox, _ = _presigned_sandbox(monkeypatch, [_sign_response(1_781_234_570, signature="v1_other")])

    url = await sandbox.fs.presigned_download_url("/tmp/demo.txt")

    assert url.endswith("?path=%2Ftmp%2Fdemo.txt&expires=1781234570&signature=v1_other")


@pytest.mark.anyio
async def test_presigned_urls_are_minted_per_call_with_no_client_side_cache(monkeypatch):
    sandbox, api = _presigned_sandbox(monkeypatch, [_sign_response(), _sign_response(), _sign_response()])

    await sandbox.fs.presigned_download_url("/tmp/demo.txt")
    await sandbox.fs.presigned_download_url("/tmp/demo.txt")
    await sandbox.fs.presigned_upload_url("/tmp/demo.txt")

    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/files/sign"] * 3


@pytest.mark.parametrize("bad", [0, -1, 604_801, 1.5, 3600.0, True, "3600", None, float("nan"), 2**63])
@pytest.mark.anyio
async def test_presigned_urls_reject_out_of_range_expiry_before_any_request(monkeypatch, bad):
    from daytona.v2 import SIGNED_FILE_MAX_TTL_SECONDS
    from daytona.v2.errors import BadRequestError

    assert SIGNED_FILE_MAX_TTL_SECONDS == 604_800
    sandbox, api = _presigned_sandbox(monkeypatch, [])

    for mint in (sandbox.fs.presigned_download_url, sandbox.fs.presigned_upload_url):
        with pytest.raises(BadRequestError) as info:
            await mint("/tmp/demo.txt", expires_in_seconds=bad)
        assert str(info.value) == "expires_in_seconds must be an integer between 1 and 604800 (7 days)"
        assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")

    assert api.specs == []


@pytest.mark.parametrize("ttl", [1, 604_800])
@pytest.mark.anyio
async def test_presigned_urls_accept_the_ttl_boundaries(monkeypatch, ttl):
    sandbox, api = _presigned_sandbox(monkeypatch, [_sign_response()])

    await sandbox.fs.presigned_download_url("/tmp/demo.txt", expires_in_seconds=ttl)

    assert api.specs[0].json_body == {
        "method": "GET",
        "path": "/tmp/demo.txt",
        "expires_at": datetime.fromtimestamp(1_781_230_967 + ttl, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


@pytest.mark.anyio
async def test_presigned_urls_surface_the_server_400_and_403_as_typed_errors(monkeypatch):
    from daytona.v2.errors import BadRequestError, ForbiddenError

    sandbox, api = _presigned_sandbox(
        monkeypatch,
        [
            create_error("expires is too far in the future", 400, "BAD_REQUEST", "DAYTONA_API"),
            create_error("missing permission write:sandboxes", 403, "FORBIDDEN", "DAYTONA_API"),
        ],
    )

    with pytest.raises(BadRequestError, match="expires is too far in the future") as bad_request:
        await sandbox.fs.presigned_download_url("/tmp/demo.txt")
    assert bad_request.value.status_code == 400
    with pytest.raises(ForbiddenError, match="missing permission write:sandboxes") as forbidden:
        await sandbox.fs.presigned_upload_url("/tmp/demo.txt")
    assert forbidden.value.status_code == 403
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/files/sign", "/sandboxes/sbx/files/sign"]


@pytest.mark.parametrize(
    "payload",
    [
        # Parity matrix with the TS suite ('reports a malformed files/sign
        # payload'): every non-object body and every malformed object answer
        # the same 500.
        None,
        {},
        [],
        "str",
        {"signature": "", "expires_at": 1},
        {"signature": "v1_x", "expires_at": 1.5},
        {"signature": "", "expires_at": "2026-06-12T03:22:47Z"},
        {"signature": "v1_ok"},
        {"signature": "v1_ok", "expires_at": "not-a-date"},
        {"signature": "v1_ok", "expires_at": "2026-06-12T03:22:47"},
        {"signature": "v1_ok", "expires_at": "1781234567"},
        {"signature": "v1_ok", "expires_at": True},
        {"signature": 7, "expires_at": "2026-06-12T03:22:47Z"},
    ],
)
@pytest.mark.anyio
async def test_presigned_urls_reject_a_malformed_sign_payload(monkeypatch, payload):
    sandbox, _ = _presigned_sandbox(monkeypatch, [payload])

    with pytest.raises(DaytonaError, match="API returned an invalid signed file URL payload") as info:
        await sandbox.fs.presigned_download_url("/tmp/demo.txt")
    assert info.value.status_code == 500


@pytest.mark.anyio
async def test_presigned_urls_reject_a_null_sign_body(monkeypatch):
    # JSON ``null`` decodes to None; the raw body reaches the payload parser,
    # which answers the same 500 invalid-payload error the TS SDK raises —
    # never the transport's 400 "expected JSON object" guard.
    sandbox, _ = _presigned_sandbox(monkeypatch, [None])

    with pytest.raises(DaytonaError, match="API returned an invalid signed file URL payload") as info:
        await sandbox.fs.presigned_download_url("/f.txt")
    assert info.value.status_code == 500
    assert info.value.code is None
    assert info.value.source == "SDK"


def test_signing_key_rotation_is_on_the_public_surface():
    from daytona.v2 import AsyncSandbox as PublicSandbox
    from daytona.v2 import Sandbox

    assert callable(PublicSandbox.rotate_signing_key)
    assert callable(Sandbox.rotate_signing_key)


# Signing-key rotation returns 204 without a body; the key never reaches the SDK.
@pytest.mark.anyio
async def test_rotate_signing_key_posts_signing_key_rotate_with_no_body_and_returns_none(monkeypatch):
    sandbox, api = _presigned_sandbox(monkeypatch, [None])

    assert await sandbox.rotate_signing_key() is None

    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/sandboxes/sbx/signing-key/rotate")]
    assert api.specs[0].json_body is None


@pytest.mark.anyio
async def test_rotate_signing_key_accepts_the_204_empty_body(monkeypatch):
    sandbox, _ = _presigned_sandbox(monkeypatch, [None])

    assert await sandbox.rotate_signing_key() is None


@pytest.mark.anyio
async def test_rotate_signing_key_surfaces_the_server_403_and_404_as_typed_errors(monkeypatch):
    from daytona.v2.errors import ForbiddenError, NotFoundError

    sandbox, api = _presigned_sandbox(
        monkeypatch,
        [
            create_error("missing permission write:sandboxes", 403, "FORBIDDEN", "DAYTONA_API"),
            create_error("sandbox not found", 404, "NOT_FOUND", "DAYTONA_API"),
        ],
    )

    with pytest.raises(ForbiddenError, match="missing permission write:sandboxes") as forbidden:
        await sandbox.rotate_signing_key()
    assert forbidden.value.status_code == 403
    with pytest.raises(NotFoundError, match="sandbox not found") as not_found:
        await sandbox.rotate_signing_key()
    assert not_found.value.status_code == 404
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/signing-key/rotate"] * 2


def test_sync_rotate_signing_key_blocks_on_the_async_twin(monkeypatch):
    from daytona.v2._sync import Daytona

    config = DaytonaConfig(api_key="key")
    with Daytona(config) as client:
        sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://toolbox.invalid/sandboxes/sbx")
        sandbox._impl._update(sandbox_class="linux")
        api = RecordingTransport([None])
        sandbox._impl._api = api  # type: ignore[attr-defined]

        assert sandbox.rotate_signing_key() is None

    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/sandboxes/sbx/signing-key/rotate")]


@pytest.mark.anyio
async def test_presigned_urls_use_proxy_scoped_toolbox_urls_without_a_direct_override(monkeypatch):
    sandbox, api = _presigned_sandbox(
        monkeypatch,
        [_sign_response()],
        toolbox_url="https://tb.eu.example.com/sandboxes/sbx",
    )

    assert await sandbox.fs.presigned_download_url("/tmp/demo.txt") == (
        f"https://tb.eu.example.com/sandboxes/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/files/sign"]


@pytest.mark.anyio
async def test_presigned_urls_require_sandbox_scoped_toolbox_url():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://127.0.0.1:2280")
    api = RecordingTransport([])
    sandbox._api = api  # type: ignore[attr-defined]

    with pytest.raises(DaytonaError, match=re.escape(_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE)):
        await sandbox.fs.presigned_download_url("/tmp/demo.txt")
    with pytest.raises(DaytonaError, match=re.escape(_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE)):
        await sandbox.fs.presigned_upload_url("/tmp/demo.txt")
    with pytest.raises(DaytonaError, match=re.escape(_CUSTOM_TOOLBOX_PRESIGNED_FILE_URLS_MESSAGE)):
        await sandbox.rotate_signing_key()

    assert api.specs == []


@pytest.mark.anyio
async def test_presigned_urls_accept_proxy_scoped_toolbox_override(monkeypatch):
    sandbox, api = _presigned_sandbox(
        monkeypatch, [_sign_response()], toolbox_url="http://127.0.0.1:9000/sandboxes/sbx"
    )

    assert await sandbox.fs.presigned_download_url("/tmp/demo.txt") == (
        f"http://127.0.0.1:9000/sandboxes/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/files/sign"]


@pytest.mark.parametrize("placeholder", ["{sandbox_id}", "{sandboxId}"])
@pytest.mark.anyio
async def test_presigned_urls_accept_proxy_scoped_toolbox_template_override(monkeypatch, placeholder):
    # Both spellings are documented in README.md; a template copied from a
    # TypeScript integration must work here too.
    sandbox, api = _presigned_sandbox(
        monkeypatch, [_sign_response()], toolbox_url=f"https://tb.example.com/sandboxes/{placeholder}"
    )

    assert sandbox._toolbox_proxy_url == "https://tb.example.com/sandboxes/sbx"
    assert await sandbox.fs.presigned_download_url("/tmp/demo.txt") == (
        f"https://tb.example.com/sandboxes/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature={SIGNED_FILE_TEST_SIGNATURE}"
    )
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/files/sign"]


@pytest.mark.anyio
async def test_schemeless_proxy_scoped_override_is_classified_not_rejected(monkeypatch):
    # Parity: TypeScript must not throw a raw TypeError from WHATWG URL on a
    # schemeless override; both SDKs recognize a sandbox-scoped URL.
    sandbox, _ = _presigned_sandbox(
        monkeypatch, [_sign_response()], sandbox_id="abc", toolbox_url="proxy.example/sandboxes/abc"
    )

    assert (await sandbox.fs.presigned_download_url("/tmp/demo.txt")).startswith(
        "proxy.example/sandboxes/abc/files/download?"
    )


@pytest.mark.parametrize(
    ("sandbox_id", "encoded"),
    [
        ("seg A/B", "seg%20A%2FB"),
        # !'()* are the characters encodeURIComponent leaves bare and quote(safe='')
        # encodes; both SDKs must agree byte-for-byte.
        ("it's (a) file!*", "it%27s%20%28a%29%20file%21%2A"),
    ],
)
def test_toolbox_template_percent_encodes_the_sandbox_id_like_typescript(sandbox_id, encoded):
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, sandbox_id, toolbox_url="https://tb.example.com/sandboxes/{sandbox_id}")
    assert sandbox._toolbox_proxy_url == f"https://tb.example.com/sandboxes/{encoded}"


@pytest.mark.parametrize(
    "template",
    [
        "https://{sandbox_id}.example.com/sandboxes",
        "https://tb.example.com:{sandboxId}/sandboxes",
        "https://tb.example.com/sandboxes?id={sandbox_id}",
        "https://tb.example.com/sandboxes#{sandboxId}",
        # A '/' inside the query must not be mistaken for the path start.
        "https://tb.example.com?x=/sandboxes/{sandbox_id}",
        "https://tb.example.com#/sandboxes/{sandboxId}",
    ],
)
def test_toolbox_template_placeholder_in_the_host_is_rejected(template):
    # The placeholder is a path segment; anywhere else (host, port, query,
    # fragment) is outside the supported URL template syntax.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    with pytest.raises(DaytonaError, match="only supported in the path"):
        lazy_sandbox(client, "sbx", toolbox_url=template)


@pytest.mark.anyio
async def test_fork_does_not_inherit_the_parent_toolbox_override():
    # A fork resolves its own toolbox URL rather than reusing its parent's override.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://10.0.0.7:2280")
    sandbox._update(sandbox_class="linux")
    api = RecordingTransport(
        [
            {
                "forked_from": "sbx",
                "status": "pending",
                "toolbox_proxy_url": "https://proxy.example",
                "id": "forked",
            },
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    forked = await sandbox.fork(wait_seconds=0)

    assert forked._toolbox_proxy_url == "https://proxy.example/forked"
    assert forked._toolbox_proxy_url != sandbox._toolbox_proxy_url
    # wait_seconds=0 is one request, as for create: no detail read.
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/fork"]
    # ...but the events hub IS inherited: it is per process and identity, not
    # per sandbox. A hub-less fork would long-poll every wait, silently.
    assert sandbox._hub is not None
    assert forked._hub is sandbox._hub


@pytest.mark.anyio
async def test_fork_sends_memory_only_when_given():
    # memory=False asks for a disk-only fork; an omitted
    # argument leaves the server default (a memory fork) in charge.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(sandbox_class="linux")
    api = RecordingTransport(
        [
            {
                "forked_from": "sbx",
                "status": "pending",
                "toolbox_proxy_url": "https://proxy.example",
                "id": "a",
            },
            {
                "forked_from": "sbx",
                "status": "pending",
                "toolbox_proxy_url": "https://proxy.example",
                "id": "b",
            },
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    assert (await sandbox.fork(name="disk", memory=False, wait_seconds=0)).id == "a"
    assert (await sandbox.fork(wait_seconds=0)).id == "b"
    assert api.specs[0].json_body == {"name": "disk", "memory": False}
    assert api.specs[1].json_body == {}


@pytest.mark.anyio
async def test_fork_of_a_macos_sandbox_reaches_the_server():
    # A macOS fork copies the disk only; the API enforces that rule.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "mac")
    sandbox._update(sandbox_class="macos")
    api = RecordingTransport(
        [
            {
                "forked_from": "mac",
                "status": "pending",
                "toolbox_proxy_url": "https://proxy.example",
                "id": "mac-fork",
            },
            _sandbox_detail_payload("mac-fork", "pending"),
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    assert (await sandbox.fork(name="copy", wait_seconds=0)).id == "mac-fork"
    assert api.specs[0].method == "POST"
    assert api.specs[0].path.endswith("/mac/fork")
    assert api.specs[0].json_body == {"name": "copy"}


@pytest.mark.anyio
async def test_macos_fork_with_timeout_settles_on_started_or_stopped():
    # A macOS fork may finish started or stopped regardless of the cached source state.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "mac")
    sandbox._update(sandbox_class="macos")
    fork_resp = {
        "forked_from": "mac",
        "status": "pending",
        "toolbox_proxy_url": "https://proxy.example",
        "id": "f",
    }
    api = RecordingTransport(
        [
            fork_resp,
            _sandbox_detail_payload("f", "pending", sandbox_class="macos"),
            _sandbox_detail_payload("f", "stopped", sandbox_class="macos"),
            fork_resp,
            _sandbox_detail_payload("f", "pending", sandbox_class="macos"),
            _sandbox_detail_payload("f", "running", sandbox_class="macos"),
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    assert (await sandbox.fork(wait_seconds=5)).state == "stopped"
    assert (await sandbox.fork(wait_seconds=5)).state == "started"
    assert not any(spec.path == "/sandboxes/mac" for spec in api.specs)
    assert api.responses == []

    with pytest.raises(DaytonaError) as refused:
        await sandbox.fork(wait_seconds=-1)
    assert refused.value.code == "BAD_REQUEST"


@pytest.mark.anyio
async def test_fork_without_a_response_toolbox_url_does_not_reuse_the_parents():
    # No URL in the response and no client base: the fork must fail loudly on
    # toolbox use, not silently target the parent's sandbox.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx", toolbox_url="http://10.0.0.7:2280")
    sandbox._update(sandbox_class="linux")
    api = RecordingTransport(
        [
            {"forked_from": "sbx", "status": "pending", "toolbox_proxy_url": "", "id": "forked"},
            _sandbox_detail_payload("forked", "running"),
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    forked = await sandbox.fork(wait_seconds=0)

    assert forked._toolbox_proxy_url is None
    with pytest.raises(DaytonaError):
        await forked.fs.list("/")


def test_bulk_upload_spec_encodes_text_parts_without_filenames():
    spec = file_bulk_upload_spec({"/tmp/a.txt": b"AAA", "/tmp/dir/b.txt": b"BB"}, timeout=9.0)
    request = httpx.Client().build_request(
        "POST", "http://toolbox.test/files/bulk-upload", **_request_kwargs(spec, 30.0)
    )

    body = request.read().decode("latin-1")

    assert 'name="files[0].path"; filename=' not in body
    assert 'name="files[1].path"; filename=' not in body
    assert 'name="files[0].path"\r\n\r\n/tmp/a.txt\r\n' in body
    assert 'name="files[0].file"; filename="a.txt"' in body
    assert 'name="files[1].path"\r\n\r\n/tmp/dir/b.txt\r\n' in body
    assert 'name="files[1].file"; filename="b.txt"' in body
    assert body.index('name="files[0].path"') < body.index('name="files[0].file"')
    assert body.index('name="files[1].path"') < body.index('name="files[1].file"')


@pytest.mark.anyio
async def test_filesystem_read_many_parses_multipart_successes_and_errors():
    boundary = "sdk-boundary-123"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="dir/?.txt"; filename*=utf-8\'\'dir%2F%C3%BC.txt\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
        "hello\r\n--DAYTONA-FILE-BOUNDARY\r\nworld\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="error"\r\n'
        "Content-Type: application/json\r\n"
        "\r\n"
        '{"path":"/nope.txt","code":"FILE_NOT_FOUND","message":"file not found: /nope.txt","source":"DAYTONA_DAEMON"}\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    class Transport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            self.calls.append((method, path, kwargs))
            return httpx.Response(
                200,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
                content=body,
            )

    transport = Transport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]

    result = await fs.read_many(["dir/ü.txt", "/nope.txt"])

    assert result.files == {"dir/ü.txt": b"hello\r\n--DAYTONA-FILE-BOUNDARY\r\nworld"}
    assert len(result.errors) == 1
    assert result.errors[0].path == "/nope.txt"
    assert result.errors[0].code == "FILE_NOT_FOUND"
    assert result.errors[0].message == "file not found: /nope.txt"
    assert result.errors[0].source == "DAYTONA_DAEMON"
    assert transport.calls == [
        (
            "POST",
            "/files/bulk-download",
            {"transfer": True, "json": {"paths": ["dir/ü.txt", "/nope.txt"]}},
        )
    ]


@pytest.mark.anyio
async def test_filesystem_read_many_keeps_payload_bytes_that_look_like_the_boundary():
    boundary = "sdk-boundary-123"
    payload = f"before\r\n--{boundary}X\r\nafter\r\n--{boundary}".encode() + b"\x00tail"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="bin.dat"\r\n'
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
        ).encode()
        + payload
        + f"\r\n--{boundary}--\r\n".encode()
    )

    class Transport:
        async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
                content=body,
            )

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]

    result = await fs.read_many(["bin.dat"])

    assert result.files == {"bin.dat": payload}
    assert result.errors == []


@pytest.mark.anyio
async def test_filesystem_watch_dir_waits_for_start_and_drains_events():
    stream = ControlledSseStream()

    class Transport:
        def __init__(self) -> None:
            self.specs: list[RequestSpec] = []

        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            self.specs.append(spec)
            return stream

    transport = Transport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    seen: list[tuple[FilesystemEventType, str]] = []
    callback_done = asyncio.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append((event.type, event.name))
        callback_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_event, recursive=True))
    await asyncio.sleep(0)
    assert watch_task.done() is False

    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)
    assert transport.specs[0].path == "/files/watch"
    assert transport.specs[0].params == {"path": "/workspace", "recursive": True}
    assert transport.specs[0].headers == {"Accept": "text/event-stream"}

    stream.push("event: fs")
    stream.push('data: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}')
    stream.push("")
    await asyncio.wait_for(callback_done.wait(), timeout=1)

    assert seen == [(FilesystemEventType.CREATE, "a.txt")]
    assert handle.get_new_events() == [
        FilesystemEvent(type=FilesystemEventType.CREATE, name="a.txt", is_dir=False, timestamp=1)
    ]
    assert handle.get_new_events() == []

    await handle.stop()
    await handle.stop()
    assert stream.closed is True


@pytest.mark.anyio
async def test_filesystem_watch_dir_raises_setup_error_before_returning_handle():
    setup_error = create_error("missing path", 404, code_names.FILE_NOT_FOUND, "DAYTONA_DAEMON")

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            raise setup_error

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]

    with pytest.raises(type(setup_error)) as exc_info:
        await fs.watch_dir("/missing")

    assert exc_info.value.code == code_names.FILE_NOT_FOUND
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_filesystem_watch_dir_pre_start_stream_failure_reaches_exit_error_and_on_exit():
    stream_error = RuntimeError("socket reset before the start frame")

    class FailingStream:
        async def iter_lines(self):
            raise stream_error
            yield  # pragma: no cover - makes this a generator

        async def close(self) -> None:
            pass

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> FailingStream:
            return FailingStream()

    seen: list[object] = []
    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError) as exc_info:
        await fs.watch_dir("/tmp", on_exit=seen.append)

    assert exc_info.value is stream_error
    assert seen == [stream_error]


@pytest.mark.anyio
async def test_filesystem_watch_dir_stop_suppresses_frames_already_buffered_with_start():
    # The start frame and a first fs frame commonly arrive in one
    # packet, so both are in the reader's buffer when watch_dir() returns.
    # stop() must not let that buffered fs frame reach the queue or callback.
    class PreloadedStream:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines
            self.closed = False
            self.closed_event = asyncio.Event()
            self.first_line_read = asyncio.Event()
            self.release = asyncio.Event()

        async def iter_lines(self):
            for index, line in enumerate(self._lines):
                if index == 3:
                    # start frame fully yielded; hold here until stop() ran.
                    self.first_line_read.set()
                    await asyncio.wait_for(self.release.wait(), timeout=2)
                yield line

        async def close(self) -> None:
            self.closed = True
            self.closed_event.set()

    stream = PreloadedStream(
        [
            "event: start",
            "data: {}",
            "",
            "event: fs",
            'data: {"type":"create","name":"demo.txt","isDir":false,"timestamp":1}',
            "",
        ]
    )

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> PreloadedStream:
            return stream

    delivered: list[FilesystemEvent] = []
    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    handle = await fs.watch_dir("/w", on_event=delivered.append)
    await asyncio.wait_for(stream.first_line_read.wait(), timeout=1)

    stopper = asyncio.create_task(handle.stop())
    # stop() sets _stopped and closes the stream before it joins the reader;
    # release the buffered frame only once that has happened.
    await asyncio.wait_for(stream.closed_event.wait(), timeout=1)
    stream.release.set()
    await asyncio.wait_for(stopper, timeout=2)
    assert await handle.wait_for_exit() is None

    assert delivered == []
    assert handle.get_new_events() == []
    assert stream.closed is True


@pytest.mark.anyio
async def test_filesystem_watch_dir_stop_against_a_real_one_packet_sse_server_delivers_nothing_after_stop():
    # The start frame and the first fs frame arrive in ONE TCP write, so both sit
    # in httpx's buffer when watch_dir() returns. stop() must guarantee that the
    # buffered fs frame is neither queued nor delivered to the callback after it
    # returns — independent of the reader join timeout.
    import socket

    body = (
        b"event: start\ndata: {}\n\n"
        b'event: fs\ndata: {"type":"create","name":"demo.txt","isDir":false,"timestamp":1}\n\n'
    )
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"
        b"Cache-Control: no-cache\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    stop_returned = threading.Event()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        try:
            conn.settimeout(2)
            conn.recv(65536)
            conn.sendall(head + body)  # one packet: start + fs together
            stop_returned.wait(timeout=5)  # keep the socket open until stop() has returned
        finally:
            conn.close()
            listener.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()

    transport = AsyncTransport(f"http://127.0.0.1:{port}")
    delivered: list[FilesystemEvent] = []
    try:
        handle = await AsyncFileSystemService(transport).watch_dir("/w", on_event=delivered.append)
        await handle.stop()
        # Snapshot at the instant stop() returns: this is the contract boundary.
        delivered_at_stop = list(delivered)
        queued_at_stop = handle.get_new_events()
        stop_returned.set()
        assert await handle.wait_for_exit() is None
        # Whatever reached the caller did so before stop() returned; the buffered
        # fs frame must not surface afterwards through either path.
        assert list(delivered) == delivered_at_stop
        assert handle.get_new_events() == []
        # The one buffered frame either reached both the callback and the queue
        # before stop() (delivered as a pair, by design) or neither — never one
        # without the other, and never partially after stop().
        assert len(delivered_at_stop) == len(queued_at_stop) <= 1
    finally:
        stop_returned.set()
        server.join(timeout=5)
        await transport.close()


@pytest.mark.anyio
async def test_filesystem_watch_dir_invokes_overflow_callback_and_tracks_count():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    overflow_count = 0
    overflow_done = asyncio.Event()

    def on_overflow() -> None:
        nonlocal overflow_count
        overflow_count += 1
        overflow_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_overflow=on_overflow))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    stream.push("event: overflow")
    stream.push('data: {"dropped":true}')
    stream.push("")
    await asyncio.wait_for(overflow_done.wait(), timeout=1)
    assert overflow_count == 1
    assert handle.overflow_count == 1

    await handle.stop()


@pytest.mark.anyio
async def test_filesystem_watch_dir_notifies_exit_once_on_stop_and_wait_for_exit_returns_none():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    exit_calls: list[Exception | None] = []
    exit_done = asyncio.Event()

    def on_exit(error: Exception | None) -> None:
        exit_calls.append(error)
        exit_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_exit=on_exit))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    await handle.stop()
    await handle.stop()

    await asyncio.wait_for(exit_done.wait(), timeout=1)
    assert exit_calls == [None]
    assert handle.exit_error is None
    assert await handle.wait_for_exit() is None


@pytest.mark.anyio
async def test_filesystem_watch_dir_tracks_overflow_without_callback():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    watch_task = asyncio.create_task(fs.watch_dir("/workspace"))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    stream.push("event: overflow")
    stream.push('data: {"dropped":true}')
    stream.push("")

    async def wait_for_overflow() -> None:
        while handle.overflow_count != 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_overflow(), timeout=1)
    assert handle.overflow_count == 1
    assert handle.get_new_events() == []

    await handle.stop()


@pytest.mark.anyio
async def test_filesystem_watch_dir_overflow_callback_errors_do_not_stop_future_events():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    overflow_calls = 0
    callback_done = asyncio.Event()

    def on_overflow() -> None:
        nonlocal overflow_calls
        overflow_calls += 1
        raise RuntimeError("overflow boom")

    def on_event(event: FilesystemEvent) -> None:
        if event.name == "b.txt":
            callback_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_event=on_event, on_overflow=on_overflow))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    stream.push("event: overflow")
    stream.push('data: {"dropped":true}')
    stream.push("")
    stream.push("event: fs")
    stream.push('data: {"type":"write","name":"b.txt","isDir":false,"timestamp":2}')
    stream.push("")

    await asyncio.wait_for(callback_done.wait(), timeout=1)
    assert overflow_calls == 1
    assert isinstance(handle.last_callback_error, RuntimeError)
    assert str(handle.last_callback_error) == "overflow boom"
    assert handle.overflow_count == 1
    assert handle.get_new_events() == [
        FilesystemEvent(type=FilesystemEventType.WRITE, name="b.txt", is_dir=False, timestamp=2),
    ]

    await handle.stop()


@pytest.mark.anyio
async def test_filesystem_watch_dir_stop_reports_a_close_failure_even_when_the_reader_exits_cleanly() -> None:
    """stop() closes the stream best-effort; if that close raises but the
    reader then exits normally (its own close is idempotent and quiet), the
    failure must still reach exit_error/on_exit rather than vanish."""

    class FailsOnceStream(ControlledSseStream):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()
            if self.close_calls == 1:
                raise OSError("first close failed")

    stream = FailsOnceStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    exits: list[Exception | None] = []
    handle = await fs.watch_dir("/workspace", on_exit=exits.append)

    await asyncio.wait_for(handle.stop(), timeout=2)

    assert isinstance(handle.exit_error, OSError)
    assert [type(error) for error in exits] == [OSError]


@pytest.mark.anyio
async def test_daytona_aexit_closes_transports_even_when_the_body_is_cancelled() -> None:
    """`async with Daytona(...)` under an anyio cancel scope: __aexit__ must
    still release both connection pools, not be cancelled along with the body."""
    import anyio

    closed: list[str] = []
    client = AsyncDaytona(DaytonaConfig(api_key="key"))

    async def close_api() -> None:
        await anyio.sleep(0.05)
        closed.append("api")

    async def close_toolbox() -> None:
        await anyio.sleep(0.05)
        closed.append("toolbox")

    client._api.close = close_api  # type: ignore[method-assign]
    client._toolbox.close = close_toolbox  # type: ignore[method-assign]

    with anyio.CancelScope() as scope:
        async with client:
            scope.cancel()
            await anyio.sleep(1)

    assert sorted(closed) == ["api", "toolbox"], f"transports leaked on cancelled exit: {closed}"


@pytest.mark.anyio
async def test_filesystem_watch_dir_stop_survives_a_stream_that_fails_to_close() -> None:
    """The reader closes the stream in its `finally`. If close() raises, the
    task must still record exit (waking stop()/wait_for_exit() and firing
    on_exit) and stop() must return cleanly — a failed teardown is reported
    through exit_error, not thrown at whoever called stop()."""

    class ExplodingCloseStream(ControlledSseStream):
        async def close(self) -> None:
            await super().close()
            raise OSError("close blew up")

    stream = ExplodingCloseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    exits: list[Exception | None] = []
    handle = await fs.watch_dir("/workspace", on_exit=exits.append)

    await asyncio.wait_for(handle.stop(), timeout=2)  # neither raises nor hangs on a missed exit

    assert isinstance(handle.exit_error, OSError)
    assert isinstance(await handle.wait_for_exit(), OSError)
    assert [type(error) for error in exits] == [OSError]


@pytest.mark.anyio
async def test_filesystem_watch_dir_reports_unexpected_exit_through_handle_and_callback():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    exit_calls: list[Exception | None] = []
    exit_done = asyncio.Event()

    def on_exit(error: Exception | None) -> None:
        exit_calls.append(error)
        exit_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_exit=on_exit))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    await stream.close()

    await asyncio.wait_for(exit_done.wait(), timeout=1)
    exit_error = await handle.wait_for_exit()
    assert exit_error is not None
    assert exit_calls == [exit_error]
    assert isinstance(exit_error, DaytonaError)
    assert str(exit_error) == "filesystem watch stream ended unexpectedly"
    assert handle.exit_error is exit_error


@pytest.mark.anyio
async def test_filesystem_watch_dir_stop_from_callback_returns_without_awaiting_the_reader_task():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    callback_done = asyncio.Event()
    handle: AsyncWatchHandle | None = None

    async def on_event(_event: FilesystemEvent) -> None:
        nonlocal handle
        assert handle is not None
        await handle.stop()
        callback_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_event=on_event))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    raw_handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(raw_handle, AsyncWatchHandle)
    handle = raw_handle

    stream.push("event: fs")
    stream.push('data: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}')
    stream.push("")

    await asyncio.wait_for(callback_done.wait(), timeout=1)
    assert handle.last_callback_error is None
    assert await handle.wait_for_exit() is None


@pytest.mark.anyio
async def test_filesystem_watch_dir_callback_errors_do_not_stop_future_events():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    callback_seen: list[str] = []
    callback_done = asyncio.Event()

    def on_event(event: FilesystemEvent) -> None:
        callback_seen.append(event.name)
        if event.name == "a.txt":
            raise RuntimeError("boom")
        callback_done.set()

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_event=on_event))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    stream.push("event: fs")
    stream.push('data: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}')
    stream.push("")
    stream.push("event: fs")
    stream.push('data: {"type":"write","name":"b.txt","isDir":false,"timestamp":2}')
    stream.push("")

    await asyncio.wait_for(callback_done.wait(), timeout=1)
    assert callback_seen == ["a.txt", "b.txt"]
    assert isinstance(handle.last_callback_error, RuntimeError)
    assert str(handle.last_callback_error) == "boom"
    assert handle.get_new_events() == [
        FilesystemEvent(type=FilesystemEventType.CREATE, name="a.txt", is_dir=False, timestamp=1),
        FilesystemEvent(type=FilesystemEventType.WRITE, name="b.txt", is_dir=False, timestamp=2),
    ]

    await handle.stop()


@pytest.mark.anyio
async def test_filesystem_watch_dir_records_exit_callback_errors_without_crashing_the_reader():
    stream = ControlledSseStream()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> ControlledSseStream:
            return stream

    fs = AsyncFileSystemService(Transport())  # type: ignore[arg-type]
    exit_done = asyncio.Event()

    def on_exit(_error: Exception | None) -> None:
        exit_done.set()
        raise RuntimeError("exit boom")

    watch_task = asyncio.create_task(fs.watch_dir("/workspace", on_exit=on_exit))
    await asyncio.sleep(0)
    stream.push("event: start")
    stream.push("data: {}")
    stream.push("")
    handle = await asyncio.wait_for(watch_task, timeout=1)
    assert isinstance(handle, AsyncWatchHandle)

    await handle.stop()

    await asyncio.wait_for(exit_done.wait(), timeout=1)
    assert await handle.wait_for_exit() is None
    assert isinstance(handle.last_callback_error, RuntimeError)
    assert str(handle.last_callback_error) == "exit boom"


def test_parse_sse_lines_accepts_cr_only_framing_from_httpx_iter_lines():
    fixture = (
        "event: start\r"
        "data: {}\r\r"
        "event: fs\r"
        'data: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}\r\r'
    )

    response = httpx.Response(200, content=fixture.encode("utf-8"))

    assert list(parse_sse_lines(response.iter_lines())) == [
        ("start", {}),
        ("fs", {"type": "create", "name": "a.txt", "isDir": False, "timestamp": 1}),
    ]


@pytest.mark.anyio
async def test_watch_handle_stop_is_reentrant_on_reader_task():
    class Stream:
        def __init__(self) -> None:
            self.closed = False

        async def iter_lines(self):
            if False:
                yield ""

        async def close(self) -> None:
            self.closed = True

    stream = Stream()
    handle = AsyncWatchHandle(stream, asyncio.Queue())
    current = asyncio.current_task()
    assert current is not None
    handle.attach_reader(current)

    await handle.stop()
    await handle.stop()

    assert stream.closed is True


@pytest.mark.anyio
async def test_watch_handle_stop_keeps_shutdown_bounded_when_reader_does_not_exit(monkeypatch):
    import daytona.v2.filesystem as filesystem_module

    class BlockingStream:
        def __init__(self) -> None:
            self.closed = False
            self.release = asyncio.Event()

        async def iter_lines(self):
            await self.release.wait()
            if False:
                yield ""

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(filesystem_module, "WATCH_STOP_JOIN_TIMEOUT_SECONDS", 0.01)

    stream = BlockingStream()
    handle = AsyncWatchHandle(stream, asyncio.Queue())

    async def reader_target() -> None:
        try:
            async for _line in stream.iter_lines():
                pass
        finally:
            await handle.record_exit(None)

    reader = asyncio.create_task(reader_target())
    handle.attach_reader(reader)

    await handle.stop()
    assert stream.closed is True

    stream.release.set()
    assert await handle.wait_for_exit() is None
    await asyncio.wait_for(reader, timeout=1)


@pytest.mark.anyio
async def test_private_preview_link_fetches_preview_token_without_returning_api_credentials():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "ep", "port": 8080, "public": False, "url": "https://p", "created_at": _DEFAULT_TIME},
            {"url": "https://p", "token": "preview-token"},
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx", access_token="sandbox-token")

    link = await sandbox.ports.preview_link(8080)

    assert link.url == "https://p"
    assert link.token == "preview-token"
    assert api.specs[1].path == "/sandboxes/sbx/ports/8080/preview-url"


def test_preview_warning_skip_header_constants_are_exported_from_v2():
    import daytona
    import daytona.v2
    from daytona.v2 import PREVIEW_WARNING_SKIP_HEADER, PREVIEW_WARNING_SKIP_HEADER_VALUE
    from daytona.v2 import ports as ports_module
    from daytona.v2._sync import ports as sync_ports_module

    assert PREVIEW_WARNING_SKIP_HEADER == "X-Daytona-Skip-Preview-Warning"
    assert PREVIEW_WARNING_SKIP_HEADER_VALUE == "true"
    assert ports_module.PREVIEW_WARNING_SKIP_HEADER is PREVIEW_WARNING_SKIP_HEADER
    assert ports_module.PREVIEW_WARNING_SKIP_HEADER_VALUE is PREVIEW_WARNING_SKIP_HEADER_VALUE
    assert sync_ports_module.PREVIEW_WARNING_SKIP_HEADER is PREVIEW_WARNING_SKIP_HEADER
    assert sync_ports_module.PREVIEW_WARNING_SKIP_HEADER_VALUE is PREVIEW_WARNING_SKIP_HEADER_VALUE
    assert {"PREVIEW_WARNING_SKIP_HEADER", "PREVIEW_WARNING_SKIP_HEADER_VALUE"} <= set(daytona.v2.__all__)
    for name in ("PREVIEW_WARNING_SKIP_HEADER", "PREVIEW_WARNING_SKIP_HEADER_VALUE"):
        assert name not in daytona.__all__
        with pytest.raises(ImportError, match=f"from daytona.v2 import {name}`"):
            getattr(daytona, name)


@pytest.mark.anyio
async def test_get_preview_link_creates_private_endpoint_before_fetching_token():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            create_error("missing", 404, "NOT_FOUND", "DAYTONA_API"),
            {"id": "ep", "port": 8080, "public": False, "url": "https://p", "created_at": _DEFAULT_TIME},
            {"url": "https://p", "token": "preview-token"},
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    link = await sandbox.ports.preview_link(8080)

    assert link == PreviewLink(url="https://p", token="preview-token")
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("GET", "/sandboxes/sbx/ports/8080"),
        ("PUT", "/sandboxes/sbx/ports/8080"),
        ("POST", "/sandboxes/sbx/ports/8080/preview-url"),
    ]


@pytest.mark.anyio
async def test_signed_preview_link_sends_ttl_query_and_parses_response():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "url": "https://8080-sdeadbeef.preview",
                "token": "sdeadbeef",
                "expires_at": "2026-08-28T00:05:00Z",
            },
        ]
    )
    client._api = api  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx")

    link = await sandbox.ports.signed_preview_link(8080, expires_in_seconds=300)

    assert link == SignedPreviewLink(
        url="https://8080-sdeadbeef.preview",
        token="sdeadbeef",
        expires_at=parse_timestamp("2026-08-28T00:05:00Z"),
    )
    assert api.specs[0].method == "POST"
    assert api.specs[0].path == "/sandboxes/sbx/ports/8080/signed-preview-urls"
    assert api.specs[0].json_body == {"expires_in_seconds": 300}


@pytest.mark.anyio
async def test_signed_preview_link_omits_ttl_query_for_server_default():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "url": "https://8080-sdeadbeef.preview",
                "token": "sdeadbeef",
                "expires_at": "2026-08-28T00:01:00Z",
            },
        ]
    )
    client._api = api  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx")

    link = await sandbox.ports.signed_preview_link(8080)

    assert link == SignedPreviewLink(
        url="https://8080-sdeadbeef.preview",
        token="sdeadbeef",
        expires_at=parse_timestamp("2026-08-28T00:01:00Z"),
    )
    assert api.specs[0].path == "/sandboxes/sbx/ports/8080/signed-preview-urls"
    assert api.specs[0].json_body == {}


@pytest.mark.anyio
async def test_signed_preview_link_rejects_non_positive_ttl_before_posting():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    client._api = api  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx")

    for ttl in (0, -1):
        with pytest.raises(DaytonaError, match="expires_in_seconds must be a positive integer"):
            await sandbox.ports.signed_preview_link(8080, expires_in_seconds=ttl)

    assert api.specs == []


@pytest.mark.anyio
async def test_expire_signed_preview_link_quotes_token_and_treats_204_as_success():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([None])
    client._api = api  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "sbx")

    assert await sandbox.ports.expire_signed_preview_link(8080, "tok/en") is None

    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes/sbx/ports/8080/signed-preview-urls/revoke"),
    ]
    assert api.specs[0].json_body == {"token": "tok/en"}


@pytest.mark.anyio
async def test_expose_port_upserts_by_port():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "ep", "port": 8080, "public": False, "url": "https://p", "created_at": _DEFAULT_TIME},
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    endpoint = await sandbox.ports.expose(8080)

    assert endpoint.port == 8080
    assert [(spec.method, spec.path) for spec in api.specs] == [("PUT", "/sandboxes/sbx/ports/8080")]


@pytest.mark.anyio
async def test_secret_delete_conflict_preserves_the_server_message() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            create_error(
                "secret is still referenced by another resource",
                409,
                "CONFLICT",
                "DAYTONA_API",
            )
        ]
    )
    bind_api(client, api)

    with pytest.raises(ConflictError) as exc_info:
        await client.secrets.delete("TOKEN")

    err = exc_info.value
    assert err.message == "secret is still referenced by another resource"
    assert (err.status_code, err.code, err.source) == (409, "CONFLICT", "DAYTONA_API")
    assert [(spec.method, spec.path) for spec in api.specs] == [("DELETE", "/secrets/TOKEN")]


def test_daytona_sandboxes_share_one_underlying_toolbox_client() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    first = lazy_sandbox(client, "one", toolbox_url="http://toolbox/one")
    second = lazy_sandbox(client, "two", toolbox_url="http://toolbox/two")

    assert first._toolbox is not None
    assert second._toolbox is not None
    assert first._toolbox is not second._toolbox
    assert first._toolbox._client is second._toolbox._client


@pytest.mark.anyio
async def test_daytona_close_closes_one_shared_toolbox_transport_and_sandboxes_have_no_close() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    first = lazy_sandbox(client, "one", toolbox_url="http://toolbox/one")
    second = lazy_sandbox(client, "two", toolbox_url="http://toolbox/two")

    assert not hasattr(first, "close")
    assert not hasattr(second, "close")

    shared_toolbox = client._toolbox  # type: ignore[attr-defined]
    closed: list[str] = []

    async def close_toolbox() -> None:
        closed.append("toolbox")

    async def close_api() -> None:
        closed.append("api")

    shared_toolbox.close = close_toolbox
    client._api.close = close_api

    await client.close()

    assert closed == ["toolbox", "api"]


@pytest.mark.anyio
async def test_daytona_close_detaches_the_shared_hub_only_once_under_concurrent_calls(monkeypatch) -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    fake_hub = object()
    client._hub = fake_hub  # type: ignore[assignment]
    detached: list[object] = []
    release = asyncio.Event()

    async def fake_detach(hub: object, *args: object, **kwargs: object) -> None:
        detached.append(hub)
        await release.wait()

    async def close_toolbox() -> None:
        return None

    async def close_api() -> None:
        return None

    monkeypatch.setattr("daytona.v2.daytona.detach_hub", fake_detach)
    client._toolbox.close = close_toolbox  # type: ignore[method-assign]
    client._api.close = close_api  # type: ignore[method-assign]

    first = asyncio.create_task(client.close())
    await asyncio.sleep(0)
    second = asyncio.create_task(client.close())
    release.set()
    await asyncio.gather(first, second)

    assert detached == [fake_hub]


@respx.mock
@pytest.mark.anyio
async def test_sandbox_toolbox_transport_shares_one_client_and_sets_identity_per_request() -> None:
    base_url = "http://toolbox.invalid/sandboxes"
    first_route = respx.get(f"{base_url}/one/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=1))
    )
    second_route = respx.get(f"{base_url}/two/system/metrics").mock(
        return_value=httpx.Response(200, json=_metrics_payload(cpu_count=2))
    )
    client = AsyncDaytona(DaytonaConfig(api_key="project-key"))

    try:
        first = lazy_sandbox(client, "one", toolbox_url=f"{base_url}/one", access_token="token-one")
        second = lazy_sandbox(client, "two", toolbox_url=f"{base_url}/two", access_token="token-two")

        assert first._toolbox is not None
        assert second._toolbox is not None
        assert first._toolbox._client is second._toolbox._client
        assert "Authorization" not in first._toolbox._client.headers

        assert (await first.metrics()).cpu_count == 1
        assert (await second.metrics()).cpu_count == 2
    finally:
        await client.close()

    assert first_route.calls[0].request.headers["Authorization"] == "Bearer token-one"
    assert second_route.calls[0].request.headers["Authorization"] == "Bearer token-two"


@pytest.mark.anyio
async def test_client_identity_headers_match_the_v0_sdk_triple_for_sync_and_async():
    assert SDK_VERSION != "unknown"
    assert _request_headers("sdk-python", "k", None) == {
        "Authorization": "Bearer k",
        "X-Daytona-Source": "sdk-python",
        "X-Daytona-SDK-Version": SDK_VERSION,
        "User-Agent": f"sdk-python/{SDK_VERSION}",
    }
    sync_transport = SyncTransport("http://api.invalid")
    async_transport = AsyncTransport("http://api.invalid")
    try:
        assert sync_transport._client.headers["x-daytona-source"] == "sdk-python"
        assert sync_transport._client.headers["user-agent"] == f"sdk-python/{SDK_VERSION}"
        assert async_transport._client.headers["x-daytona-source"] == "sdk-python-async"
        assert async_transport._client.headers["x-daytona-sdk-version"] == SDK_VERSION
        assert async_transport._client.headers["user-agent"] == f"sdk-python-async/{SDK_VERSION}"
    finally:
        sync_transport.close()
        await async_transport.close()

    facade = AsyncDaytona(DaytonaConfig(api_key="k"))
    sync_facade = Daytona(DaytonaConfig(api_key="k"))
    try:
        assert facade._api._source == "sdk-python-async"
        assert facade._toolbox._source == "sdk-python-async"
        assert sync_facade._impl._api._source == "sdk-python"
        assert sync_facade._impl._toolbox._source == "sdk-python"
    finally:
        await facade.close()
        sync_facade.close()


def test_process_create_requires_exactly_one_payload():
    with pytest.raises(DaytonaError):
        process_create_spec(
            None,
            argv=None,
            code=None,
            language=None,
            kind="exec",
            name=None,
            session_id=None,
            cwd=None,
            env=None,
            user=None,
            shell=None,
            login=None,
            stdin="closed",
            execution_timeout=None,
            terminal=None,
            keep_logs=None,
        )
    with pytest.raises(DaytonaError):
        process_create_spec(
            "echo hi",
            argv=["echo", "hi"],
            code=None,
            language=None,
            kind="exec",
            name=None,
            session_id=None,
            cwd=None,
            env=None,
            user=None,
            shell=None,
            login=None,
            stdin="closed",
            execution_timeout=None,
            terminal=None,
            keep_logs=None,
        )


def test_process_create_maps_v2_wire_fields():
    spec = process_create_spec(
        "echo hi",
        argv=None,
        code=None,
        language=None,
        kind="pty",
        name="n",
        session_id="s",
        cwd="/w",
        env={"A": "B"},
        user="u",
        shell="bash",
        login=True,
        stdin="open",
        execution_timeout=0.01,
        terminal=TerminalSize(cols=100, rows=30),
        keep_logs="until_cleanup",
    )
    assert spec.method == "POST"
    assert spec.path == "/processes"
    assert spec.json_body == {
        "kind": "pty",
        "shellCommand": "echo hi",
        "name": "n",
        "sessionId": "s",
        "cwd": "/w",
        "env": {"A": "B"},
        "user": "u",
        "shell": "bash",
        "login": True,
        "stdin": "open",
        "timeoutMs": 10,
        "keepLogs": "until_cleanup",
        "terminal": {"cols": 100, "rows": 30},
    }


def test_pty_create_can_omit_command_source():
    spec = process_create_spec(
        None,
        argv=None,
        code=None,
        language=None,
        kind="pty",
        name=None,
        session_id=None,
        cwd=None,
        env=None,
        user=None,
        shell=None,
        login=None,
        stdin="open",
        execution_timeout=None,
        terminal=TerminalSize(cols=80, rows=24),
        keep_logs=None,
    )
    assert spec.json_body == {"kind": "pty", "stdin": "open", "terminal": {"cols": 80, "rows": 24}}


def test_code_create_omits_timeout():
    spec = process_create_spec(
        None,
        argv=None,
        code="print(1)",
        language="python",
        kind="code",
        name=None,
        session_id=None,
        cwd=None,
        env=None,
        user=None,
        shell=None,
        login=None,
        stdin="closed",
        execution_timeout=0.1,
        terminal=None,
        keep_logs=None,
    )
    assert spec.json_body is not None
    assert "timeoutMs" not in spec.json_body


def test_stdin_bytes_put_encoding_in_json_body():
    spec = stdin_spec("p", b"\x00\xff", eof=True)
    assert spec.params == {}
    assert spec.json_body == {"eof": True, "data": "AP8=", "encoding": "base64"}


def test_parsers_tolerate_unknown_fields():
    process = parse_process(expect_process_dto(_process_payload("p", "exec", "running", future={"x": 1})))
    assert process.id == "p"
    page = parse_log_page(
        {
            "items": [{"cursor": "c1", "channel": "stdout", "data": "hi", "timestamp": 42, "extra": True}],
            "nextCursor": "c2",
            "new": 1,
        }
    )
    assert page.items[0].data == "hi"
    assert page.items[0].timestamp == 42
    assert page.next_cursor == "c2"


def test_errors_are_code_specific_and_carry_code_source():
    err = create_error("missing", 404, code_names.PROCESS_NOT_FOUND, "DAYTONA_DAEMON")
    assert isinstance(err, ProcessNotFoundError)
    assert err.code == code_names.PROCESS_NOT_FOUND
    assert err.source == "DAYTONA_DAEMON"
    assert isinstance(
        create_error("expired", 409, code_names.CURSOR_EXPIRED, "DAYTONA_DAEMON"), CursorExpiredError
    )
    assert isinstance(
        create_error("unsupported", 400, code_names.UNSUPPORTED_OPERATION, "DAYTONA_DAEMON"),
        UnsupportedOperationError,
    )
    assert isinstance(create_error("not supported", 501, None, "DAYTONA_API"), InternalServerError)
    assert error_class_from_status(501).__name__ == "NotSupportedError"
    assert isinstance(create_error("unavailable", 503, None, "DAYTONA_API"), ServiceUnavailableError)
    assert isinstance(
        create_error("addon unavailable", 503, code_names.ADDON_UNAVAILABLE, "DAYTONA_DAEMON"),
        AddonUnavailableError,
    )
    addon_unavailable = create_error("not staged", 503, code_names.ADDON_UNAVAILABLE, "DAYTONA_DAEMON")
    assert isinstance(addon_unavailable, AddonUnavailableError)
    assert isinstance(addon_unavailable, ServiceUnavailableError)
    assert addon_unavailable.code == code_names.ADDON_UNAVAILABLE


@pytest.mark.anyio
async def test_computer_use_namespace_shapes_requests_and_parses_results():
    class Transport:
        def __init__(self):
            self.sent = []
            self.raw_specs = []
            self.responses = {
                ("GET", "/computeruse/status"): {"status": "active", "desktop": "ready"},
                ("GET", "/computeruse/process-status"): {
                    "wm": {
                        "Running": True,
                        "Priority": 5,
                        "AutoRestart": False,
                        "Pid": 99,
                        "Failed": False,
                        "RestartAttempts": 0,
                        "RestartBackoffMs": 0,
                        "Status": "running",
                    }
                },
                ("GET", "/computeruse/process/wm/status"): {"processName": "wm", "running": True},
                ("GET", "/computeruse/screenshot"): {
                    "screenshot": "abc",
                    "cursorPosition": {"x": 1, "y": 2},
                    "sizeBytes": 3,
                },
                ("GET", "/computeruse/display/info"): {
                    "displays": [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "isActive": True}]
                },
                ("GET", "/computeruse/display/windows"): {
                    "windows": [
                        {
                            "id": 14680067,
                            "title": "Main",
                            "x": 0,
                            "y": 0,
                            "width": 10,
                            "height": 20,
                            "isActive": True,
                        }
                    ]
                },
                ("GET", "/computeruse/a11y/tree"): {
                    "root": {
                        "id": "1",
                        "role": "window",
                        "name": "Main",
                        "bounds": {"x": 0, "y": 0, "width": 10, "height": 20},
                        "states": ["focused"],
                    },
                    "truncated": False,
                },
                ("GET", "/computeruse/recordings"): {
                    "recordings": [
                        {
                            "id": "r1",
                            "fileName": "demo.mp4",
                            "filePath": "/tmp/demo.mp4",
                            "startTime": "2026-08-19T00:00:00Z",
                            "status": "running",
                        }
                    ]
                },
            }

        async def send(self, spec):
            self.sent.append(spec)
            return self.responses[(spec.method, spec.path)]

        async def raw(self, spec):
            self.raw_specs.append(spec)
            return b"video"

    namespace = AsyncComputerUseService(Transport())  # type: ignore[arg-type]

    parsed_status = await namespace.status()
    assert parsed_status.status == "active"
    assert parsed_status.desktop == "ready"
    status = await namespace.service.status()
    assert status["wm"].priority == 5
    assert (await namespace.service.status("wm")).running is True
    screenshot = await namespace.screen.screenshot(show_cursor=True)
    assert screenshot.cursor_position == ComputerUsePoint(1, 2)
    assert (await namespace.display.info()).displays[0].id == 0
    assert (await namespace.display.windows()).windows[0].id == 14680067
    assert (await namespace.a11y.tree(scope="pid", pid=77, max_depth=2)).root.role == "window"
    assert (await namespace.recording.list()).recordings[0].file_name == "demo.mp4"
    assert await namespace.recording.download("r1") == b"video"
    sent = namespace._transport.sent  # type: ignore[attr-defined]
    assert sent[3].params == {"show_cursor": "true"}
    assert sent[6].params == {"scope": "pid", "pid": 77, "maxDepth": 2}


@pytest.mark.anyio
async def test_screen_screenshot_routes_by_region_and_compression_options():
    class Transport:
        def __init__(self) -> None:
            self.sent: list[RequestSpec] = []

        async def send(self, spec: RequestSpec) -> dict[str, object]:
            self.sent.append(spec)
            return {"screenshot": "abc", "sizeBytes": 3}

    transport = Transport()
    screen = AsyncComputerUseService(transport).screen  # type: ignore[arg-type]
    region = ComputerUseRegion(x=1, y=2, width=30, height=40)

    await screen.screenshot()
    await screen.screenshot(region=region, show_cursor=False)
    await screen.screenshot(format="jpeg", quality=80, scale=0.5)
    await screen.screenshot(region=region, quality=60)

    assert [(spec.path, spec.params) for spec in transport.sent] == [
        ("/computeruse/screenshot", {}),
        (
            "/computeruse/screenshot/region",
            {"x": 1, "y": 2, "width": 30, "height": 40, "show_cursor": "false"},
        ),
        ("/computeruse/screenshot/compressed", {"format": "jpeg", "quality": 80, "scale": 0.5}),
        (
            "/computeruse/screenshot/region/compressed",
            {"x": 1, "y": 2, "width": 30, "height": 40, "quality": 60},
        ),
    ]


@pytest.mark.anyio
async def test_computer_use_a11y_nodes_carry_value_truncated_bool_children_included():
    bounds = {"x": 0, "y": 0, "width": 10, "height": 10}
    cut = {
        "id": "cut",
        "role": "textField",
        "name": "Body",
        "value": "abcd",
        "valueTruncated": True,
        "bounds": bounds,
        "states": [],
    }
    leaf = {"id": "leaf", "role": "staticText", "name": "Save", "bounds": bounds, "states": []}
    plain = {
        "id": "plain",
        "role": "button",
        "name": "Save",
        "bounds": bounds,
        "states": [],
        "children": [leaf],
    }
    root = {
        "id": "root",
        "role": "window",
        "name": "Main",
        "bounds": bounds,
        "states": [],
        "children": [cut, plain],
    }
    responses = {
        ("GET", "/computeruse/a11y/tree"): {"root": root, "truncated": False},
        ("POST", "/computeruse/a11y/find"): {"matches": [cut, plain], "truncated": True},
    }

    class Transport:
        async def send(self, spec):
            return responses[(spec.method, spec.path)]

    namespace = AsyncComputerUseService(Transport())  # type: ignore[arg-type]

    def flags(node) -> list:
        return [node.id, node.value, node.value_truncated, [flags(child) for child in node.children or []]]

    cut_flags = ["cut", "abcd", True, []]
    plain_flags = ["plain", None, False, [["leaf", None, False, []]]]
    tree = await namespace.a11y.tree(scope="focused")
    assert flags(tree.root) == ["root", None, False, [cut_flags, plain_flags]]
    found = await namespace.a11y.find(scope="all")
    assert [flags(match) for match in found.matches] == [cut_flags, plain_flags]


def test_computer_use_literals_are_publicly_exported():
    assert ComputerUseA11yScope.__args__ == ("focused", "pid", "all")
    assert ComputerUseA11yNameMatch.__args__ == ("exact", "substring", "regex")


@pytest.mark.anyio
async def test_ports_and_lsp_namespaces_shape_requests_and_parse_results():
    class Transport:
        def __init__(self):
            self.sent = []
            self.responses = {
                ("GET", "/port"): {"ports": [3000, 8080]},
                ("GET", "/port/3999/in-use"): {"isInUse": True},
                ("POST", "/lsp/completions"): {"isIncomplete": False, "items": [{"label": "append"}]},
                ("GET", "/lsp/document-symbols"): [
                    {
                        "name": "main",
                        "kind": 12,
                        "location": {
                            "uri": "file:///p/a.py",
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 4},
                            },
                        },
                    }
                ],
                ("GET", "/lsp/workspacesymbols"): [],
            }

        async def send(self, spec):
            self.sent.append(spec)
            return self.responses.get((spec.method, spec.path), {})

    ports_transport = Transport()
    ports_api = Transport()
    ports_api.responses = {
        ("POST", "/sandboxes/sbx/ports/8080/preview-url"): {"url": "https://p", "token": "preview-token"},
        ("PUT", "/sandboxes/sbx/ports/8080"): {
            "id": "ep",
            "port": 8080,
            "public": True,
            "url": "https://p",
            "created_at": _DEFAULT_TIME,
        },
    }
    ports_api.sent = []

    async def ports_api_send(spec):
        ports_api.sent.append(spec)
        endpoint = {
            "id": "ep",
            "port": 8080,
            "public": False,
            "url": "https://p",
            "created_at": _DEFAULT_TIME,
        }
        if spec.method == "GET" and spec.path == "/sandboxes/sbx/ports":
            if (
                len(
                    [
                        sent
                        for sent in ports_api.sent
                        if sent.method == "GET" and sent.path == "/sandboxes/sbx/ports"
                    ]
                )
                == 1
            ):
                return {"ports": [], "next_cursor": None}
            return {"ports": [endpoint], "next_cursor": None}
        if spec.method == "GET" and spec.path == "/sandboxes/sbx/ports/8080":
            return endpoint
        if spec.method == "PUT" and spec.path == "/sandboxes/sbx/ports/8080":
            return {**endpoint, "public": spec.json_body == {"public": True}}
        return ports_api.responses.get((spec.method, spec.path), {})

    ports_api.send = ports_api_send

    ports = AsyncPortsService(ports_transport, ports_api, _sandbox_id="sbx")  # type: ignore[arg-type]
    assert list((await ports.list()).items) == [3000, 8080]
    assert await ports.is_in_use(3999) is True
    assert list((await ports.list_endpoints()).items) == []
    assert await ports.preview_link(8080) == PreviewLink(url="https://p", token="preview-token")
    assert (await ports.expose(8080, public=True)).public is True
    assert not hasattr(ports, "set_public")
    await ports.unexpose(8080)
    assert [(s.method, s.path) for s in ports_transport.sent] == [
        ("GET", "/port"),
        ("GET", "/port/3999/in-use"),
    ]

    lsp_transport = Transport()
    lsp = AsyncLspService(lsp_transport)  # type: ignore[arg-type]
    server = await lsp.start("python", "/p")
    await server.open_document("file:///p/a.py")
    await server.close_document("file:///p/a.py")
    assert lsp_transport.sent[0].json_body == {"languageId": "python", "pathToProject": "/p"}
    assert lsp_transport.sent[1].json_body == {
        "languageId": "python",
        "pathToProject": "/p",
        "uri": "file:///p/a.py",
    }
    assert lsp_transport.sent[2].json_body == {
        "languageId": "python",
        "pathToProject": "/p",
        "uri": "file:///p/a.py",
    }
    completions = await server.completions("file:///p/a.py", {"line": 2, "character": 5})
    assert completions.is_incomplete is False
    assert completions.items[0].label == "append"
    assert "context" not in (lsp_transport.sent[3].json_body or {})
    assert len(await server.document_symbols("file:///p/a.py")) == 1
    assert lsp_transport.sent[4].params == {
        "languageId": "python",
        "pathToProject": "/p",
        "uri": "file:///p/a.py",
    }
    assert await server.workspace_symbols("main") == []
    assert lsp_transport.sent[5].params == {"query": "main", "languageId": "python", "pathToProject": "/p"}
    await server.stop()


@pytest.mark.anyio
async def test_lsp_completion_parse_errors_raise_typed_bad_request():
    class Transport:
        async def send(self, _spec):
            return {"isIncomplete": False, "items": {"label": "append"}}

    server = await AsyncLspService(Transport()).start("python", "/p")  # type: ignore[arg-type]

    with pytest.raises(DaytonaError, match="expected LSP completion list") as exc_info:
        await server.completions("file:///p/a.py", {"line": 2, "character": 5})

    assert exc_info.value.code == code_names.BAD_REQUEST
    assert exc_info.value.source == "SDK"


@pytest.mark.anyio
async def test_lsp_completion_rejects_invalid_mapping_positions_before_request():
    class Transport:
        def __init__(self) -> None:
            self.sent: list[RequestSpec] = []

        async def send(self, spec: RequestSpec):
            self.sent.append(spec)
            return {"isIncomplete": False, "items": []}

    transport = Transport()
    server = await AsyncLspService(transport).start("python", "/p")  # type: ignore[arg-type]

    with pytest.raises(DaytonaError, match="expected LSP position") as exc_info:
        await server.completions("file:///p/a.py", {"line": -1, "character": 5})

    assert exc_info.value.code == code_names.BAD_REQUEST
    assert exc_info.value.source == "SDK"
    assert transport.sent == [
        RequestSpec("POST", "/lsp/start", json_body={"languageId": "python", "pathToProject": "/p"})
    ]


@pytest.mark.anyio
async def test_ports_is_in_use_rejects_non_integer_segments_before_request() -> None:
    transport = RecordingTransport([])
    ports = AsyncPortsService(transport)  # type: ignore[arg-type]

    with pytest.raises(DaytonaError, match="port must be an integer") as exc_info:
        await object.__getattribute__(ports, "is_in_use")("39/99")

    assert exc_info.value.code == code_names.BAD_REQUEST
    assert exc_info.value.source == "SDK"
    assert transport.specs == []


def test_process_handle_is_json_rehydratable():
    handle = AsyncProcessHandle("s", "p")
    assert handle.to_json() == {"sandboxId": "s", "processId": "p"}
    assert AsyncProcessHandle.from_json(handle.to_json()).ref().process_id == "p"


def test_process_handle_from_json_drops_unlisted_kind_strings() -> None:
    handle = AsyncProcessHandle.from_json({"sandboxId": "s", "processId": "p", "kind": "future-kind"})

    assert handle.kind is None
    assert handle.to_json() == {"sandboxId": "s", "processId": "p"}


@pytest.mark.anyio
async def test_wait_or_interrupt_returns_final_status_when_interrupt_races_terminal_process(monkeypatch):
    from daytona.v2.process import wait_or_interrupt

    ticks = iter([0.0, 0.0, 0.0, 0.001, 0.001, 0.001])
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(ticks))

    class Handle:
        def __init__(self):
            self.waits: list[float | None] = []

        async def wait(self, *, timeout=None):
            self.waits.append(timeout)
            if len(self.waits) == 1:
                return WaitResult(reason="timed_out")
            return WaitResult(reason="exited", exit_code=0)

        async def kill(self, *, signal="TERM", escalate_after_ms=None, escalate_to="KILL"):
            raise create_error("already terminal", 409, code_names.PROCESS_TERMINAL, "DAYTONA_DAEMON")

    handle = Handle()

    result = await wait_or_interrupt(handle, timeout=0.001)  # type: ignore[arg-type]

    assert result == WaitResult(reason="exited", exit_code=0)
    assert handle.waits[-1] == 10.0


@pytest.mark.anyio
async def test_process_namespace_get_fetches_metadata():
    class Transport:
        def __init__(self):
            self.spec = None

        async def send(self, spec):
            self.spec = spec
            return _process_payload("p", "exec", "running")

    transport = Transport()
    process = AsyncProcessService("s", transport)  # type: ignore[arg-type]

    info = await process.get("p")

    assert info.id == "p"
    assert transport.spec.method == "GET"
    assert transport.spec.path == "/processes/p"


def test_logs_spec_supports_cursor_replay_and_sse_follow():
    spec = logs_spec("p", cursor="c", limit=10, encoding="text", follow=True)
    assert spec.path == "/processes/p/logs"
    assert spec.params == {"encoding": "text", "cursor": "c", "limit": 10, "follow": "true"}


@pytest.mark.anyio
async def test_all_logs_follows_next_cursor_until_exhausted():
    class Handle:
        def __init__(self):
            self.cursors = []

        async def logs(self, *, cursor=None, limit=None, encoding="text"):
            self.cursors.append(cursor)
            if cursor is None:
                return parse_log_page(_log_page_payload([_log_frame_payload("c1", "a")], next_cursor="c1"))
            if cursor == "c1":
                return parse_log_page(_log_page_payload([_log_frame_payload("c2", "b")], next_cursor="c2"))
            assert cursor is not None
            return parse_log_page(_log_page_payload([], next_cursor=cursor, eof=True))

    handle = Handle()
    assert "".join(frame.data for frame in await _all_logs(handle)) == "ab"  # type: ignore[arg-type]
    assert handle.cursors == [None, "c1", "c2"]


def test_create_body_matches_image_and_snapshot_sources():
    assert _create_body({"route": "linux", "source_kind": "image", "source": "ubuntu:24.04"}) == {
        "image": "ubuntu:24.04"
    }
    assert _create_body({"route": "linux", "source_kind": "snapshot", "source": "snap"}) == {
        "snapshot": "snap"
    }
    assert (
        _create_path({"route": "linux", "source_kind": "snapshot", "source": "snap"})
        == "/sandboxes/from-snapshot"
    )
    with pytest.raises(Exception, match="requires a non-empty image"):
        _create_body({"route": "linux", "source_kind": "image", "source": "   "})


@pytest.mark.anyio
async def test_sandbox_build_logs_discovers_url_before_reading_text():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    body = "state: running\nLoading build definition\nBuild step completed\n"
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("build-logs-url"):
            return httpx.Response(200, json={"url": "https://logs.example/build-logs/sandboxes/sbx"})
        return httpx.Response(200, text=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = AsyncTransport("http://api.invalid", "key", client=http)
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    try:
        assert await sandbox.build_logs() == body
    finally:
        await http.aclose()
    assert [str(request.url) for request in requests] == [
        "http://api.invalid/sandboxes/sbx/build-logs-url",
        "https://logs.example/build-logs/sandboxes/sbx?follow=false&format=text",
    ]


def test_create_body_forwards_labels_on_all_create_routes():
    assert _create_body(
        {"route": "linux", "source_kind": "image", "source": "python:3.12", "labels": {"team": "sdk"}}
    )["labels"] == {"team": "sdk"}


def test_create_body_forwards_lifecycle_on_all_create_routes():
    linux = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "python:3.12",
            "lifecycle_policy": {
                "timeout_seconds": 3600,
                "on_timeout": "delete",
                "idle_expiry_seconds": 900,
                "on_idle_timeout": "delete",
            },
        }
    )
    assert linux["lifecycle_policy"] == {
        "timeout_seconds": 3600,
        "on_timeout": "delete",
        "idle_expiry_seconds": 900,
        "on_idle_timeout": "delete",
    }


def test_create_body_omits_lifecycle_when_nothing_is_set():
    assert "lifecycle_policy" not in _create_body(
        {"route": "linux", "source_kind": "image", "source": "python:3.12"}
    )
    assert "lifecycle_policy" not in _create_body(
        {"route": "linux", "source_kind": "image", "source": "python:3.12", "lifecycle_policy": {}}
    )


def test_create_body_leaves_on_timeout_to_the_server_default():
    """`on_timeout` is optional: the server fills the class default, so the SDK
    never invents one — and `on_timeout` alone may ride on the default timeout."""
    body = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "py",
            "lifecycle_policy": {"timeout_seconds": 300},
        }
    )
    assert body["lifecycle_policy"] == {"timeout_seconds": 300}


@pytest.mark.parametrize(
    ("message", "config"),
    [
        (
            "linux create does not accept: lifecycle_policy.on_timeout=stop",
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"timeout_seconds": 60, "on_timeout": "stop"},
            },
        ),
        (
            "linux create does not accept: lifecycle_policy.on_timeout=pause",
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"timeout_seconds": 60, "on_timeout": "pause"},
            },
        ),
        (
            "linux create does not accept: lifecycle_policy.on_idle_timeout=stop",
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": 900, "on_idle_timeout": "stop"},
            },
        ),
        (
            "lifecycle_policy contains unknown fields: future_option",
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"future_option": True},
            },
        ),
        (
            "lifecycle_policy contains unknown fields: future_interval_seconds",
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"future_interval_seconds": 3600},
            },
        ),
    ],
)
def test_create_body_rejects_lifecycle_actions_the_class_forbids(message, config):
    with pytest.raises(Exception, match=re.escape(message)):
        _create_body(config)


@pytest.mark.parametrize("action", ["stop", "pause"])
def test_snapshot_create_rejects_unsupported_lifecycle_actions(action: str) -> None:
    # Given a Linux snapshot source.
    config = {
        "route": "linux",
        "source_kind": "snapshot",
        "source": "snapshot-name",
        "lifecycle_policy": {"timeout_seconds": 60, "on_timeout": action},
    }
    # When rendering its create body, then unsupported actions are rejected.
    with pytest.raises(BadRequestError):
        _create_body(config)


def test_create_body_forwards_snapshot_lifecycle_settings():
    """Snapshot creation forwards the requested lifecycle policy unchanged."""
    lifecycle = {"idle_expiry_seconds": 60, "on_idle_timeout": "delete"}
    body = _create_body(
        {"route": "linux", "source_kind": "snapshot", "source": "snap", "lifecycle_policy": dict(lifecycle)}
    )
    assert body["lifecycle_policy"] == lifecycle


@pytest.mark.anyio
async def test_create_from_snapshot_forwards_lifecycle_settings():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "from-snap", "status": "running", "wait_cap_seconds": 3}])
    client._api = api  # type: ignore[attr-defined]
    await client.create_from_snapshot(
        "snap",
        lifecycle_policy={"idle_expiry_seconds": 60, "on_idle_timeout": "delete"},
    )
    assert api.specs[0].path == "/sandboxes/from-snapshot"
    assert api.specs[0].json_body["lifecycle_policy"] == {
        "idle_expiry_seconds": 60,
        "on_idle_timeout": "delete",
    }


def test_create_body_accepts_the_delete_action():
    lifecycle = {
        "timeout_seconds": 120,
        "on_timeout": "delete",
        "idle_expiry_seconds": 60,
        "on_idle_timeout": "delete",
    }
    body = _create_body(
        {"route": "linux", "source_kind": "image", "source": "py", "lifecycle_policy": dict(lifecycle)}
    )
    assert body["lifecycle_policy"] == lifecycle


def test_timeout_seconds_cannot_be_disabled_or_zero():
    for value in (DISABLED, 0, -2):
        with pytest.raises(
            DaytonaError,
            match=re.escape(
                "lifecycle_policy.timeout_seconds must be at least 1; the timeout cannot be disabled"
            ),
        ):
            _create_body(
                {
                    "route": "linux",
                    "source_kind": "image",
                    "source": "py",
                    "lifecycle_policy": {"timeout_seconds": value},
                }
            )
    assert _create_body(
        {"route": "linux", "source_kind": "image", "source": "py", "lifecycle_policy": {"timeout_seconds": 1}}
    )["lifecycle_policy"] == {"timeout_seconds": 1}


def test_disabled_clears_only_idle_expiry_seconds():
    assert DISABLED == -1
    body = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "py",
            "lifecycle_policy": {"idle_expiry_seconds": DISABLED},
        }
    )
    assert body["lifecycle_policy"] == {"idle_expiry_seconds": -1}
    with pytest.raises(
        DaytonaError, match=re.escape("lifecycle_policy.idle_expiry_seconds: 0 is not a valid budget")
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": 0, "on_idle_timeout": "delete"},
            }
        )
    with pytest.raises(
        DaytonaError, match=re.escape("lifecycle_policy.idle_expiry_seconds must be >= -1: use -1 to disable")
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": -2},
            }
        )


def test_arming_the_idle_timeout_at_create_requires_its_action():
    with pytest.raises(
        DaytonaError,
        match=re.escape("lifecycle_policy.idle_expiry_seconds requires lifecycle_policy.on_idle_timeout"),
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": 900},
            }
        )
    with pytest.raises(
        DaytonaError, match=re.escape("lifecycle_policy.on_idle_timeout requires an idle timeout")
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"on_idle_timeout": "delete"},
            }
        )
    with pytest.raises(
        DaytonaError, match=re.escape("lifecycle_policy.on_idle_timeout requires an idle timeout")
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": DISABLED, "on_idle_timeout": "delete"},
            }
        )


def test_lifecycle_rejects_non_integer_durations_bad_actions_and_unsupported_fields():
    with pytest.raises(
        Exception, match=re.escape("lifecycle_policy.timeout_seconds must be an integer number of seconds")
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"timeout_seconds": 1.5},
            }
        )
    with pytest.raises(
        Exception,
        match=re.escape("lifecycle_policy.idle_expiry_seconds must be an integer number of seconds"),
    ):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"idle_expiry_seconds": True},
            }
        )
    for action in ("future-action", "STOP", 1):
        with pytest.raises(
            Exception, match=re.escape("lifecycle_policy.on_timeout must be one of: stop, pause, delete")
        ):
            _create_body(
                {
                    "route": "linux",
                    "source_kind": "image",
                    "source": "py",
                    "lifecycle_policy": {"timeout_seconds": 60, "on_timeout": action},
                }
            )
    with pytest.raises(Exception, match=re.escape("lifecycle_policy contains unknown fields: future_option")):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {"future_option": 1},
            }
        )
    with pytest.raises(Exception, match="lifecycle_policy must be an object"):
        _create_body({"route": "linux", "source_kind": "image", "source": "py", "lifecycle_policy": 900})


def test_lifecycle_rejections_are_typed_bad_request_with_source_sdk():
    """TS's twin throws BadRequestError via createDaytonaError(…, 'SDK') at every
    site; a caller's ``isinstance(e, BadRequestError)`` must catch the same
    inputs there. One probe per rejection site in lifecycle_body +
    assert_lifecycle_class_support (parity: unit.test.ts)."""
    rejections = [
        lambda: lifecycle_body(900),
        lambda: lifecycle_body({"nope": 1}),
        lambda: lifecycle_body({"timeout_seconds": 1.5}),
        lambda: lifecycle_body({"timeout_seconds": 0}),
        lambda: lifecycle_body({"idle_expiry_seconds": 0, "on_idle_timeout": "delete"}),
        lambda: lifecycle_body({"idle_expiry_seconds": -2}),
        lambda: lifecycle_body({"timeout_seconds": 2147483648}),
        lambda: lifecycle_body({"timeout_seconds": 60, "on_timeout": "future-action"}),
        lambda: lifecycle_body({"future_option": 1}),
        lambda: lifecycle_body({"idle_expiry_seconds": DISABLED, "on_idle_timeout": "stop"}),
        lambda: lifecycle_body({"idle_expiry_seconds": 900}, creating=True),
        lambda: lifecycle_body({"on_idle_timeout": "stop"}, creating=True),
        lambda: assert_lifecycle_class_support(
            {"on_timeout": "pause"}, sandbox_class="linux", persistent=False
        ),
    ]
    for reject in rejections:
        with pytest.raises(BadRequestError) as excinfo:
            reject()
        assert excinfo.value.source == "SDK"


def test_lifecycle_accepts_the_generated_action_enum_as_its_wire_string():
    from daytona.v2._gen.api.models.lifecycle_action import LifecycleAction as WireLifecycleAction

    body = lifecycle_body({"timeout_seconds": 60, "on_timeout": WireLifecycleAction.DELETE})
    assert body == {"timeout_seconds": 60, "on_timeout": "delete"}
    assert type(body["on_timeout"]) is str


@pytest.mark.parametrize("unknown", [{"unknown_field": 900}, {"unknown_field": 60, "unknown_field_2": 1}])
def test_lifecycle_rejects_unknown_fields_instead_of_dropping_them(unknown):
    """Reject unknown lifecycle fields rather than silently dropping them."""
    with pytest.raises(DaytonaError, match="lifecycle_policy contains unknown fields"):
        _create_body({"route": "linux", "source_kind": "image", "source": "py", "lifecycle_policy": unknown})


def test_a_misspelled_only_key_never_creates_a_policy_free_sandbox():
    """The single-key typo is the dangerous one: projecting it away omits
    `lifecycle_policy` entirely and creates the sandbox with NO policy at all."""
    for lifecycle in ({"unknown_field": 900}, {"unknown_field": "stop"}, {"unknown_field": True}):
        with pytest.raises(DaytonaError, match="lifecycle_policy contains unknown fields"):
            _create_body(
                {
                    "route": "linux",
                    "source_kind": "image",
                    "source": "py",
                    "lifecycle_policy": lifecycle,
                }
            )
    assert "lifecycle_policy" not in _create_body({"route": "linux", "source_kind": "image", "source": "py"})


def test_every_known_lifecycle_field_survives_the_unknown_field_guard():
    policy = {
        "timeout_seconds": 3600,
        "on_timeout": "pause",
        "idle_expiry_seconds": 900,
        "on_idle_timeout": "stop",
    }
    body = lifecycle_body(policy)
    assert body is not None
    assert set(body) == set(LIFECYCLE_FIELDS)
    # A known field carrying None is the documented "omitted" case (unchanged on
    # PATCH, disabled at create) — it is not an unknown field.
    assert lifecycle_body({name: None for name in LIFECYCLE_FIELDS}) is None
    assert lifecycle_body({"timeout_seconds": 60, "on_timeout": None}) == {"timeout_seconds": 60}


def test_client_mirrors_the_servers_ceiling_rule():
    """Reject lifecycle durations above the API limit before posting."""
    for field in ("timeout_seconds", "idle_expiry_seconds"):
        with pytest.raises(
            DaytonaError, match=re.escape(f"lifecycle_policy.{field} must not exceed 2147483647")
        ):
            _create_body(
                {
                    "route": "linux",
                    "source_kind": "image",
                    "source": "py",
                    "lifecycle_policy": {field: 2147483648, "on_idle_timeout": "delete"},
                }
            )
    body = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "py",
            "lifecycle_policy": {"timeout_seconds": 2147483647},
        }
    )
    assert body["lifecycle_policy"] == {"timeout_seconds": 2147483647}


def test_linux_create_can_disable_the_idle_timeout():
    body = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "py",
            "lifecycle_policy": {"idle_expiry_seconds": DISABLED},
        }
    )
    assert body["lifecycle_policy"] == {"idle_expiry_seconds": -1}


@pytest.mark.anyio
async def test_update_lifecycle_policy_patches_only_the_passed_keys():
    mac_api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running", sandbox_class="macos", persistent=True),
            {
                "id": "sbx",
                "lifecycle_policy": {
                    "timeout_seconds": 300,
                    "on_timeout": "stop",
                    "timeout_at": "2026-08-28T10:00:00Z",
                    "idle_expiry_seconds": 900,
                    "on_idle_timeout": "stop",
                    "last_activity_at": "2026-08-27T09:00:00Z",
                    "last_activity_source": "traffic",
                },
            },
        ]
    )
    mac = AsyncSandbox(sandbox_info_from_id("sbx"), mac_api, None)
    lifecycle = await mac.update_lifecycle_policy(idle_expiry_seconds=900, on_idle_timeout="stop")

    assert [(spec.method, spec.path) for spec in mac_api.specs] == [
        ("GET", "/sandboxes/sbx"),
        ("PATCH", "/sandboxes/sbx/lifecycle-policy"),
    ]
    assert mac_api.specs[1].json_body == {
        "idle_expiry_seconds": 900,
        "on_idle_timeout": "stop",
    }
    assert lifecycle == SandboxLifecycle(
        timeout_seconds=300,
        on_timeout="stop",
        timeout_at=parse_timestamp("2026-08-28T10:00:00Z"),
        idle_expiry_seconds=900,
        on_idle_timeout="stop",
        last_activity_at=parse_timestamp("2026-08-27T09:00:00Z"),
        last_activity_source="traffic",
    )
    assert mac.lifecycle_policy == lifecycle


@pytest.mark.anyio
async def test_reset_idle_timer_posts_and_caches_the_echo():
    """Resetting the idle timer updates the cached lifecycle policy."""
    linux_api = RecordingTransport(
        [
            {
                "lifecycle_policy": {
                    "timeout_seconds": 300,
                    "on_timeout": "delete",
                    "timeout_at": "2026-08-28T10:00:00Z",
                    "idle_expiry_seconds": 900,
                    "on_idle_timeout": "delete",
                    "idle_timeout_at": "2026-08-27T09:15:00Z",
                    "last_activity_at": "2026-08-27T09:00:00Z",
                    "last_activity_source": "api",
                },
                "id": "sbx",
            },
        ]
    )
    linux = AsyncSandbox(sandbox_info_from_id("sbx"), linux_api, None)
    lifecycle = await linux.reset_idle_timer()
    assert [(spec.method, spec.path) for spec in linux_api.specs] == [
        ("POST", "/sandboxes/sbx/reset-idle-timer"),
    ]
    assert linux_api.specs[0].json_body is None
    assert lifecycle == SandboxLifecycle(
        timeout_seconds=300,
        on_timeout="delete",
        timeout_at=parse_timestamp("2026-08-28T10:00:00Z"),
        idle_expiry_seconds=900,
        on_idle_timeout="delete",
        idle_timeout_at=parse_timestamp("2026-08-27T09:15:00Z"),
        last_activity_at=parse_timestamp("2026-08-27T09:00:00Z"),
        last_activity_source="api",
    )
    # The echo IS the full post-reset lifecycle — the cache takes it whole,
    # exactly like update_lifecycle_policy (a bare handle included).
    assert linux.lifecycle_policy == lifecycle

    # Preserve the response's activity source and timestamp without inferring
    # an idle deadline when none is supplied.
    concurrent_api = RecordingTransport(
        [
            {
                "lifecycle_policy": {
                    "last_activity_at": "2026-08-27T09:00:02Z",
                    "last_activity_source": "traffic",
                },
                "id": "concurrent",
            },
        ]
    )
    concurrent = AsyncSandbox(sandbox_info_from_id("concurrent"), concurrent_api, None)
    echoed = await concurrent.reset_idle_timer()
    assert concurrent_api.specs[0].path == "/sandboxes/concurrent/reset-idle-timer"
    assert echoed.last_activity_source == "traffic"
    assert echoed.idle_timeout_at is None


@pytest.mark.anyio
async def test_reset_idle_timer_does_not_roll_the_cached_clock_backwards():
    """A late echo from an earlier concurrent reset must not regress the cache."""
    api = RecordingTransport(
        [
            {
                "lifecycle_policy": {
                    "idle_expiry_seconds": 900,
                    "on_idle_timeout": "stop",
                    "last_activity_at": "2026-08-27T09:00:00Z",
                    "last_activity_source": "traffic",
                },
                "id": "sbx",
            },
            {
                "lifecycle_policy": {
                    "last_activity_at": "2026-08-27T08:00:00Z",
                    "last_activity_source": "ssh",
                },
                "id": "sbx",
            },
        ]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    newer = await sandbox.reset_idle_timer()
    assert sandbox.lifecycle_policy == newer

    # The caller still gets the stale echo verbatim…
    stale = await sandbox.reset_idle_timer()
    assert stale.last_activity_at == parse_timestamp("2026-08-27T08:00:00Z")
    assert stale.last_activity_source == "ssh"
    # …but the cached policy keeps the newer clock.
    assert sandbox.lifecycle_policy == newer


@pytest.mark.anyio
async def test_reset_idle_timer_keeps_a_valid_cached_clock_over_a_clockless_echo():
    """A response without a parseable clock must not replace a valid cached clock."""
    api = RecordingTransport(
        [
            {
                "lifecycle_policy": {
                    "idle_expiry_seconds": 900,
                    "on_idle_timeout": "stop",
                    "last_activity_at": "2026-08-27T09:00:00Z",
                    "last_activity_source": "traffic",
                },
                "id": "sbx",
            },
            {"lifecycle_policy": {"last_activity_at": None, "last_activity_source": "ssh"}, "id": "sbx"},
            {"lifecycle_policy": {"last_activity_source": "ssh"}, "id": "sbx"},
            {
                "lifecycle_policy": {
                    "last_activity_at": "not-a-timestamp",
                    "last_activity_source": "ssh",
                },
                "id": "sbx",
            },
        ]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    cached = await sandbox.reset_idle_timer()
    assert sandbox.lifecycle_policy == cached

    # An explicit null, an absent field and an unparseable value are all the
    # same degenerate echo: returned verbatim, never cached over a valid clock.
    for _ in range(3):
        degenerate = await sandbox.reset_idle_timer()
        assert degenerate.last_activity_source == "ssh"
        assert sandbox.lifecycle_policy == cached


@pytest.mark.anyio
async def test_reset_idle_timer_accepts_a_clockless_echo_into_an_empty_cache():
    """With nothing valid to protect, the fresh echo still wins."""
    api = RecordingTransport(
        [{"lifecycle_policy": {"idle_expiry_seconds": 900, "on_idle_timeout": "stop"}, "id": "sbx"}]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    fresh = await sandbox.reset_idle_timer()
    assert fresh.last_activity_at is None
    assert sandbox.lifecycle_policy == fresh


@pytest.mark.anyio
async def test_reset_idle_timer_survives_a_naive_cached_clock():
    """A cached offset-naive timestamp must not TypeError the guard comparison
    after the reset already landed (naive parses are normalized to UTC)."""
    api = RecordingTransport(
        [
            {
                # Compare an offset-naive cached timestamp against an aware response.
                "lifecycle_policy": {
                    "last_activity_at": "2026-08-27T09:00:00.000000",
                    "last_activity_source": "ssh",
                },
                "id": "sbx",
            },
            {
                "lifecycle_policy": {
                    "last_activity_at": "2026-08-27T10:00:00.000000Z",
                    "last_activity_source": "api",
                },
                "id": "sbx",
            },
        ]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    naive = await sandbox.reset_idle_timer()
    assert sandbox.lifecycle_policy == naive
    aware = await sandbox.reset_idle_timer()
    assert aware.last_activity_source == "api"
    assert sandbox.lifecycle_policy == aware


def test_parse_lifecycle_reads_idle_timeout_at():
    armed = parse_lifecycle(
        {
            "idle_expiry_seconds": 900,
            "on_idle_timeout": "stop",
            "idle_timeout_at": "2026-08-27T09:15:00Z",
        }
    )
    assert armed.idle_timeout_at == parse_timestamp("2026-08-27T09:15:00Z")
    # Explicit null and an absent field both read back as None.
    assert parse_lifecycle({"idle_timeout_at": None}).idle_timeout_at is None
    assert parse_lifecycle({}).idle_timeout_at is None


@pytest.mark.anyio
async def test_update_lifecycle_policy_validates_before_patching():
    empty_api = RecordingTransport([])
    empty = AsyncSandbox(sandbox_info_from_id("sbx"), empty_api, None)
    with pytest.raises(Exception, match="update_lifecycle_policy requires at least one lifecycle field"):
        await empty.update_lifecycle_policy()
    assert empty_api.specs == []

    bad_api = RecordingTransport([])
    bad = AsyncSandbox(sandbox_info_from_id("sbx"), bad_api, None)
    with pytest.raises(Exception, match=re.escape("the timeout cannot be disabled")):
        await bad.update_lifecycle_policy(timeout_seconds=DISABLED)
    with pytest.raises(
        Exception, match=re.escape("lifecycle_policy.idle_expiry_seconds: 0 is not a valid budget")
    ):
        await bad.update_lifecycle_policy(idle_expiry_seconds=0)
    with pytest.raises(
        Exception, match=re.escape("lifecycle_policy.on_idle_timeout requires an idle timeout")
    ):
        await bad.update_lifecycle_policy(idle_expiry_seconds=DISABLED, on_idle_timeout="stop")
    assert bad_api.specs == []


@pytest.mark.anyio
async def test_update_lifecycle_policy_leaves_pair_coherence_to_the_merged_state():
    """A partial update may change either idle setting independently."""
    api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running", sandbox_class="macos", persistent=True),
            {"id": "sbx", "lifecycle_policy": {"idle_expiry_seconds": 1800, "on_idle_timeout": "stop"}},
            {"id": "sbx", "lifecycle_policy": {"idle_expiry_seconds": 1800, "on_idle_timeout": "delete"}},
        ]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    await sandbox.update_lifecycle_policy(idle_expiry_seconds=1800)
    await sandbox.update_lifecycle_policy(on_idle_timeout="delete")
    assert [spec.json_body for spec in api.specs[1:]] == [
        {"idle_expiry_seconds": 1800},
        {"on_idle_timeout": "delete"},
    ]


@pytest.mark.anyio
async def test_update_lifecycle_policy_cannot_trip_the_unknown_field_guard():
    """The method builds its own dict from keyword-only knobs, so the PATCH
    path can only ever hand `lifecycle_body` KNOWN fields — a signature that
    drifted from the wire field set would start 400ing every PATCH."""
    params = [
        name for name in inspect.signature(AsyncSandbox.update_lifecycle_policy).parameters if name != "self"
    ]
    assert set(params) == set(LIFECYCLE_FIELDS)

    api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running"),
            {
                "id": "sbx",
                "lifecycle_policy": {"timeout_seconds": 3600, "timeout_at": "2026-09-01T00:00:00Z"},
            },
        ]
    )
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), api, None)
    await sandbox.update_lifecycle_policy(timeout_seconds=3600)
    # Omitted stays omitted: the guard inspects key NAMES, so it cannot smuggle
    # unset knobs onto a PATCH body and overwrite unrelated policy settings.
    assert api.specs[1].json_body == {"timeout_seconds": 3600}


@pytest.mark.anyio
async def test_update_lifecycle_policy_distinguishes_omitted_disabled_and_value() -> None:
    omitted_api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running"),
            {"id": "sbx", "lifecycle_policy": {"timeout_seconds": 60}},
        ]
    )
    omitted = AsyncSandbox(sandbox_info_from_id("sbx"), omitted_api, None)
    await omitted.update_lifecycle_policy(timeout_seconds=60)
    assert omitted_api.specs[1].json_body == {"timeout_seconds": 60}

    disabled_api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running"),
            {"id": "sbx", "lifecycle_policy": {"idle_expiry_seconds": None, "on_idle_timeout": None}},
        ]
    )
    disabled = AsyncSandbox(sandbox_info_from_id("sbx"), disabled_api, None)
    await disabled.update_lifecycle_policy(idle_expiry_seconds=DISABLED)
    assert disabled_api.specs[1].json_body == {"idle_expiry_seconds": DISABLED}

    value_api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "running"),
            {"id": "sbx", "lifecycle_policy": {"idle_expiry_seconds": 3600, "on_idle_timeout": "delete"}},
        ]
    )
    value = AsyncSandbox(sandbox_info_from_id("sbx"), value_api, None)
    await value.update_lifecycle_policy(idle_expiry_seconds=3600, on_idle_timeout="delete")
    assert value_api.specs[1].json_body == {"idle_expiry_seconds": 3600, "on_idle_timeout": "delete"}


@pytest.mark.anyio
async def test_sandbox_from_api_fallback_toolbox_inherits_the_api_source_label():
    api = AsyncTransport("http://api.invalid", "k", source="sdk-python")
    try:
        sandbox = AsyncSandbox._from_api(
            _sandbox_detail_payload("sbx", "running"),
            api,
            None,
            toolbox_base_url="http://toolbox.invalid/sandboxes",
            access_token="t",
        )
        assert sandbox._toolbox is not None
        assert sandbox._toolbox._source == "sdk-python"
        await sandbox._toolbox.close()
    finally:
        await api.close()


def test_sandbox_from_api_exposes_the_read_side_lifecycle_policy():
    sandbox = AsyncSandbox._from_api(
        _sandbox_detail_payload(
            "sbx",
            "running",
            lifecycle_policy={
                "timeout_seconds": 300,
                "on_timeout": "pause",
                "timeout_at": "2026-08-28T10:00:00Z",
                "idle_expiry_seconds": None,
                "on_idle_timeout": None,
                "last_activity_at": "2026-08-27T09:00:00Z",
                "last_activity_source": "traffic",
            },
        ),
        None,
        None,
    )
    assert sandbox.lifecycle_policy == SandboxLifecycle(
        timeout_seconds=300,
        on_timeout="pause",
        timeout_at=parse_timestamp("2026-08-28T10:00:00Z"),
        last_activity_at=parse_timestamp("2026-08-27T09:00:00Z"),
        last_activity_source="traffic",
    )
    assert (
        AsyncSandbox._from_api(
            expect_sandbox_detail(_sandbox_detail_payload("sbx", "running")), None, None
        ).lifecycle_policy
        is None
    )


def test_parse_lifecycle_passes_an_unknown_action_through_as_its_wire_string():
    """A newer server may add an action; the read side must not fail or rename it."""
    assert (
        parse_lifecycle({"timeout_seconds": 60, "on_timeout": "future-action"}).on_timeout == "future-action"
    )
    assert (
        parse_lifecycle(
            LifecyclePolicy.model_validate(
                {
                    "timeout_seconds": 60,
                    "on_timeout": "future-action",
                }
            )
        ).on_timeout
        == "future-action"
    )


@pytest.mark.anyio
async def test_create_uses_split_routes():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))

    linux_api = RecordingTransport(
        [
            {"id": "linux", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    client._api = linux_api  # type: ignore[attr-defined]
    await client.create(Image.base("python:3.12"))
    assert linux_api.specs[0].path == "/sandboxes"
    assert linux_api.specs[0].json_body == {"image": "python:3.12", "wait_seconds": 60}

    snapshot_api = RecordingTransport(
        [
            {"id": "from-snap", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    client._api = snapshot_api  # type: ignore[attr-defined]
    await client.create_from_snapshot("snap")
    assert snapshot_api.specs[0].path == "/sandboxes/from-snapshot"
    assert snapshot_api.specs[0].json_body == {"snapshot": "snap", "wait_seconds": 60}


def test_create_body_maps_burst_caps():
    body = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "python:3.12",
            "resources": Resources(cpu=0.5, memory=1, cpu_max=4, memory_max=0.25, disk_max=20),
        }
    )
    assert body["cores_max"] == 4
    assert body["memory_max_mib"] == 256
    assert body["disk_max_mib"] == 20 * 1024
    # Fractional GiB disk limits are converted to MiB.
    fractional = _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "python:3.12",
            "resources": Resources(disk_max=0.5),
        }
    )
    assert fractional["disk_max_mib"] == 512


def test_create_body_sends_every_limit():
    # A fixed shape sets each limit equal to its requested allocation.
    config = {
        "route": "linux",
        "source_kind": "image",
        "source": "img",
        "resources": Resources(cpu=2, cpu_max=2, memory=4, memory_max=4, disk=10, disk_max=10),
    }
    body = _create_body(config)
    assert (body["cores_max"], body["memory_max_mib"], body["disk_max_mib"]) == (2, 4096, 10240)
    assert (body["cpu"], body["memory_mib"], body["disk_mib"]) == (2, 4096, 10240)


def test_public_optional_params_are_keyword_only():
    import importlib.util

    from daytona.v2.daytona import AsyncDaytona

    sig = inspect.signature(AsyncDaytona.create)
    assert sig.parameters["image"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert sig.parameters["wait_seconds"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "create_timeout_seconds" in sig.parameters
    assert (
        inspect.signature(AsyncDaytona.create_from_snapshot).parameters["snapshot"].kind
        is inspect.Parameter.POSITIONAL_ONLY
    )
    assert (
        inspect.signature(AsyncDaytona.create_from_build).parameters["builder"].kind
        is inspect.Parameter.POSITIONAL_ONLY
    )
    # The former helper module `daytona.v2.http` is gone; its helpers are internal.
    assert importlib.util.find_spec("daytona.v2.http") is None
    from daytona.v2 import GoneError

    assert GoneError.__name__ == "GoneError"


# The generic codes each source's catalog publishes, pinned per source: the
# shared codes.GENERIC_ERROR_CODES is the union, so a subset check against it cannot
# catch a source DROPPING a generic code it used to emit.
DAEMON_GENERIC_CODES = frozenset(
    {
        code_names.BAD_GATEWAY,
        code_names.BAD_REQUEST,
        code_names.CONFLICT,
        code_names.FORBIDDEN,
        code_names.GONE,
        code_names.INTERNAL_SERVER_ERROR,
        code_names.METHOD_NOT_ALLOWED,
        code_names.NOT_FOUND,
        code_names.PAYLOAD_TOO_LARGE,
        code_names.REQUEST_TIMEOUT,
        code_names.SERVICE_UNAVAILABLE,
        code_names.TOO_MANY_REQUESTS,
        code_names.UNAUTHORIZED,
        code_names.UNPROCESSABLE_ENTITY,
        code_names.UNSUPPORTED_MEDIA_TYPE,
    }
)

PROXY_GENERIC_CODES = frozenset(
    {
        code_names.BAD_GATEWAY,
        code_names.BAD_REQUEST,
        code_names.CONFLICT,
        code_names.FORBIDDEN,
        code_names.GATEWAY_TIMEOUT,
        code_names.INTERNAL_SERVER_ERROR,
        code_names.METHOD_NOT_ALLOWED,
        code_names.NOT_FOUND,
        code_names.SERVICE_UNAVAILABLE,
        code_names.UNAUTHORIZED,
    }
)


@pytest.mark.anyio
async def test_codes_module_matches_toolbox_error_catalog():
    # The committed toolbox contract is the source of truth.
    contract = _repo_file("contract/toolbox-openapi.json")
    spec = json.loads(contract.read_text())
    domain = spec["x-daytona-error-codes"]
    generic = spec["x-daytona-generic-codes"]
    assert set(spec["components"]["schemas"]["ErrorCode"]["enum"]) == set(domain)
    assert set(domain) == codes.DAEMON_ERROR_CODES
    # Pinned per-source: a generic code removed from the contract must go
    # red here, so the subset check the artifact would pass is not enough.
    assert set(generic) == DAEMON_GENERIC_CODES
    assert all(meta["source"] == "DAYTONA_DAEMON" for meta in domain.values())
    assert {code: meta["status"] for code, meta in domain.items()} == {
        code: codes.CODE_STATUS[code] for code in codes.DAEMON_ERROR_CODES
    }
    assert {code: meta["status"] for code, meta in generic.items()} == {
        code: codes.CODE_STATUS[code] for code in generic
    }
    assert {code: tuple(meta["extras"]) for code, meta in domain.items()} == {
        code: codes.DAEMON_CODE_EXTRAS.get(code, ()) for code in domain
    }
    assert {code: tuple(meta["extras"]) for code, meta in generic.items()} == {
        code: codes.DAEMON_CODE_EXTRAS.get(code, ()) for code in generic
    }


def test_codes_module_matches_api_error_catalog():
    contract = _repo_file("contract/api-openapi.json")
    spec = json.loads(contract.read_text())
    domain = spec["x-daytona-error-codes"]
    generic = spec["x-daytona-generic-codes"]

    assert set(domain) == codes.API_ERROR_CODES == set(API_ERROR_CLASSES)
    # The api's generic table mirrors the full shared set (pinned equality,
    # not subset — see DAEMON_GENERIC_CODES above).
    assert set(generic) == codes.GENERIC_ERROR_CODES
    assert all(meta["source"] == "DAYTONA_API" for meta in domain.values())
    assert {code: meta["status"] for code, meta in domain.items()} == {
        code: codes.CODE_STATUS[code] for code in codes.API_ERROR_CODES
    }
    assert {code: meta["status"] for code, meta in generic.items()} == {
        code: codes.CODE_STATUS[code] for code in generic
    }
    assert {code: tuple(meta["extras"]) for code, meta in domain.items()} == {
        code: codes.API_CODE_EXTRAS.get(code, ()) for code in domain
    }
    assert {code: tuple(meta["extras"]) for code, meta in generic.items()} == {
        code: codes.API_CODE_EXTRAS.get(code, ()) for code in generic
    }


def test_codes_module_matches_proxy_error_catalog():
    contract = _repo_file("contract/proxy-errors.json")
    spec = json.loads(contract.read_text())
    domain = {entry["code"]: entry["status"] for entry in spec["codes"]}
    generic = {entry["code"]: entry["status"] for entry in spec["generic"]}

    assert spec["source"] == "DAYTONA_PROXY"
    assert set(domain) == codes.PROXY_ERROR_CODES == set(PROXY_ERROR_CLASSES)
    assert set(generic) == PROXY_GENERIC_CODES
    assert domain == {code: codes.CODE_STATUS[code] for code in codes.PROXY_ERROR_CODES}
    assert generic == {code: codes.CODE_STATUS[code] for code in generic}
    assert {entry["code"]: tuple(entry["extras"]) for entry in spec["codes"] + spec["generic"]} == {
        entry["code"]: codes.PROXY_CODE_EXTRAS.get(entry["code"], ())
        for entry in spec["codes"] + spec["generic"]
    }


def test_codes_module_matches_router_error_catalog():
    contract = _repo_file("contract/router-errors.json")
    spec = json.loads(contract.read_text())
    domain = {entry["code"]: entry["status"] for entry in spec["codes"]}
    generic = {entry["code"]: entry["status"] for entry in spec["generic"]}

    assert spec["source"] == "DAYTONA_ROUTER"
    assert set(domain) == codes.ROUTER_ERROR_CODES == set(ROUTER_ERROR_CLASSES)
    # The router's generic table mirrors the full shared set, like the api's.
    assert set(generic) == codes.GENERIC_ERROR_CODES
    assert domain == {code: codes.CODE_STATUS[code] for code in codes.ROUTER_ERROR_CODES}
    assert generic == {code: codes.CODE_STATUS[code] for code in generic}
    assert {entry["code"]: tuple(entry["extras"]) for entry in spec["codes"] + spec["generic"]} == {
        entry["code"]: codes.ROUTER_CODE_EXTRAS.get(entry["code"], ())
        for entry in spec["codes"] + spec["generic"]
    }


def test_router_source_dispatches_by_source_and_code():
    assert isinstance(
        create_error("two regions", 400, code_names.REGION_MISMATCH, "DAYTONA_ROUTER"), BadRequestError
    )
    assert isinstance(
        create_error("temporarily unavailable", 503, code_names.PLACEMENT_UNAVAILABLE, "DAYTONA_ROUTER"),
        ServiceUnavailableError,
    )
    assert isinstance(
        create_error("body too large", 413, code_names.CREATE_BODY_TOO_LARGE, "DAYTONA_ROUTER"),
        PayloadTooLargeError,
    )


# SDK lifecycle bounds must agree with the committed API contract.
def test_lifecycle_bounds_match_the_api_contract():
    contract = _repo_file("contract/api-openapi.json")
    spec = json.loads(contract.read_text())
    props = spec["components"]["schemas"]["UpdateSandboxLifecyclePolicyRequest"]["properties"]

    assert props["timeout_seconds"]["maximum"] == LIFECYCLE_MAX_SECS
    assert props["idle_expiry_seconds"]["maximum"] == LIFECYCLE_MAX_SECS

    floor = re.search(r"below (\d+) is raised to (\d+)", props["idle_expiry_seconds"]["description"])
    assert floor is not None
    assert floor.group(1) == floor.group(2)
    types_source = (Path(__file__).resolve().parents[1] / "src" / "daytona" / "v2" / "types.py").read_text()
    assert f"below {floor.group(1)} raised to {floor.group(1)} server-side" in types_source


@pytest.mark.anyio
async def test_log_channel_extension_matches_known_vocabulary() -> None:
    contract = _repo_file("contract/toolbox-openapi.json")
    spec = json.loads(contract.read_text())
    assert set(spec["x-daytona-log-channels"]) == {
        "display",
        "error",
        "execute_result",
        "pty",
        "setup",
        "stderr",
        "stdout",
    }


def test_sdk_route_literals_bind_to_exactly_one_openapi_operation() -> None:
    operations = _read_contract_operations("api") + _read_contract_operations("toolbox")
    mismatches: list[str] = []
    for route_use in _collect_python_route_uses():
        candidates = [
            operation
            for operation in operations
            if operation[0] == route_use.method and _route_matches_spec(route_use.path, operation[1])
        ]
        # A literal segment beats a path parameter, so
        # ``GET /sandboxes/search`` binds to ``/sandboxes/search``, not to ``/sandboxes/{id}`` as well.
        exact = [
            operation for operation in candidates if _route_matches_spec_exactly(route_use.path, operation[1])
        ]
        matches = exact or candidates
        if len(matches) == 1:
            continue
        nearest = ", ".join(_nearest_spec_paths(route_use.method, route_use.path, operations)) or "(none)"
        mismatches.append(
            f"{route_use.file}:{route_use.line} {route_use.method} {route_use.path} from {route_use.raw} matched {len(matches)} operations; nearest: {nearest}"
        )
    assert mismatches == []


def test_build_log_discovery_forwarders_remain_in_route_drift_inventory() -> None:
    routes = {(use.method, use.path) for use in _collect_python_route_uses()}
    assert ("GET", "/images/{param}/build-logs-url") in routes
    assert ("GET", "/sandboxes/{param}/build-logs-url") in routes


# Route collection must resolve paths returned by awaited helpers.
def test_presigned_routes_built_from_awaited_helpers_remain_in_route_drift_inventory() -> None:
    routes = {(use.method, use.path) for use in _collect_python_route_uses()}
    for expected in (
        ("POST", "/sandboxes/{param}/files/sign"),
        ("POST", "/sandboxes/{param}/signing-key/rotate"),
    ):
        assert expected in routes


@pytest.mark.parametrize(
    ("contract", "allowlist"),
    [("toolbox", _TOOLBOX_ROUTE_ALLOWLIST), ("api", _API_ROUTE_ALLOWLIST)],
)
def test_contract_operations_are_covered_or_allow_listed(
    contract: str, allowlist: dict[tuple[str, str], str]
) -> None:
    route_uses = _collect_python_route_uses()
    operations = _read_contract_operations(contract)
    checked_operations = [
        (method, path) for method, path in operations if contract == "toolbox" or path.startswith("/volumes")
    ]

    def is_used(method: str, path: str) -> bool:
        return any(
            route_use.method == method and _route_matches_spec(route_use.path, path)
            for route_use in route_uses
        )

    missing = [
        f"{method} {path}"
        for method, path in checked_operations
        if (method, path) not in allowlist and not is_used(method, path)
    ]
    assert missing == []
    # An allow-list entry for a route the SDK does call is stale: the list must
    # shrink as coverage grows, or it stops meaning anything.
    stale = [
        f"{method} {path}"
        for method, path in allowlist
        if (method, path) not in set(operations) or is_used(method, path)
    ]
    assert stale == []


@pytest.mark.anyio
async def test_image_service_shapes_build_pull_list_and_tar_context():
    transport = RecordingTransport(
        [
            _image_operation_payload("img-build"),
            _image_page_payload("img-build"),
            _image_operation_payload("img-pull"),
            _image_page_payload("img-pull"),
            {"images": [_image_page_payload("img")], "next_cursor": "next"},
        ]
    )
    service = AsyncImagesService(transport)  # type: ignore[arg-type]

    assert (
        await service.build(ImageBuilder.from_dockerfile("FROM scratch\n"), name="built", region="us")
    ).id == "img-build"
    build_spec = transport.specs[0]
    assert build_spec.method == "POST"
    assert build_spec.path == "/images"
    assert build_spec.json_body == {
        "name": "built",
        "dockerfile": "FROM scratch\n",
        "region_id": "us",
    }
    assert build_spec.timeout is None

    assert (await service.pull(image="python:3.12", name="pulled", region="eu")).id == "img-pull"
    assert transport.specs[2].json_body == {
        "name": "pulled",
        "source": {"type": "registry", "ref": "python:3.12"},
        "region_id": "eu",
    }
    page = await service.list(cursor="c", limit=5)
    assert [item.id for item in page.items] == ["img"]
    assert page.next_cursor == "next"
    assert transport.specs[4].params == {"anonymous": False, "cursor": "c", "limit": 5}


@pytest.mark.anyio
async def test_images_pull_accepts_ref_positionally() -> None:
    """pull("ref", name=...) is identical to pull(image="ref", name=...) on the wire."""
    transport = RecordingTransport(
        [
            _image_operation_payload("img-pos"),
            _image_page_payload("img-pos"),
        ]
    )
    service = AsyncImagesService(transport)  # type: ignore[arg-type]

    result = await service.pull("python:3.12", name="pos-pull")

    assert result.id == "img-pos"
    post_spec = transport.specs[0]
    assert post_spec.method == "POST"
    assert post_spec.path == "/images"
    assert post_spec.json_body == {
        "name": "pos-pull",
        "source": {"type": "registry", "ref": "python:3.12"},
    }


@pytest.mark.anyio
async def test_list_audit_actions_reads_public_catalog() -> None:
    transport = RecordingTransport(
        [{"targets": [{"target_type": "sandbox", "actions": ["create", "delete"]}]}]
    )
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    actions = await client.list_audit_actions()

    assert actions == AuditActionList(
        targets=(AuditActionGroup(target_type="sandbox", actions=("create", "delete")),)
    )
    assert transport.specs == [RequestSpec("GET", "/audit/actions")]


@pytest.mark.anyio
async def test_list_audit_actions_malformed_payload_raises_typed_error() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    for payload in (["not", "a", "dict"], {"targets": "nope"}):
        client._api = RecordingTransport([payload])  # type: ignore[attr-defined]
        with pytest.raises(BadRequestError, match="audit actions response"):
            await client.list_audit_actions()


_CURRENT_API_KEY_PAYLOAD = {
    "id": "0d1c8a6e-4a0a-4f6c-9c58-6a7b2f4b7c11",
    "name": "ci",
    "key_prefix": "dtn_a1b2c3d",
    "key_suffix": "xyz",
    "project_id": "6e1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b",
    "context": {
        "project_name": "sdk-tests",
        "org_id": "b7a6c5d4-e3f2-4a1b-9c8d-7e6f5a4b3c2d",
        "org_name": "acme",
    },
    "project_permissions": ["read:sandboxes"],
    "created_at": "2026-09-08T14:00:00Z",
    "expires_at": None,
    "last_used_at": None,
    "service_account_id": None,
    "sync_to_v0": True,
    "sync_to_v1": True,
}


@pytest.mark.anyio
async def test_current_api_key_describes_the_presented_key() -> None:
    transport = RecordingTransport([_CURRENT_API_KEY_PAYLOAD])
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    key = await client.current_api_key()

    assert key == ProjectApiKey(
        id="0d1c8a6e-4a0a-4f6c-9c58-6a7b2f4b7c11",
        name="ci",
        project_id="6e1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b",
        project_permissions=("read:sandboxes",),
        created_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
        context=ProjectApiKeyContext(
            org_id="b7a6c5d4-e3f2-4a1b-9c8d-7e6f5a4b3c2d", org_name="acme", project_name="sdk-tests"
        ),
        key_prefix="dtn_a1b2c3d",
        key_suffix="xyz",
    )
    # An SDK-owned model: no generated-model methods, no key-sync bookkeeping fields.
    assert not hasattr(key, "to_dict") and not hasattr(key, "sync_to_v0")
    assert transport.specs == [RequestSpec("GET", "/api-keys/current")]


@pytest.mark.anyio
async def test_current_api_key_malformed_payload_raises_typed_error() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    for payload in (["not", "a", "dict"], {"id": "only"}):
        client._api = RecordingTransport([payload])  # type: ignore[attr-defined]
        with pytest.raises(BadRequestError, match="current API key response"):
            await client.current_api_key()


_PROJECT_BUDGET_PAYLOAD = {
    "monthly_limit_cents": 50000,
    "period_start_date": "2026-10-01",
    "spent_cents": 41237,
    "evaluated_at": "2026-10-07T09:41:00Z",
    "exceeded_at": None,
    "stop_at": None,
    "future_field": "tolerated",
    "created_at": None,
    "updated_at": None,
}


@pytest.mark.anyio
async def test_get_project_budget_reads_the_key_projects_budget() -> None:
    transport = RecordingTransport([_PROJECT_BUDGET_PAYLOAD])
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    budget = await client.get_project_budget()

    assert isinstance(budget, ProjectBudget)
    assert budget.monthly_limit_cents == 50000
    assert budget.period_start_date.isoformat() == "2026-10-01"
    assert budget.spent_cents == 41237
    assert budget.evaluated_at is not None and budget.evaluated_at.year == 2026
    assert budget.exceeded_at is None
    assert budget.stop_at is None
    assert transport.specs == [RequestSpec("GET", "/usage/budget")]


@pytest.mark.anyio
async def test_get_project_budget_unevaluated_period_has_null_spend() -> None:
    payload = {
        **_PROJECT_BUDGET_PAYLOAD,
        "spent_cents": None,
        "evaluated_at": None,
    }
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = RecordingTransport([payload])  # type: ignore[attr-defined]

    budget = await client.get_project_budget()

    assert budget is not None
    assert budget.spent_cents is None
    assert budget.evaluated_at is None


@pytest.mark.anyio
async def test_get_project_budget_returns_the_unset_state() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = RecordingTransport(  # type: ignore[attr-defined]
        [
            {
                "monthly_limit_cents": None,
                "period_start_date": None,
                "spent_cents": None,
                "evaluated_at": None,
                "exceeded_at": None,
                "stop_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]
    )

    budget = await client.get_project_budget()
    assert budget.monthly_limit_cents is None
    assert budget.period_start_date is None


@pytest.mark.anyio
async def test_get_project_budget_propagates_other_errors() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = RecordingTransport(  # type: ignore[attr-defined]
        [create_error("missing read:spending", 403, "FORBIDDEN", "DAYTONA_API")]
    )

    with pytest.raises(DaytonaError) as exc_info:
        await client.get_project_budget()
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_get_project_budget_malformed_payload_raises_typed_error() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    for payload in (
        ["not", "a", "dict"],
        {
            "monthly_limit_cents": "not-an-integer",
            "period_start_date": None,
            "created_at": None,
            "updated_at": None,
        },
    ):
        client._api = RecordingTransport([payload])  # type: ignore[attr-defined]
        with pytest.raises(BadRequestError, match="project budget response"):
            await client.get_project_budget()


_ORGANIZATION_LIMITS_PAYLOAD = {
    "sandboxes": {
        # Complete the wire model; only the named limits below are under test.
        **{
            name: {"limit": None, "usage": 0}
            for name, field in SandboxLimits.model_fields.items()
            if field.is_required()
        },
        "concurrent": {"limit": 100, "usage": 3},
        "total": {"limit": 400, "usage": 11},
    },
    "sandbox_resources": {"max_cores": 4, "max_memory_mib": 8192},
    "image_builds": {"concurrent": {"limit": None, "usage": 0}},
    "storage": {
        "images": {"limit": None, "usage": 4},
        "snapshots": {"limit": None, "usage": 0},
        "volumes": {"limit": None, "usage": 1},
    },
    "secrets_per_project": {"limit": 500, "usage": 2},
    "api_keys_per_project": {"limit": 100, "usage": 1},
    "api_keys_per_organization": {"limit": 100, "usage": 0},
}


@pytest.mark.anyio
async def test_get_limits_looks_up_the_key_organization_once() -> None:
    transport = RecordingTransport(
        [_CURRENT_API_KEY_PAYLOAD, _ORGANIZATION_LIMITS_PAYLOAD, _ORGANIZATION_LIMITS_PAYLOAD]
    )
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    await client.get_limits()
    limits = await client.get_limits()

    org_id = _CURRENT_API_KEY_PAYLOAD["context"]["org_id"]  # type: ignore[index]
    assert isinstance(limits, OrganizationLimits)
    assert limits.sandboxes.concurrent.limit == 100
    assert limits.members is None
    assert transport.specs == [
        RequestSpec("GET", "/api-keys/current"),
        RequestSpec("GET", f"/orgs/{org_id}/limits"),
        RequestSpec("GET", f"/orgs/{org_id}/limits"),
    ]


@pytest.mark.anyio
async def test_get_limits_reads_the_named_organization() -> None:
    transport = RecordingTransport([_ORGANIZATION_LIMITS_PAYLOAD])
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    await client.get_limits(organization_id="org/2")

    assert transport.specs == [RequestSpec("GET", "/orgs/org%2F2/limits")]


@pytest.mark.anyio
async def test_get_limits_shares_one_lookup_across_concurrent_first_calls() -> None:
    lookups = 0

    class SlowLookupTransport(RecordingTransport):
        async def send(self, spec: RequestSpec):
            nonlocal lookups
            self.specs.append(spec)
            if spec.path == "/api-keys/current":
                lookups += 1
                await asyncio.sleep(0.01)
                return _CURRENT_API_KEY_PAYLOAD
            return _ORGANIZATION_LIMITS_PAYLOAD

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = SlowLookupTransport([])  # type: ignore[attr-defined]

    await asyncio.gather(*(client.get_limits() for _ in range(5)))

    assert lookups == 1


@pytest.mark.anyio
async def test_get_limits_retries_the_lookup_after_a_failed_one() -> None:
    unauthorized = create_error("unauthorized", 401, "UNAUTHORIZED", "DAYTONA_API")
    transport = RecordingTransport([unauthorized, _CURRENT_API_KEY_PAYLOAD, _ORGANIZATION_LIMITS_PAYLOAD])
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = transport  # type: ignore[attr-defined]

    with pytest.raises(DaytonaError):
        await client.get_limits()
    await client.get_limits()

    assert [spec.path for spec in transport.specs] == [
        "/api-keys/current",
        "/api-keys/current",
        f"/orgs/{_CURRENT_API_KEY_PAYLOAD['context']['org_id']}/limits",  # type: ignore[index]
    ]


@pytest.mark.anyio
async def test_get_limits_requires_organization_id_with_jwt_token() -> None:
    transport = RecordingTransport([])
    client = AsyncDaytona(DaytonaConfig(jwt_token="jwt", project_id="project-1"))
    client._api = transport  # type: ignore[attr-defined]

    with pytest.raises(
        BadRequestError, match="organization_id is required with jwt_token authentication"
    ) as missing:
        await client.get_limits()
    assert (missing.value.status_code, missing.value.code, missing.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )
    with pytest.raises(BadRequestError, match="organization_id must be a non-empty string"):
        await client.get_limits(organization_id="")
    assert transport.specs == []


@pytest.mark.anyio
async def test_resource_service_iterators_use_iter_everywhere() -> None:
    image_service = AsyncImagesService(
        RecordingTransport(
            [
                {"images": [_image_page_payload("img-1")], "next_cursor": "cursor-1"},
                {"images": [_image_page_payload("img-2")], "next_cursor": "cursor-1"},
            ]
        )
    )  # type: ignore[arg-type]
    snapshot_service = AsyncSnapshotsService(
        RecordingTransport(
            [
                {
                    "snapshots": [
                        {
                            "class": "linux",
                            "id": "snap-1",
                            "kind": "full",
                            "name": "snap-1",
                            "region_id": "us",
                            "status": "ready",
                            "region_ids": ["us"],
                            "created_at": _DEFAULT_TIME,
                            "updated_at": _DEFAULT_TIME,
                            "cursor": "cursor-0",
                        }
                    ],
                    "next_cursor": "cursor-1",
                },
                {
                    "snapshots": [
                        {
                            "class": "linux",
                            "id": "snap-2",
                            "kind": "full",
                            "name": "snap-2",
                            "region_id": "us",
                            "status": "ready",
                            "region_ids": ["us"],
                            "created_at": _DEFAULT_TIME,
                            "updated_at": _DEFAULT_TIME,
                            "cursor": "cursor-1",
                        }
                    ],
                    "next_cursor": "cursor-1",
                },
            ]
        )
    )  # type: ignore[arg-type]
    secret_service = AsyncSecretsService(
        RecordingTransport(
            [
                {"secrets": [_secret_list_item_payload("TOKEN")], "next_cursor": "cursor-1"},
                {"secrets": [_secret_list_item_payload("TOKEN_2")], "next_cursor": "cursor-1"},
            ]
        )
    )  # type: ignore[arg-type]

    volume_service = AsyncVolumesService(
        RecordingTransport(
            [
                {"volumes": [_volume_payload("vol-1")], "next_cursor": "cursor-1"},
                {"volumes": [_volume_payload("vol-2")], "next_cursor": "cursor-1"},
            ]
        )
    )  # type: ignore[arg-type]

    assert [item.id async for item in image_service.iter()] == ["img-1", "img-2"]
    assert [item.id async for item in snapshot_service.iter()] == ["snap-1", "snap-2"]
    assert [item.id async for item in volume_service.iter()] == ["vol-1", "vol-2"]
    assert [item.name async for item in secret_service.iter()] == ["TOKEN", "TOKEN_2"]


@pytest.mark.anyio
async def test_git_clone_pull_push_use_remote_deadline_defaults_and_positive_overrides():
    transport = RecordingTransport([{}, {}, {}, {}, {}, {}])
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    await git.clone("https://example.invalid/repo.git", "/repo")
    await git.pull("/repo")
    await git.push("/repo")
    await git.clone("https://example.invalid/repo.git", "/repo", timeout=12.5)
    await git.pull("/repo", timeout=3.0)
    await git.push("/repo", timeout=7.0)

    assert transport.specs[0].path == "/git/clone"
    assert transport.specs[0].unbounded_read is False
    assert transport.specs[0].timeout == 1800
    assert transport.specs[1].path == "/git/pull"
    assert transport.specs[1].unbounded_read is False
    assert transport.specs[1].timeout == 1800
    assert transport.specs[2].path == "/git/push"
    assert transport.specs[2].unbounded_read is False
    assert transport.specs[2].timeout == 1800
    assert transport.specs[3].timeout == 12.5
    assert transport.specs[4].timeout == 3.0
    assert transport.specs[5].timeout == 7.0


def test_request_kwargs_keeps_plain_requests_on_transport_defaults():
    assert _request_kwargs(RequestSpec("POST", "/git/pull"), 12.5) == {
        "params": None,
        "json": None,
        "data": None,
        "files": None,
    }


@pytest.mark.anyio
async def test_git_init_remotes_and_config_shape_transport_requests():
    transport = RecordingTransport(
        [
            {},
            {"remotes": [{"name": "origin", "url": "https://example.invalid/repo.git"}]},
            {},
            {"value": "Ada"},
            {},
            {"value": None},
        ]
    )
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    await git.init("/repo", bare=True, initial_branch="trunk")
    assert transport.specs[0].path == "/git/init"
    assert transport.specs[0].json_body == {
        "path": "/repo",
        "bare": True,
        "initial_branch": "trunk",
    }

    assert list((await git.list_remotes("/repo")).items) == [
        {"name": "origin", "url": "https://example.invalid/repo.git"}
    ]
    assert transport.specs[1].params == {"path": "/repo"}

    await git.add_remote("/repo", "origin", "https://example.invalid/repo.git")
    assert transport.specs[2].json_body == {
        "path": "/repo",
        "name": "origin",
        "url": "https://example.invalid/repo.git",
    }

    assert await git.get_config("user.name", path="/repo", scope="local") == "Ada"
    assert transport.specs[3].params == {
        "key": "user.name",
        "path": "/repo",
        "scope": "local",
    }

    await git.set_config("user.name", "Ada", path="/repo", scope="local")
    assert transport.specs[4].json_body == {
        "key": "user.name",
        "value": "Ada",
        "path": "/repo",
        "scope": "local",
    }

    assert await git.get_config("demo.missing") is None
    assert transport.specs[5].params == {"key": "demo.missing", "scope": "global"}


@pytest.mark.anyio
async def test_git_reset_and_restore_allow_minimal_request_shapes():
    transport = RecordingTransport([{}, {}])
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    await git.reset("/repo")
    await git.restore("/repo", ["README.md"])

    assert transport.specs[0].json_body == {"path": "/repo"}
    assert transport.specs[1].json_body == {"path": "/repo", "files": ["README.md"]}


def test_filesystem_items_tolerate_absent_optional_fields_and_prefer_explicit_metadata() -> None:
    from daytona.v2._core import expect_file_info

    minimal_payload = {"name": "a", "isDir": False, "size": 1, "mode": "-rw-r--r--", "modTime": _DEFAULT_TIME}
    # Listing context: path is derived from the directory that was listed —
    # real information, not a fabricated absolute path.
    listed = expect_file_info(minimal_payload, parent="/tmp/dir/")
    assert (listed.name, listed.path, listed.is_dir) == ("a", "/tmp/dir/a", False)
    # Missing metadata stays empty rather than copying a differently formatted field.
    assert (listed.modified_at, listed.owner, listed.group, listed.permissions) == ("", "", "", "")
    assert listed.mod_time == _DEFAULT_TIME
    # stat() context: no parent → path stays empty rather than the bare name.
    assert expect_file_info(minimal_payload).path == ""

    full_payload = {
        **minimal_payload,
        "path": "/tmp/a",
        "modifiedAt": "later",
        "owner": "me",
        "group": "us",
        "permissions": "600",
    }
    full = expect_file_info(full_payload, parent="/elsewhere")
    assert (full.path, full.modified_at, full.owner, full.permissions) == ("/tmp/a", "later", "me", "600")

    with pytest.raises(DaytonaError):
        expect_file_info({"name": "no-size"})


@pytest.mark.anyio
async def test_filesystem_list_includes_depth_only_when_requested():
    def payload(name: str, is_dir: bool) -> dict[str, object]:
        return {
            "name": name,
            "path": f"/tmp/{name}",
            "isDir": is_dir,
            "size": 0 if is_dir else 1,
            "mode": "drwxr-xr-x" if is_dir else "-rw-r--r--",
            "modTime": _DEFAULT_TIME,
            "modifiedAt": _DEFAULT_TIME,
            "owner": "root",
            "group": "root",
            "permissions": "755" if is_dir else "644",
        }

    file_payload, directory_payload = payload("a", False), payload("b", True)
    transport = RecordingTransport([[file_payload], [directory_payload]])
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]

    # Typed items (parity with the TS FileInfo), not raw dicts.
    deep = (await fs.list("/tmp", depth=2)).items
    assert [(item.name, item.is_dir) for item in deep] == [("a", False)]
    assert isinstance(deep[0], FileInfo)
    assert [(item.name, item.is_dir) for item in (await fs.list("/tmp")).items] == [("b", True)]
    assert transport.specs[0].params == {"path": "/tmp", "depth": 2}
    assert transport.specs[1].params == {"path": "/tmp"}


@pytest.mark.anyio
async def test_secret_services_shape_routes():
    transport = RecordingTransport(
        [
            {"secrets": [_secret_list_item_payload("TOKEN")]},
            _secret_payload("TOKEN"),
            _secret_payload("TOKEN"),
            {},
        ]
    )

    secret = AsyncSecretsService(transport)  # type: ignore[arg-type]
    assert [item.name for item in (await secret.list()).items] == ["TOKEN"]
    meta = await secret.get("TOKEN")
    assert meta.name == "TOKEN"
    assert isinstance(meta, Secret)
    await secret.set(
        "TOKEN", "value", allowed_domains=["*.example.com"], description="desc", if_not_exists=True
    )
    assert transport.specs[2].json_body == {
        "value": "value",
        "allowed_domains": ["*.example.com"],
        "description": "desc",
    }
    assert transport.specs[2].headers == {"If-None-Match": "*"}
    await secret.delete("TOKEN")

    # set with ["*"] sends allowed_domains: ["*"]
    transport2 = RecordingTransport([_secret_payload("WIDE")])
    secret2 = AsyncSecretsService(transport2)  # type: ignore[arg-type]
    await secret2.set("WIDE", "val", allowed_domains=["*"])
    assert transport2.specs[0].json_body == {"value": "val", "allowed_domains": ["*"]}


def test_secret_set_without_allowed_domains_raises_type_error():
    """Calling set() without allowed_domains is a TypeError (required keyword-only arg)."""
    with pytest.raises(TypeError):
        AsyncSecretsService(RecordingTransport([{}])).set("X", "v")  # type: ignore[call-arg]


def test_python_root_exports_interpreter_context_json() -> None:
    from daytona.v2 import InterpreterContextJSON

    assert InterpreterContextJSON.__name__ == "InterpreterContextJSON"


@pytest.mark.anyio
async def test_git_filesystem_process_and_interpreter_shape_transport_requests(monkeypatch):
    transport = RecordingTransport(
        [
            {},
            _git_status_payload("main"),
            {"branches": ["main", "dev"]},
            [_commit_info_payload("h")],
            {},
            {},
            {},
            {},
            {},
            {},
            {"hash": "h"},
            {},
            {},
            b"hello",
            {},
            [
                {
                    "name": "a",
                    "path": "/tmp/a",
                    "isDir": False,
                    "size": 1,
                    "mode": "-rw-r--r--",
                    "modTime": _DEFAULT_TIME,
                    "modifiedAt": _DEFAULT_TIME,
                    "owner": "root",
                    "group": "root",
                    "permissions": "644",
                }
            ],
            {
                "group": "1000",
                "isDir": False,
                "modTime": "2026-09-15 00:00:00 +0000 UTC",
                "mode": "-rw-r--r--",
                "modifiedAt": _DEFAULT_TIME,
                "name": "a",
                "owner": "1000",
                "path": "/tmp/a",
                "permissions": "0644",
                "size": 1,
            },
            {},
            {},
            {},
            {"items": [{"path": "/tmp/a"}], "nextCursor": "fn"},
            {"files": ["/tmp/a"]},
            {"items": [{"file": "/tmp/a", "replacements": 1}]},
            {},
            _process_payload("proc", "exec", "running"),
            {"reason": "signaled", "signal": "SIGTERM"},
            {"items": [_log_frame_payload("c1", "out")], "nextCursor": "c1"},
            {"items": [], "nextCursor": "c1"},
            [_process_payload("proc", "exec", "running")],
            _session_payload("ctx", kernel="javascript"),
            _process_payload("code", "code", "running", language="javascript"),
            {"reason": "timed_out"},
            _process_payload("code", "code", "running", language="javascript"),
            {},
            {"reason": "exited", "exitCode": 130},
            {
                "frames": [
                    _log_frame_payload("c2", "after"),
                    _log_frame_payload("c3", '{"mime":"text/plain","data":"rich"}', channel="display"),
                    _log_frame_payload(
                        "c4", '{"text/html":"<b>x</b>","application/json":{"x":1}}', channel="display"
                    ),
                ],
                "nextCursor": "c4",
                "eof": False,
                "truncatedHead": False,
            },
            {"frames": [], "nextCursor": "c3", "eof": True, "truncatedHead": False},
        ]
    )

    git = AsyncGitService(transport)  # type: ignore[arg-type]
    await git.clone(
        "https://example.invalid/repo.git",
        "/repo",
        branch="main",
        commit_id="abc",
        username="u",
        password="p",
    )
    assert transport.specs[0].json_body == {
        "url": "https://example.invalid/repo.git",
        "path": "/repo",
        "branch": "main",
        "commit_id": "abc",
        "username": "u",
        "password": "p",
    }
    assert (await git.status("/repo")).current_branch == "main"
    assert list((await git.list_branches("/repo")).items) == ["main", "dev"]
    assert (await git.history("/repo")).items[0]["hash"] == "h"
    await git.create_branch("/repo", "feature")
    await git.delete_branch("/repo", "feature")
    await git.checkout_branch("/repo", "main")
    await git.add("/repo", ["README.md"])
    await git.reset("/repo", mode="mixed", target="HEAD~1", files=["README.md"])
    await git.restore("/repo", ["README.md"], staged=True, worktree=False, source="HEAD")
    assert (await git.commit("/repo", "msg", author="A", email="a@example.invalid")).hash == "h"
    await git.pull("/repo", username="u")
    await git.push("/repo", password="p")
    assert [spec.path for spec in transport.specs[:13]] == [
        "/git/clone",
        "/git/status",
        "/git/branches",
        "/git/history",
        "/git/branches",
        "/git/branches",
        "/git/checkout",
        "/git/add",
        "/git/reset",
        "/git/restore",
        "/git/commit",
        "/git/pull",
        "/git/push",
    ]
    assert transport.specs[8].json_body == {
        "path": "/repo",
        "mode": "mixed",
        "target": "HEAD~1",
        "files": ["README.md"],
    }
    assert transport.specs[9].json_body == {
        "path": "/repo",
        "files": ["README.md"],
        "staged": True,
        "worktree": False,
        "source": "HEAD",
    }

    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    assert await fs.read_text("/tmp/a") == "hello"
    await fs.write_text("/tmp/a", "hello")
    assert [item.name for item in (await fs.list("/tmp")).items] == ["a"]
    stat = await fs.stat("/tmp/a")
    assert stat.name == "a"
    assert stat.path == "/tmp/a"
    await fs.mkdir("/tmp/new", mode="0700")
    await fs.remove("/tmp/a", recursive=True)
    await fs.move("/tmp/a", "/tmp/b")
    assert (await fs.grep("/tmp", "needle")).next_cursor == "fn"
    assert list((await fs.glob("/tmp", "*.txt")).items) == ["/tmp/a"]
    assert list((await fs.replace(["/tmp/a"], "old", "new")).items) == [{"file": "/tmp/a", "replacements": 1}]
    await fs.chmod("/tmp/a", mode="0644", owner="me", group="staff")
    assert transport.specs[13].path == "/files/download"
    assert transport.specs[14].path == "/files/upload"
    assert transport.specs[22].json_body == {"files": ["/tmp/a"], "pattern": "old", "newValue": "new"}

    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]
    result = await process.run(argv=["bash", "-lc", "echo out"], name="job", timeout=0.01)
    assert result.reason == "signaled"
    assert result.signal == "TERM"
    assert result.stdout == "out"
    assert transport.specs[24].json_body == {
        "kind": "exec",
        "argv": ["bash", "-lc", "echo out"],
        "name": "job",
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
    }
    assert process.connect({"sandboxId": "sbx", "processId": "proc"}).id == "proc"
    with pytest.raises(Exception, match="different sandbox"):
        await process.connect({"sandboxId": "other", "processId": "proc"})
    assert (await process.list(state="running", kind="exec", session_id="s", name="job", pid=42)).items[
        0
    ].id == "proc"
    interpreter = AsyncInterpreterService("sbx", transport, process)

    class ImmediateGate:
        async def run(self, timeout: Optional[float], task):
            return await task(timeout)

    monkeypatch.setattr(interpreter, "_gate_for", lambda _context_id: ImmediateGate())
    context = await interpreter.create_context(
        language="javascript", context_id="ctx", cwd="/w", env={"A": "B"}
    )
    now = iter([0.0, 0.0, 0.01, 0.01, 0.01])
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(now, 0.01))
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    rich = await interpreter.run("print(1)", context=context, timeout=0.01)
    assert rich.process.process_id == "code"
    session_spec = next(
        spec
        for spec in transport.specs
        if spec.path == "/sessions"
        and spec.json_body == {"kernel": "javascript", "id": "ctx", "cwd": "/w", "env": {"A": "B"}}
    )
    code_spec = next(
        spec
        for spec in transport.specs
        if spec.path == "/processes" and spec.json_body and spec.json_body.get("kind") == "code"
    )
    assert session_spec.json_body == {
        "kernel": "javascript",
        "id": "ctx",
        "cwd": "/w",
        "env": {"A": "B"},
    }
    assert code_spec.json_body == {
        "kind": "code",
        "code": "print(1)",
        "sessionId": "ctx",
        "stdin": "closed",
        "language": "javascript",
        "keepLogs": "on_exit_ttl",
    }


@pytest.mark.anyio
async def test_process_start_preserves_closed_input_literals_in_the_create_body() -> None:
    transport = RecordingTransport([_process_payload("proc", "exec", "running")])

    handle = await AsyncProcessService("sbx", transport).start(  # type: ignore[arg-type]
        "cat",
        stdin="open",
        keep_logs="none",
    )

    assert handle.kind == "exec"
    assert transport.specs[0].json_body == {
        "kind": "exec",
        "shellCommand": "cat",
        "stdin": "open",
        "keepLogs": "none",
    }


@pytest.mark.anyio
async def test_pty_start_uses_requested_kind_when_response_kind_is_unrecognized() -> None:
    transport = RecordingTransport([_process_payload("pty-1", "sidecar", "running")])

    handle = await AsyncPtyService(AsyncProcessService("sbx", transport)).start("bash")  # type: ignore[arg-type]

    assert handle.kind == "pty"
    assert handle.to_json() == {"sandboxId": "sbx", "processId": "pty-1", "kind": "pty"}


@pytest.mark.anyio
async def test_process_code_start_uses_namespace_default_language():
    transport = RecordingTransport([_process_payload("code", "code", "running", language="javascript")])
    await AsyncProcessService("sbx", transport, language="javascript")._start_process(code="1 + 1")  # type: ignore[arg-type]
    assert transport.specs[0].json_body == {
        "kind": "code",
        "code": "1 + 1",
        "stdin": "closed",
        "language": "javascript",
    }


@pytest.mark.anyio
async def test_interpreter_context_free_runs_omit_session_id():
    transport = RecordingTransport(
        [
            _process_payload("p1", "code", "running", language="python"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([_log_frame_payload("1", "first")], next_cursor="1"),
            _log_page_payload([], next_cursor="1"),
            _process_payload("p2", "code", "running", language="python"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([_log_frame_payload("2", "second")], next_cursor="2"),
            _log_page_payload([], next_cursor="2"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))

    first = await interpreter.run("x = 5", language="python")
    second = await interpreter.run("print(x)", language="python")

    assert first.logs.stdout == "first"
    assert second.logs.stdout == "second"
    assert transport.specs[0].json_body == {
        "kind": "code",
        "code": "x = 5",
        "stdin": "closed",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }
    assert transport.specs[4].json_body == {
        "kind": "code",
        "code": "print(x)",
        "stdin": "closed",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }


@pytest.mark.anyio
async def test_interpreter_context_free_runs_serialize_concurrent_runs_before_submitting_the_next_process(
    monkeypatch,
) -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    submitted: list[tuple[str, Optional[float]]] = []

    async def fake_run_implicit_context(
        *,
        code: str,
        language: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        assert language == "python"
        submitted.append((code, timeout))
        if code == "first":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), timeout=1)
        return InterpreterResult(
            process=InterpreterProcess(f"proc-{code}", 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_implicit_context", fake_run_implicit_context)

    first = asyncio.create_task(interpreter.run("first", language="python"))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(interpreter.run("second", language="python"))
    await asyncio.sleep(0)

    assert submitted == [("first", None)]

    release_first.set()
    results = await asyncio.gather(first, second)

    assert submitted == [("first", None), ("second", None)]
    assert sorted(result.logs.stdout for result in results) == ["first", "second"]


@pytest.mark.anyio
async def test_interpreter_context_asdict_is_identity_only_and_rebinds_cleanly():
    transport = RecordingTransport(
        [
            _session_payload("ctx", kernel="python"),
            _process_payload("proc", "code", "running", language="python"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([], next_cursor="0"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))
    context = await interpreter.create_context(language="python", context_id="ctx")

    serialized = asdict(context)
    assert serialized == {"sandbox_id": "sbx", "context_id": "ctx", "language": "python"}
    assert json.loads(json.dumps(serialized)) == serialized

    rebound = interpreter.connect_context(serialized)
    result = await rebound.run("print(1)")

    assert result.process.process_id == "proc"
    assert transport.specs[1].json_body == {
        "kind": "code",
        "code": "print(1)",
        "sessionId": "ctx",
        "stdin": "closed",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }


def test_interpreter_connect_context_requires_language_when_rehydrating_by_id() -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))

    with pytest.raises(
        DaytonaError, match="language is required when reconnecting an interpreter context by id"
    ) as exc_info:
        interpreter.connect_context("ctx")

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == code_names.BAD_REQUEST
    assert transport.specs == []


@pytest.mark.anyio
async def test_interpreter_explicit_context_surfaces_session_not_found_without_recreate_or_retry():
    transport = RecordingTransport(
        [
            _session_payload("ctx", kernel="python"),
            create_error("missing", 404, code_names.SESSION_NOT_FOUND, "DAYTONA_DAEMON"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))
    context = await interpreter.create_context(language="python", context_id="ctx")

    with pytest.raises(SessionNotFoundError) as exc_info:
        await context.run("print(1)")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == code_names.SESSION_NOT_FOUND
    assert exc_info.value.source == "DAYTONA_DAEMON"
    assert [spec.path for spec in transport.specs] == ["/sessions", "/processes"]
    assert transport.specs[1].json_body == {
        "kind": "code",
        "code": "print(1)",
        "sessionId": "ctx",
        "stdin": "closed",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }


@pytest.mark.anyio
async def test_sandbox_run_code_rejects_a_foreign_interpreter_context_without_a_wire_call():
    foreign_transport = RecordingTransport([_session_payload("ctx", kernel="python")])
    foreign = AsyncSandbox(sandbox_info_from_id("foreign"), None, foreign_transport)
    local_transport = RecordingTransport([])
    local = AsyncSandbox(sandbox_info_from_id("local"), None, local_transport)
    foreign_context = await foreign.interpreter.create_context(language="python", context_id="ctx")

    with pytest.raises(DaytonaError) as exc_info:
        await local.run_code("print(1)", context=foreign_context)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == code_names.BAD_REQUEST
    assert exc_info.value.source == "SDK"
    assert local_transport.specs == []
    assert foreign_transport.specs == [
        RequestSpec("POST", "/sessions", json_body={"kernel": "python", "id": "ctx"})
    ]


@pytest.mark.anyio
async def test_interpreter_delete_context_rejects_a_foreign_context_without_a_wire_call():
    foreign_transport = RecordingTransport([_session_payload("ctx", kernel="python")])
    foreign = AsyncSandbox(sandbox_info_from_id("foreign"), None, foreign_transport)
    local_transport = RecordingTransport([])
    local = AsyncSandbox(sandbox_info_from_id("local"), None, local_transport)
    foreign_context = await foreign.interpreter.create_context(language="python", context_id="ctx")

    with pytest.raises(DaytonaError) as exc_info:
        await local.interpreter.delete_context(foreign_context)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == code_names.BAD_REQUEST
    assert exc_info.value.source == "SDK"
    assert local_transport.specs == []
    assert foreign_transport.specs == [
        RequestSpec("POST", "/sessions", json_body={"kernel": "python", "id": "ctx"})
    ]


@pytest.mark.anyio
async def test_interpreter_isolated_runs_use_throwaway_context_and_delete_best_effort():
    transport = RecordingTransport(
        [
            _session_payload("isolated-python-12345678123456781234567812345678", kernel="python"),
            _process_payload("code", "code", "running", language="python"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([], next_cursor="0"),
            create_error("gone", 404, code_names.SESSION_NOT_FOUND, "DAYTONA_DAEMON"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "daytona.v2.interpreter.uuid.uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678")
        )
        result = await interpreter.run("print(1)", language="python", isolated=True)

    assert result.process.process_id == "code"
    assert transport.specs[0].json_body == {
        "kernel": "python",
        "id": "isolated-python-12345678123456781234567812345678",
    }
    assert transport.specs[1].json_body == {
        "kind": "code",
        "code": "print(1)",
        "sessionId": "isolated-python-12345678123456781234567812345678",
        "stdin": "closed",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }
    assert transport.specs[4].path == "/sessions/isolated-python-12345678123456781234567812345678"
    assert interpreter._context_gates == {}


@pytest.mark.anyio
async def test_interpreter_isolated_run_spends_the_caller_budget_on_context_creation(monkeypatch) -> None:

    clock = iter([100.0] + [105.0] * 20)
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(clock))
    transport = RecordingTransport(
        [
            _session_payload("isolated-python-12345678123456781234567812345678", kernel="python"),
            create_error("gone", 404, code_names.SESSION_NOT_FOUND, "DAYTONA_DAEMON"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "daytona.v2.interpreter.uuid.uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678")
        )
        # POST /sessions took 5s of a 2s budget: the run must expire, not start
        # the execution with a fresh (or unbounded) budget.
        with pytest.raises(DaytonaError) as exc_info:
            await interpreter.run("print(1)", language="python", isolated=True, timeout=2)

    assert exc_info.value.code == code_names.REQUEST_TIMEOUT
    assert [spec.path for spec in transport.specs] == [
        "/sessions",
        "/sessions/isolated-python-12345678123456781234567812345678",
    ]
    assert transport.specs[0].timeout == 2


@pytest.mark.anyio
async def test_process_run_timeout_zero_uses_the_unbounded_wait_wire_form():
    transport = RecordingTransport(
        [
            _process_payload("proc", "exec", "running"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([_log_frame_payload("1", "out")], next_cursor="1"),
            _log_page_payload([], next_cursor="1"),
        ]
    )
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    result = await process.run("echo hi", timeout=0)

    assert result.reason == "exited"
    assert result.stdout == "out"
    assert transport.specs[0] == RequestSpec(
        "POST",
        "/processes",
        params={"waitMs": 0, "logs": "text"},
        json_body={"kind": "exec", "shellCommand": "echo hi", "stdin": "closed", "keepLogs": "on_exit_ttl"},
        timeout=None,
        unbounded_read=True,
    )
    assert transport.specs[1] == RequestSpec(
        "GET", "/processes/proc/wait", params={"timeoutMs": 0}, unbounded_read=True
    )


@pytest.mark.anyio
async def test_daytona_create_list_fork_and_delete_are_offline_transport_calls():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "id": "sbx",
                "access_token": "tok",
                "status": "queued",
                "toolbox_proxy_url": "http://toolbox",
                "wait_cap_seconds": 0,
            },
            _sandbox_detail_payload("sbx", "running", toolbox_proxy_url="http://toolbox", cores_max=2, cpu=2),
            {
                "sandboxes": [
                    _sandbox_page_payload("sbx", "running", name="n"),
                    _sandbox_page_payload("other", "stopped", name="skip"),
                ],
                "next_cursor": "next",
                "total": 0,
                "total_relation": "eq",
            },
            {
                "forked_from": "sbx",
                "status": "pending",
                "toolbox_proxy_url": "http://toolbox",
                "id": "fork",
            },
            {},
            _sandbox_detail_payload("sbx", "deleted", toolbox_proxy_url="http://toolbox"),
            _snapshot_operation_payload("snap"),
            {
                "id": "snap",
                "class": "linux",
                "kind": "full",
                "name": "snap",
                "region_id": "us",
                "status": "ready",
                "region_ids": ["us"],
                "created_at": _DEFAULT_TIME,
                "updated_at": _DEFAULT_TIME,
            },
            {"id": "ep", "port": 8080, "public": False, "url": "https://p", "created_at": _DEFAULT_TIME},
            {"url": "https://p", "token": "preview-token"},
            {"id": "ep2", "port": 9000, "public": True, "url": "https://p2", "created_at": _DEFAULT_TIME},
        ]
    )
    bind_api(client, api)

    sandbox = await client.create(
        Image.base("python:3.12"),
        resources=Resources(cpu=1.5, memory=2, disk=3),
        ports=[PortMapping(port=8080)],
        volumes=[VolumeMount(volume_id="vol", mount_path="/mnt", subpath="sub")],
        secrets=["TOKEN"],
        region="us",
    )
    assert sandbox.id == "sbx"
    assert sandbox.cpu == 2.0
    assert sandbox.memory == 1
    assert sandbox.disk == 2
    assert api.specs[0].path == "/sandboxes"
    assert api.specs[0].headers is not None and "Idempotency-Key" in api.specs[0].headers
    assert api.specs[0].json_body == {
        "image": "python:3.12",
        "region_id": "us",
        "ports": [{"port": 8080, "proto": "tcp"}],
        "volumes": [{"volume": "vol", "mount_path": "/mnt", "subpath": "sub"}],
        "secrets": ["TOKEN"],
        "cpu": 1.5,
        "memory_mib": 2048,
        "disk_mib": 3072,
        "wait_seconds": 60,
    }
    page = await client.list(cursor="c", limit=10)
    assert [item.id for item in page.items] == ["sbx", "other"]
    assert page.next_cursor == "next"

    assert (await sandbox.fork(name="child", wait_seconds=0)).id == "fork"
    await sandbox.delete()
    assert sandbox.state == "destroyed"
    assert (await sandbox.create_snapshot("snap")).id == "snap"
    assert await sandbox.ports.preview_link(8080) == PreviewLink(url="https://p", token="preview-token")
    assert (await sandbox.ports.expose(9000, public=True)).url == "https://p2"
    assert [spec.path for spec in api.specs[3:8]] == [
        "/sandboxes/sbx/fork",
        "/sandboxes/sbx",
        "/sandboxes/sbx",
        "/sandboxes/sbx/snapshots",
        "/snapshots/snap",
    ]


@pytest.mark.anyio
async def test_daytona_create_wait_zero_returns_the_create_response_without_fetching() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "queued", "wait_cap_seconds": 0}])
    bind_api(client, api)

    sandbox = await client.create(Image.base("python:3.12"), wait_seconds=0)

    assert sandbox.id == "sbx"
    assert sandbox.state == "queued"
    assert api.specs == [
        RequestSpec(
            "POST",
            "/sandboxes",
            json_body={"image": "python:3.12", "wait_seconds": 0},
            headers=api.specs[0].headers,
        )
    ]


@pytest.mark.anyio
async def test_daytona_create_sets_raw_state_from_the_create_response() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 0}])
    bind_api(client, api)

    sandbox = await client.create(Image.base("python:3.12"), wait_seconds=0)

    assert sandbox.state == "started"
    assert sandbox.raw_state == "running"


@pytest.mark.anyio
async def test_daytona_create_wait_remainder_long_polls_and_returns_a_queued_sandbox(monkeypatch) -> None:
    """Spend the remaining wait budget in bounded polls, then return the current sandbox."""
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "sbx", "status": "queued", "wait_cap_seconds": 3},
            _sandbox_detail_payload("sbx", "queued"),
            _sandbox_detail_payload("sbx", "queued"),
        ]
    )
    bind_api(client, api)
    # Each held read "takes" its full hold; after two slices the 57 s remainder is spent.
    now = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: now[0])
    send = api.send

    async def holding_send(spec: RequestSpec) -> object:
        result = await send(spec)
        now[0] += int(spec.params.get("timeout_ms", 0)) / 1000  # type: ignore[call-overload]
        return result

    api.send = holding_send  # type: ignore[method-assign]

    sandbox = await client.create(Image.base("python:3.12"), wait_seconds=60)

    assert sandbox.id == "sbx"
    assert sandbox.state == "queued"
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes"),
        ("GET", "/sandboxes/sbx"),
        ("GET", "/sandboxes/sbx"),
    ]
    assert api.specs[0].json_body == {"image": "python:3.12", "wait_seconds": 60}
    assert api.specs[1].params == {"wait": "running", "timeout_ms": 30000}
    assert api.specs[2].params == {"wait": "running", "timeout_ms": 27000}
    assert api.specs[1].timeout == pytest.approx(30 + WAIT_READ_SLACK_SECONDS)


# Name-derived idempotency keys. The SAME vectors are pinned in
# sdk-typescript/tests/unit.test.ts ("idempotencyKeyForName parity
# vectors"): the two SDKs must derive byte-identical keys, or a Python and a
# TypeScript caller creating the same end user's sandbox would get two.
NAME_KEY_VECTORS: list[tuple[str, str]] = [
    ("example-user", "name:example-user"),
    ("Example 2 (test)", "name:Example 2 (test)"),
    ("a" * 251, "name:" + "a" * 251),
    # One over the 256-character key cap: hashed.
    ("a" * 252, "name:sha256:" + hashlib.sha256(("a" * 252).encode()).hexdigest()),
    # Non-ASCII: hashed over the UTF-8 bytes.
    ("météo", "name:sha256:54d1ed6448e2d6fe64cf3356d89435a041992b4f761a692d4f33fd8e8869ad74"),
    # Surrounding whitespace would be trimmed by the API / stripped by HTTP: hashed.
    (" example-user", "name:sha256:dc734153d169d526ca4e514011da05c95fa28ad25d9ba57483c612638e46a78c"),
    ("example-user\t", "name:sha256:" + hashlib.sha256(b"example-user\t").hexdigest()),
    # A control character is not printable ASCII: hashed.
    ("example\x01user", "name:sha256:" + hashlib.sha256(b"example\x01user").hexdigest()),
]


@pytest.mark.parametrize(("name", "expected"), NAME_KEY_VECTORS)
def test_idempotency_key_for_name_parity_vectors(name: str, expected: str) -> None:
    key = _idempotency_key_for_name(name)
    assert key == expected
    # Every derived key is one the API accepts verbatim.
    assert key == key.strip() and len(key) <= 256 and all(" " <= c <= "~" for c in key)


def test_idempotency_key_for_name_rejects_non_strings() -> None:
    with pytest.raises(BadRequestError):
        _idempotency_key_for_name(42)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_named_create_stamps_the_name_label_and_derives_the_key() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 3}])
    bind_api(client, api)

    await client.create(Image.base("py312"), name="example-user", labels={"team": "cli"})

    spec = api.specs[0]
    assert (spec.method, spec.path) == ("POST", "/sandboxes")
    assert spec.json_body["name"] == "example-user"
    assert spec.json_body["labels"] == {"team": "cli", "name": "example-user"}
    assert spec.headers["Idempotency-Key"] == "name:example-user"
    assert "idempotency_key" not in spec.json_body


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "args", "path"),
    [
        ("create_from_snapshot", ("snap",), "/sandboxes/from-snapshot"),
    ],
)
async def test_every_create_surface_stamps_the_name_label_and_derives_the_key(method, args, path) -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 3}])
    bind_api(client, api)

    await getattr(client, method)(*args, name="example-user")

    spec = api.specs[0]
    assert spec.path == path
    assert spec.json_body["labels"] == {"name": "example-user"}
    assert spec.headers["Idempotency-Key"] == "name:example-user"


@pytest.mark.anyio
async def test_explicit_idempotency_key_wins_over_the_name_and_a_blank_name_derives_nothing() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "sbx", "status": "running", "wait_cap_seconds": 3},
            {"id": "sbx2", "status": "running", "wait_cap_seconds": 3},
            {"id": "sbx3", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    await client.create(Image.base("py312"), name="example-user", idempotency_key="my-key")
    assert api.specs[0].headers["Idempotency-Key"] == "my-key"
    assert api.specs[0].json_body["labels"] == {"name": "example-user"}, "the label is stamped regardless"

    # A blank name is no name (the API generates one): no label, random key.
    await client.create(Image.base("py312"), name="   ")
    assert "labels" not in api.specs[1].json_body
    assert uuid.UUID(api.specs[1].headers["Idempotency-Key"])

    # No name at all: random key, as before.
    await client.create(Image.base("py312"))
    assert uuid.UUID(api.specs[2].headers["Idempotency-Key"])
    assert api.specs[2].headers["Idempotency-Key"] != api.specs[1].headers["Idempotency-Key"]


@pytest.mark.anyio
async def test_named_create_refuses_a_contradicting_name_label() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)

    with pytest.raises(BadRequestError, match="disagrees with name="):
        await client.create(Image.base("py312"), name="example-user", labels={"name": "other"})
    assert api.specs == [], "refused before any request"

    # The same spelling in both places is not a contradiction.
    api.responses.append({"id": "sbx", "status": "running", "wait_cap_seconds": 3})
    await client.create(Image.base("py312"), name="example-user", labels={"name": "example-user"})
    assert api.specs[0].json_body["labels"] == {"name": "example-user"}


@pytest.mark.anyio
async def test_find_by_name_uses_exact_name_search_and_returns_one_live_sandbox() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "sandboxes": [_sandbox_page_payload("sbx-new", "running", name="example-user")],
                "total": 1,
                "total_relation": "eq",
            },
        ]
    )
    bind_api(client, api)

    found = await client.find_by_name("example-user")

    assert [sandbox.id for sandbox in found] == ["sbx-new"]
    assert [(spec.method, spec.path) for spec in api.specs] == [("GET", "/sandboxes/search")]
    assert api.specs[0].params == {"labels": json.dumps({"name": "example-user"}), "limit": 100}
    # The default target is "local": the default region, no selector header.
    assert "x-daytona-region" not in api.specs[0].headers


@pytest.mark.anyio
async def test_find_by_name_uses_the_named_creates_region() -> None:
    # The same `x-daytona-region` selector create*() sends, so the lookup reads
    # the region selected for the named create.
    client = AsyncDaytona(_config_at("http://api.invalid", api_key="key"))
    api = RecordingTransport(
        [
            {"id": "sbx", "status": "running", "wait_cap_seconds": 3},
            {
                "sandboxes": [_sandbox_page_payload("sbx", "running", name="example-user", region="eu")],
                "total": 1,
                "total_relation": "eq",
            },
            {
                "sandboxes": [_sandbox_page_payload("sbx", "running", name="example-user", region="us")],
                "total": 1,
                "total_relation": "eq",
            },
            {
                "sandboxes": [_sandbox_page_payload("sbx", "running", name="example-user")],
                "total": 1,
                "total_relation": "eq",
            },
        ]
    )
    bind_api(client, api)

    await client.create(Image.base("py312"), name="example-user", region="eu")
    found = await client.find_by_name("example-user", region="eu")
    await client.find_by_name("example-user")
    await client.find_by_name("example-user", region="local")

    assert api.specs[0].headers["x-daytona-region"] == "eu"
    assert api.specs[1].headers["x-daytona-region"] == "eu", "the same selector create sends"
    assert found[0].region == "eu"
    assert "x-daytona-region" not in api.specs[2].headers, "no region = default region"
    assert "x-daytona-region" not in api.specs[3].headers, "local = default region"


@pytest.mark.anyio
async def test_find_by_name_returns_every_live_match_across_pages_and_empty_when_none() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"sandboxes": [], "total": 0, "total_relation": "eq"},
            {
                "sandboxes": [
                    _sandbox_page_payload("a", "running"),
                    _sandbox_page_payload("gone", "deleted"),
                ],
                "next_cursor": "c1",
                "total": 3,
                "total_relation": "eq",
            },
            {"sandboxes": [_sandbox_page_payload("b", "running")], "total": 3, "total_relation": "eq"},
        ]
    )
    bind_api(client, api)

    assert await client.find_by_name("nobody") == ()
    assert [sandbox.id for sandbox in await client.find_by_name("duplicate")] == ["a", "b"]
    with pytest.raises(BadRequestError):
        await client.find_by_name("  ")
    assert api.specs[2].params["cursor"] == "c1"
    assert len(api.specs) == 3


@pytest.mark.anyio
async def test_find_by_name_surfaces_a_deployment_without_search_as_search_not_configured() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [create_error("sandbox search is not configured", 501, "SEARCH_NOT_CONFIGURED", "DAYTONA_API")]
    )
    bind_api(client, api)

    with pytest.raises(SearchNotConfiguredError):
        await client.find_by_name("example-user")


def test_sync_find_by_name_wraps_handles() -> None:
    # The generated sync twin returns Sandbox handles, like list().
    assert "find_by_name" in Daytona.__dict__
    hints = Daytona.find_by_name.__annotations__
    assert "Sandbox" in str(hints.get("return"))


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["region-a", "region-b", "region-c", "region-d"])
async def test_create_preserves_region_headers_and_body_across_retries(target):
    client = AsyncDaytona(_config_at("http://api.invalid", api_key="key"))
    api = RecordingTransport(
        [
            ClientTimeoutError("lost response"),
            ConnectionError("socket closed"),
            {"id": "sbx-example", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    async def create(key=None):
        return await client.create(
            Image.base("python:3.12"), name="same-action", region=target, idempotency_key=key
        )

    with pytest.raises(SandboxCreateRequestTimeoutError) as uncertain:
        await create()
    key = uncertain.value.idempotency_key
    with pytest.raises(SandboxCreateConnectionError) as dropped:
        await create(key)
    assert dropped.value.idempotency_key == key
    assert dropped.value.source == "SDK"
    sandbox = await create(key)
    for spec in api.specs[:3]:
        assert spec.path == "/sandboxes"
        assert spec.json_body == api.specs[0].json_body
        assert spec.json_body["region_id"] == target
        assert spec.json_body["name"] == "same-action"
        assert spec.json_body["image"] == "python:3.12"
        assert spec.json_body["wait_seconds"] == 60
        assert spec.headers == {"Idempotency-Key": key, "x-daytona-region": target}
    assert sandbox.id == "sbx-example"
    assert sandbox.region == target


@pytest.mark.anyio
async def test_create_region_is_per_call_and_local_keeps_the_server_default():
    # No region in config: each create picks
    # its own, and "local" sends no selector so the server default applies.
    client = AsyncDaytona(_config_at("http://api.invalid", api_key="key"))
    api = RecordingTransport(
        [
            {"id": "first"},
            _sandbox_detail_payload("first", "running"),
            {"id": "second"},
            _sandbox_detail_payload("second", "running"),
        ]
    )
    bind_api(client, api)
    await client.create("python:3.12", region="region-a")
    await client.create("python:3.12", region="local")
    assert api.specs[0].json_body["region_id"] == "region-a"
    assert api.specs[0].headers["x-daytona-region"] == "region-a"
    assert "region_id" not in api.specs[2].json_body
    assert "x-daytona-region" not in api.specs[2].headers


@pytest.mark.anyio
async def test_daytona_create_running_response_returns_without_follow_up_get() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 3}])
    bind_api(client, api)

    sandbox = await client.create(Image.base("python:3.12"), wait_seconds=60)

    assert sandbox.id == "sbx"
    assert sandbox.state == "started"
    assert len(api.specs) == 1
    assert api.specs[0].path == "/sandboxes"


def test_interpreter_result_json_check_survives_deeply_nested_sandbox_output() -> None:
    from daytona.v2.interpreter import _is_json_value

    # A sandbox-controlled result nested deeper than the interpreter stack must
    # neither crash the caller nor be misclassified.
    depth = 50_000
    nested: object = []
    for _ in range(depth):
        nested = [nested]
    assert _is_json_value(nested) is True
    deep_dict: object = {"k": "v"}
    for _ in range(depth):
        deep_dict = {"k": deep_dict}
    assert _is_json_value(deep_dict) is True
    poisoned: object = {1: "non-string key"}
    for _ in range(depth):
        poisoned = [poisoned]
    assert _is_json_value(poisoned) is False
    assert _is_json_value({"ok": [1, 2.5, "s", None, True]}) is True
    assert _is_json_value({"bad": object()}) is False


@pytest.mark.anyio
async def test_process_ids_are_strictly_percent_encoded_in_every_request_path() -> None:
    # A process id / user-chosen name may contain reserved characters; every
    # path segment must be quote(safe='') so `/` `?` `#` cannot re-route the
    # request — byte-identical to the TS SDK's strictUriEncode.
    raw = "my/proc?x#y!'()*"
    encoded = "my%2Fproc%3Fx%23y%21%27%28%29%2A"
    transport = RecordingTransport(
        [
            _process_payload(raw, "exec", "running"),
            _process_payload(raw, "exec", "running"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([], next_cursor="0"),
            {},
            {},
            {},
        ]
    )
    service = AsyncProcessService("sbx", transport)
    await service.get(raw)
    handle = service.connect(raw)
    await handle.get()
    await handle.wait(timeout=1)
    await handle.logs()
    await handle.write_stdin("x")
    await handle.kill()
    await handle.delete()
    paths = [spec.path for spec in transport.specs]
    assert paths == [
        f"/processes/{encoded}",
        f"/processes/{encoded}",
        f"/processes/{encoded}/wait",
        f"/processes/{encoded}/logs",
        f"/processes/{encoded}/stdin",
        f"/processes/{encoded}/signals",
        f"/processes/{encoded}",
    ]
    assert all(raw not in path for path in paths)


@pytest.mark.anyio
async def test_wait_or_interrupt_treats_explicit_zero_as_unbounded_and_only_expires_on_request() -> None:
    from daytona.v2.process import wait_or_interrupt

    class Handle:
        def __init__(self) -> None:
            self.waits: list[Optional[float]] = []
            self.kills = 0

        async def wait(self, *, timeout: Optional[float] = None) -> WaitResult:
            self.waits.append(timeout)
            return WaitResult(reason="exited", exit_code=0)

        async def kill(self, **_: object) -> None:
            self.kills += 1

    handle = Handle()
    # timeout=0 is the documented unbounded wait: it must reach handle.wait, not
    # be short-circuited as an expired budget.
    assert (await wait_or_interrupt(handle, timeout=0)).exit_code == 0  # type: ignore[arg-type]
    assert handle.waits == [0]
    assert handle.kills == 0
    # A computed-zero remainder is signalled explicitly: the caller's zero is
    # never used as a wait budget; the process is interrupted (^C) and only the
    # bounded post-interrupt grace wait runs.
    expired = Handle()
    await wait_or_interrupt(expired, timeout=0.0, expired=True)  # type: ignore[arg-type]
    assert expired.kills == 1
    assert 0.0 not in expired.waits
    assert all(w is not None and w > 0 for w in expired.waits)


@pytest.mark.anyio
async def test_process_wait_bounds_the_transport_read_for_a_positive_budget_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Frozen clock: wait() reads the clock twice (deadline, then remainder), so
    # a real clock shaves the elapsed microseconds off the asserted bound.
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    transport = RecordingTransport([{"exitCode": 0, "reason": "exited"}, {"exitCode": 0, "reason": "exited"}])
    handle = AsyncProcessService("sbx", transport).connect("p")
    await handle.wait(timeout=10)
    await handle.wait(timeout=0)
    bounded, unbounded = transport.specs
    # A stalled response must not hold wait() past its budget plus read slack.
    assert bounded.params == {"timeoutMs": 10000}
    assert bounded.timeout == pytest.approx(10 + WAIT_READ_SLACK_SECONDS)
    assert bounded.unbounded_read is False
    # 0 = unbounded, on the wire AND on the read side.
    assert unbounded.params == {"timeoutMs": 0}
    assert unbounded.timeout is None
    assert unbounded.unbounded_read is True


@pytest.mark.anyio
async def test_process_start_validates_execution_timeout_for_session_backed_processes() -> None:
    process = AsyncProcessService("sbx", RecordingTransport([]))  # type: ignore[arg-type]

    with pytest.raises(DaytonaError, match="timeout must be a non-negative finite number"):
        await process.connect_session("sess").start("echo hi", execution_timeout=float("nan"))


def test_process_create_body_with_non_string_lifecycle_key_is_rejected_instead_of_dropped() -> None:
    with pytest.raises(DaytonaError, match=r"lifecycle_policy contains unknown fields: 1"):
        _create_body(
            {
                "route": "linux",
                "source_kind": "image",
                "source": "py",
                "lifecycle_policy": {1: 60},
            }
        )


@pytest.mark.anyio
async def test_isolated_interpreter_run_deletes_the_context_even_if_setup_times_out() -> None:
    transport = RecordingTransport(
        [
            ClientTimeoutError("timed out", status_code=408, code="CLIENT_TIMEOUT", source="SDK"),
            {},
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))

    with pytest.raises(ClientTimeoutError):
        await interpreter.run("print(1)", language="python", isolated=True)

    assert transport.specs[0].path == "/sessions"
    created_id = transport.specs[0].json_body["id"]
    assert transport.specs[1] == RequestSpec("DELETE", f"/sessions/{created_id}")


def test_process_create_spec_rejects_user_for_code_processes() -> None:
    with pytest.raises(UnsupportedOperationError, match="user is not supported for code processes"):
        process_create_spec(
            command=None,
            argv=None,
            code="print(1)",
            language="python",
            kind="code",
            name=None,
            session_id=None,
            cwd=None,
            env=None,
            user="root",
            shell=None,
            login=None,
            stdin="closed",
            execution_timeout=None,
            terminal=None,
            keep_logs=None,
        )


@pytest.mark.anyio
async def test_process_run_bounds_the_log_snapshot_when_the_process_timed_out() -> None:
    # A still-running process can out-write the paginator: on timed_out the
    # snapshot stops after TIMED_OUT_LOG_PAGE_LIMIT pages instead of chasing it.
    pages = [
        _log_page_payload([_log_frame_payload(str(i), f"p{i}")], next_cursor=str(i + 1)) for i in range(50)
    ]
    # The inline waitMs answer reports the process still running and the budget
    # is already spent by the time it returns (clock patched), so run() reports
    # timed_out without another wait and pages the logs.
    transport = RecordingTransport([_process_payload("proc", "exec", "running"), *pages])
    clock = iter([100.0] + [200.0] * 200)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(clock))
        result = await AsyncProcessService("sbx", transport).run("yes", timeout=5)  # type: ignore[arg-type]
    assert result.reason == "timed_out"
    log_calls = [spec for spec in transport.specs if spec.path == "/processes/proc/logs"]
    assert len(log_calls) == TIMED_OUT_LOG_PAGE_LIMIT
    assert result.stdout == "".join(f"p{i}" for i in range(TIMED_OUT_LOG_PAGE_LIMIT))


@pytest.mark.anyio
async def test_interpreter_timed_out_result_bounds_the_log_snapshot() -> None:
    pages = [
        _log_page_payload([_log_frame_payload(str(i), f"p{i}")], next_cursor=str(i + 1)) for i in range(50)
    ]
    transport = RecordingTransport([_process_payload("proc", "code", "running", language="python"), *pages])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as monkeypatch:

        async def timed_out_wait(*_args: object, **_kwargs: object) -> WaitResult:
            return WaitResult(reason="timed_out")

        monkeypatch.setattr("daytona.v2.interpreter.wait_or_interrupt", timed_out_wait)
        result = await interpreter.run("print(1)", language="python")

    assert result.process.reason == "timed_out"
    log_calls = [spec for spec in transport.specs if spec.path == "/processes/proc/logs"]
    assert len(log_calls) == TIMED_OUT_LOG_PAGE_LIMIT
    assert result.logs.stdout == "".join(f"p{i}" for i in range(TIMED_OUT_LOG_PAGE_LIMIT))


@pytest.mark.anyio
async def test_pty_delete_refuses_non_pty_processes() -> None:
    transport = RecordingTransport([_process_payload("p1", "exec", "running")])
    pty = AsyncPtyService(AsyncProcessService("sbx", transport))
    with pytest.raises(
        UnsupportedOperationError, match="process.delete\\(\\) is only supported for pty handles"
    ):
        await pty.delete("p1")
    # Only the metadata lookup was sent — no signal, no cleanup.
    assert [(spec.method, spec.path) for spec in transport.specs] == [("GET", "/processes/p1")]


@pytest.mark.anyio
async def test_daytona_create_timeout_omits_none_and_forwards_numeric_values() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "omit", "status": "running", "wait_cap_seconds": 3},
            {"id": "sentinel", "status": "running", "wait_cap_seconds": 3},
            {"id": "ttl", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    await client.create(Image.base("python:3.12"), create_timeout_seconds=None)
    await client.create(Image.base("python:3.12"), create_timeout_seconds=-1)
    await client.create(Image.base("python:3.12"), create_timeout_seconds=3600)

    assert "create_timeout_seconds" not in api.specs[0].json_body
    assert api.specs[1].json_body is not None and api.specs[1].json_body["create_timeout_seconds"] == -1
    assert api.specs[2].json_body is not None and api.specs[2].json_body["create_timeout_seconds"] == 3600


@pytest.mark.anyio
async def test_daytona_create_validates_image_source_class_rules_before_posting():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)

    for blank in ("", "   "):
        with pytest.raises(DaytonaError, match="image must be a non-empty string"):
            await client.create(blank)

    for blank in ("", "   "):
        with pytest.raises(DaytonaError, match="snapshot must be a non-empty string"):
            await client.create_from_snapshot(blank)

    with pytest.raises(BadRequestError, match="create_from_build"):
        await client.create(Image.from_dockerfile("FROM python:3.12"))
    with pytest.raises(BadRequestError, match=r"^create_macos\(\) accepts .*do not build one$"):
        await client.create_macos(Image.from_dockerfile("FROM python:3.12"))  # type: ignore[arg-type]
    with pytest.raises(
        BadRequestError, match="^ImageBuilder.from_dockerfile content must be a non-empty string$"
    ):
        ImageBuilder.from_dockerfile("   ")
    with pytest.raises(BadRequestError, match="^ImageBuilder.from_registry ref must be a non-empty string$"):
        ImageBuilder.from_registry("   ")

    assert api.specs == []


# Parity vector shared with sdk-typescript/tests/unit.test.ts
# ("ImageBuilder renders the parity vector byte-identically"): the Dockerfile
# text must render identically in both SDKs.
PARITY_DOCKERFILE = (
    "FROM python:3.12\nRUN pip install x\nRUN python -m pip install numpy 'pandas>=2.0' 'it'\"'\"'s'\n"
    'ENV APP_ENV="dev mode"\nWORKDIR /workspace\n'
)


def test_image_builder_renders_the_parity_vector_byte_identically() -> None:
    builder = (
        ImageBuilder.from_registry("python:3.12")
        .run_commands("pip install x")
        .pip_install("numpy", "pandas>=2.0", "it's")
        .env("APP_ENV", "dev mode")
        .workdir("/workspace")
    )
    _, dockerfile = builder._request_source()
    assert dockerfile == PARITY_DOCKERFILE
    # A trailing newline (or several) on inline content normalizes to ONE.
    _, inline = ImageBuilder.from_dockerfile("FROM python:3.12\n\n")._request_source()
    assert inline == "FROM python:3.12\n"


def test_image_builder_env_and_workdir_render_like_typescript() -> None:
    base = ImageBuilder.from_registry("python:3.12")
    builder = base.env("APP_ENV", "dev mode").env({"HOME": "/workspace"}).workdir("/workspace")

    source, dockerfile = base._request_source()
    assert source == {"type": "registry", "ref": "python:3.12"}
    assert dockerfile is None

    source, dockerfile = builder._request_source()
    assert source is None
    assert (
        dockerfile == 'FROM python:3.12\nENV APP_ENV="dev mode"\nENV HOME="/workspace"\nWORKDIR /workspace\n'
    )

    # Non-ASCII stays literal, like TypeScript's JSON.stringify: a \\uXXXX
    # escape is not something the Dockerfile parser unescapes, and the two
    # SDKs must render byte-identical Dockerfiles.
    _, unicode_dockerfile = base.env("GREETING", "héllo")._request_source()
    assert unicode_dockerfile == 'FROM python:3.12\nENV GREETING="héllo"\n'


def test_image_builder_env_and_workdir_validate_non_empty_text() -> None:
    builder = ImageBuilder.from_registry("python:3.12")

    with pytest.raises(Exception, match="ImageBuilder.env name must be a non-empty string"):
        builder.env("   ", "value")
    with pytest.raises(Exception, match="ImageBuilder.env value must be a non-empty string"):
        builder.env("APP_ENV", "   ")
    with pytest.raises(Exception, match="ImageBuilder.env key must be a non-empty string"):
        builder.env({"   ": "value"})
    with pytest.raises(Exception, match="ImageBuilder.env value must be a non-empty string"):
        builder.env({"APP_ENV": "   "})
    with pytest.raises(Exception, match="ImageBuilder.workdir path must be a non-empty string"):
        builder.workdir("   ")


@pytest.mark.anyio
async def test_snapshot_class_is_normalized_with_the_raw_wire_string_on_get_and_list() -> None:
    def payload(snapshot_class: str) -> dict[str, object]:
        return {
            "id": f"snap-{snapshot_class}",
            "cursor": f"cursor-{snapshot_class}",
            "class": snapshot_class,
            "kind": "full",
            "name": "after-setup",
            "region_id": "us",
            "status": "ready",
            "region_ids": ["us"],
            "created_at": _DEFAULT_TIME,
            "updated_at": _DEFAULT_TIME,
        }

    classes = ["linux", "macos", "future-class"]
    transport = RecordingTransport(
        [payload(c) for c in classes] + [{"snapshots": [payload(c) for c in classes]}]
    )
    snapshots = AsyncSnapshotsService(transport)  # type: ignore[arg-type]

    fetched = [await snapshots.get(f"snap-{c}") for c in classes]
    listed = (await snapshots.list()).items

    expected = [("linux", "linux"), ("unknown", "macos"), ("unknown", "future-class")]
    assert [(s.sandbox_class, s.raw_sandbox_class) for s in fetched] == expected
    assert [(s.sandbox_class, s.raw_sandbox_class) for s in listed] == expected


@pytest.mark.anyio
async def test_snapshot_service_get_and_delete_address_one_snapshot() -> None:
    payload = {
        "id": "snap/1",
        "class": "linux",
        "kind": "full",
        "name": "after-setup",
        "region_id": "us",
        "status": "ready",
        "region_ids": ["us"],
        "memory_bytes": 1073741824,
        "created_at": _DEFAULT_TIME,
        "updated_at": _DEFAULT_TIME,
    }
    transport = RecordingTransport([dict(payload), dict(payload), dict(payload), dict(payload), {}])
    snapshots = AsyncSnapshotsService(transport)  # type: ignore[arg-type]

    plain = await snapshots.get("snap/1")
    assert isinstance(plain, Snapshot)
    assert (plain.kind, plain.memory_bytes) == ("full", 1073741824)
    assert transport.specs[0].path == "/snapshots/snap%2F1"
    assert transport.specs[0].params == {}
    assert transport.specs[0].timeout is None

    # wait="ready" long-polls; the read allows the hold plus slack, never past
    # the wait's own deadline (here the whole 30 s budget).
    await snapshots.get("snap/1", wait="ready", timeout=30)
    assert transport.specs[1].params == {"wait": "ready", "timeout_ms": 30000}
    assert 29 < (transport.specs[1].timeout or 0) <= 30
    assert transport.specs[1].unbounded_read is False
    # A budget beyond the server's 60 s hold ceiling is capped on the wire,
    # however short the request timeout (30 s here): the wait budget governs.
    await snapshots.get("snap/1", wait="ready", timeout=10_000)
    assert transport.specs[2].params == {"wait": "ready", "timeout_ms": 60000}
    assert transport.specs[2].timeout == 65
    # No explicit budget: the server picks the hold, so the read is unbounded
    # rather than cut at the transport default.
    await snapshots.get("snap/1", wait="ready")
    assert transport.specs[-1].params == {"wait": "ready"}
    assert transport.specs[-1].timeout is None
    assert transport.specs[-1].unbounded_read is True

    await snapshots.delete("snap/1")
    assert (transport.specs[4].method, transport.specs[4].path) == ("DELETE", "/snapshots/snap%2F1")

    with pytest.raises(DaytonaError):
        await snapshots.get("x", wait="ready", timeout=-1)


@pytest.mark.anyio
async def test_image_and_snapshot_keep_the_update_timestamp_apart_from_the_create_one() -> None:
    created, updated = "2026-10-01T00:00:00Z", "2026-10-09T00:00:00Z"
    images = AsyncImagesService(
        RecordingTransport([{**_image_page_payload("img-1"), "created_at": created, "updated_at": updated}])
    )  # type: ignore[arg-type]
    snapshots = AsyncSnapshotsService(
        RecordingTransport(
            [
                {
                    "id": "snap-1",
                    "class": "linux",
                    "kind": "full",
                    "name": "snap-1",
                    "region_id": "us",
                    "status": "ready",
                    "region_ids": ["us"],
                    "created_at": created,
                    "updated_at": updated,
                }
            ]
        )
    )  # type: ignore[arg-type]

    image = await images.get("img-1")
    snapshot = await snapshots.get("snap-1")

    assert (image.created_at, image.updated_at) == (parse_timestamp(created), parse_timestamp(updated))
    assert (snapshot.created_at, snapshot.updated_at) == (parse_timestamp(created), parse_timestamp(updated))
    assert image.created_at.tzinfo is not None


@pytest.mark.anyio
async def test_interpreter_isolated_run_rejects_invalid_timeouts_before_any_request() -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))
    with pytest.raises(DaytonaError) as exc_info:
        await interpreter.run("print(1)", language="python", isolated=True, timeout=-1)
    assert exc_info.value.code == code_names.BAD_REQUEST
    assert transport.specs == []


@pytest.mark.anyio
async def test_daytona_create_posts_an_inline_dockerfile_source_on_linux():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    linux_api = RecordingTransport(
        [
            _image_operation_payload("img-built"),
            {**_image_page_payload("img-built"), "status": "pending"},
            {**_image_page_payload("img-built"), "status": "pending"},
            _image_page_payload("img-built"),
            {"id": "sbx", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, linux_api)

    assert (
        await client.create_from_build(
            Image.from_dockerfile("FROM python:3.12\nRUN pip install numpy"),
            volumes=[VolumeMount(volume_id="vol", mount_path="/mnt/data")],
        )
    ).id == "sbx"
    assert linux_api.specs[0].path == "/images"
    assert linux_api.specs[1].path == "/images/img-built"
    assert linux_api.specs[1].params == {}
    # No build_timeout: the build is awaited without a deadline (server-held long polls).
    assert linux_api.specs[2].path == "/images/img-built"
    assert linux_api.specs[2].params == {"wait": "ready"}
    assert linux_api.specs[3].path == "/images/img-built"
    assert linux_api.specs[3].params == {"wait": "ready"}
    assert linux_api.specs[4].path == "/sandboxes"
    assert linux_api.specs[4].json_body == {
        "image": "img-built",
        "volumes": [{"volume": "vol", "mount_path": "/mnt/data"}],
        "wait_seconds": 60,
    }


@pytest.mark.anyio
async def test_image_service_promote_patches_the_name_and_rereads() -> None:
    # Parity with TypeScript `images.promote`: PATCH /images/{id} {name}, then GET.
    transport = RecordingTransport(
        [{}, {**_image_page_payload("img-1"), "name": "keep-this", "anonymous": False}]
    )
    service = AsyncImagesService(transport)  # type: ignore[arg-type]

    image = await service.promote("img-1", "keep-this")

    assert [(spec.method, spec.path) for spec in transport.specs] == [
        ("PATCH", "/images/img-1"),
        ("GET", "/images/img-1"),
    ]
    assert transport.specs[0].json_body == {"name": "keep-this"}
    assert image.name == "keep-this"


@pytest.mark.anyio
async def test_sandbox_capture_image_returns_an_image() -> None:
    api = RecordingTransport(
        [
            {"id": "img-cap", "status": "pending"},
            {
                **_image_page_payload("img-cap"),
                "name": None,
                "anonymous": True,
                "origin": "capture",
                "created_at": "2026-01-01T00:00:00Z",
                "retention": "idle",
            },
        ]
    )
    sandbox = AsyncSandbox._from_api(_sandbox_detail_payload("sbx", "running"), api, None, access_token="key")  # type: ignore[arg-type]

    image = await sandbox.capture_image(idle_expiry_seconds=86400)

    from daytona.v2.types import Image as ImageHandle

    assert isinstance(image, ImageHandle)
    assert image.id == "img-cap"
    assert image.origin == "capture"
    assert image.anonymous is True
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes/sbx/images"),
        ("GET", "/images/img-cap"),
    ]
    assert api.specs[0].json_body == {"idle_expiry_seconds": 86400}


@pytest.mark.anyio
async def test_images_build_takes_only_an_image_builder() -> None:
    # Parity with TypeScript: the builder is the single declaration type.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)
    with pytest.raises(BadRequestError, match="ImageBuilder"):
        await client.images.build("FROM python:3.12", name="raw")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await client.images.build(name="kw-only", dockerfile="FROM python:3.12")  # type: ignore[call-arg]
    assert api.specs == []


@pytest.mark.anyio
async def test_create_from_build_raises_a_typed_error_when_the_image_build_fails():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    pending = {**_image_page_payload("img-bad"), "status": "pending"}
    failed = {**_image_page_payload("img-bad"), "status": "failed", "error": "RUN apt-get exited 100"}
    # POST /images, the build's first read (still pending), then the ready-wait
    # that ends in `failed`.
    api = RecordingTransport([_image_operation_payload("img-bad"), pending, failed])
    bind_api(client, api)

    with pytest.raises(ImageBuildFailedError) as info:
        await client.create_from_build(Image.from_dockerfile("FROM python:3.12\nRUN apt-get install x"))
    # The SDK-minted refusal names the failed image (so its build log can be
    # fetched) and carries the build's recorded error; no sandbox was created.
    assert info.value.image_id == "img-bad"
    assert info.value.build_error == "RUN apt-get exited 100"
    assert str(info.value) == "RUN apt-get exited 100"
    assert (info.value.status_code, info.value.code, info.value.source) == (409, "IMAGE_BUILD_FAILED", "SDK")
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/images"),
        ("GET", "/images/img-bad"),
        ("GET", "/images/img-bad"),
    ]
    assert api.specs[2].params["wait"] == "ready"


@pytest.mark.anyio
async def test_create_from_build_bounds_the_build_by_build_timeout_and_gives_the_create_its_full_wait(
    monkeypatch: pytest.MonkeyPatch,
):
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _image_operation_payload("img-wait"),
            {**_image_page_payload("img-wait"), "status": "pending"},
            _image_page_payload("img-wait"),
            {"id": "sbx", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)

    await client.create_from_build(Image.from_dockerfile("FROM python:3.12"), build_timeout=5, wait_seconds=7)

    assert api.specs[1].params == {}
    assert api.specs[2].params == {"wait": "ready", "timeout_ms": 5000}
    assert api.specs[3].json_body == {"image": "img-wait", "wait_seconds": 7}


@pytest.mark.anyio
async def test_create_from_build_raises_request_timeout_when_the_build_outlasts_build_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _image_operation_payload("img-slow"),
            *[{**_image_page_payload("img-slow"), "status": "building"} for _ in range(5)],
        ]
    )
    bind_api(client, api)
    ticks = iter(range(1000))
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: float(next(ticks)))

    with pytest.raises(RequestTimeoutError, match="image build did not finish before timeout") as raised:
        await client.create_from_build(Image.from_dockerfile("FROM python:3.12"), build_timeout=5)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    assert all(spec.path != "/sandboxes" for spec in api.specs), "no sandbox is created"


@pytest.mark.anyio
async def test_create_from_build_takes_typed_keywords_only():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = RecordingTransport([])  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        await client.create_from_build(Image.from_dockerfile("FROM x"), not_an_option=1)  # type: ignore[call-arg]
    with pytest.raises(BadRequestError, match="build_timeout"):
        await client.create_from_build(Image.from_dockerfile("FROM x"), build_timeout=-1)
    assert client._api.specs == []  # type: ignore[attr-defined]
    signature = inspect.signature(AsyncDaytona.create_from_build)
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    create_keywords = set(inspect.signature(AsyncDaytona.create).parameters) - {"self", "image"}
    assert create_keywords | {"idle_expiry_seconds", "build_timeout"} == (
        set(signature.parameters) - {"self", "builder"}
    )


@pytest.mark.anyio
async def test_create_from_build_passes_cpu_arch_to_the_image_build() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _image_operation_payload("img-arm"),
            _image_page_payload("img-arm"),
            {"id": "sbx-arm", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    await client.create_from_build(Image.from_dockerfile("FROM python:3.12"), cpu_arch="arm64")

    assert api.specs[0].json_body["cpu_arch"] == "arm64"


@pytest.mark.anyio
async def test_create_from_build_applies_target_to_both_the_image_build_and_the_sandbox():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _image_operation_payload("img-eu"),
            _image_page_payload("img-eu"),
            {"id": "sbx-eu", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    await client.create_from_build(
        Image.from_dockerfile("FROM python:3.12"), region="region-a", idle_expiry_seconds=3600
    )
    # A Dockerfile build is created and used in the same region: the
    # image is built where the sandbox will run, and the build-only knobs
    # never reach the create body.
    assert api.specs[0].path == "/images"
    assert api.specs[0].json_body["region_id"] == "region-a"
    assert api.specs[0].json_body["idle_expiry_seconds"] == 3600
    assert api.specs[2].path == "/sandboxes"
    assert api.specs[2].json_body["region_id"] == "region-a"
    assert "idle" not in api.specs[2].json_body
    # Region selection uses region=; target= is not a supported create keyword.
    with pytest.raises(TypeError, match="target"):
        await client.create_from_build(Image.from_dockerfile("FROM x"), target="region-a")


def test_unified_error_envelope_message_trace_and_extras_are_surfaced():
    from daytona.v2._core import parse_error_payload

    err = parse_error_payload(
        429,
        {
            "code": "RATE_LIMITED",
            "message": "retry this request later",
            "source": "DAYTONA_API",
            "trace_id": "0af7651916cd43dd8448eb211c80319c",
            "retry_after_seconds": 7,
            "feature": "example_feature",
            "entitlement": "example_feature",
            "replacement": "new-value",
            "firstAvailableCursor": "cursor-1",
        },
    )
    assert isinstance(err, RateLimitedError)
    assert str(err) == "retry this request later"
    assert err.trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert err.retry_after_seconds == 7
    assert err.feature == "example_feature"
    assert err.entitlement == "example_feature"
    assert err.replacement == "new-value"
    assert err.first_available_cursor == "cursor-1"


def test_source_and_code_dispatch_precedes_generic_and_status_fallback():
    from daytona.v2._core import parse_error_payload

    assert isinstance(
        parse_error_payload(402, {"code": "ORG_SUSPENDED", "message": "suspended", "source": "DAYTONA_API"}),
        OrgSuspendedError,
    )
    budget_err = parse_error_payload(
        402,
        {"code": "PROJECT_BUDGET_EXCEEDED", "message": "over budget", "source": "DAYTONA_API"},
    )
    assert isinstance(budget_err, ProjectBudgetExceededError)
    assert not isinstance(budget_err, OrgSuspendedError)
    assert budget_err.status_code == 402
    assert budget_err.code == "PROJECT_BUDGET_EXCEEDED"
    assert isinstance(
        parse_error_payload(
            501, {"code": "BILLING_DISABLED", "message": "disabled", "source": "DAYTONA_API"}
        ),
        BillingDisabledError,
    )
    assert isinstance(
        parse_error_payload(
            409, {"code": "SANDBOX_NOT_RUNNING", "message": "not running", "source": "DAYTONA_PROXY"}
        ),
        SandboxNotRunningError,
    )
    wrong_source = parse_error_payload(
        404, {"code": "PROCESS_NOT_FOUND", "message": "missing", "source": "DAYTONA_API"}
    )
    assert isinstance(wrong_source, NotFoundError)
    assert not isinstance(wrong_source, ProcessNotFoundError)
    assert isinstance(
        parse_error_payload(503, {"code": "SERVICE_UNAVAILABLE", "message": "down", "source": "DAYTONA_API"}),
        ServiceUnavailableError,
    )


def test_error_field_is_read_as_message_but_never_promoted_to_a_code():
    from daytona.v2._core import parse_error_payload

    err = parse_error_payload(400, {"error": "search is off"})
    assert err.code is None
    assert not isinstance(err, SearchNotConfiguredError)
    assert str(err) == "search is off"


def test_envelope_less_error_body_keeps_its_prose_and_dispatches_on_status():
    from daytona.v2._core import parse_error_payload

    err = parse_error_payload(404, {"error": "coupon not found"})
    assert str(err) == "coupon not found"
    assert err.code is None
    assert isinstance(err, NotFoundError)


def test_message_wins_over_the_error_field():
    from daytona.v2._core import parse_error_payload

    err = parse_error_payload(404, {"message": "no such coupon", "error": "ignored_error"})
    assert str(err) == "no such coupon"


@pytest.mark.anyio
async def test_daytona_create_posts_snapshot_source():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    linux_api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 3}])
    bind_api(client, linux_api)

    assert (await client.create_from_snapshot("snap")).id == "sbx"
    assert linux_api.specs[0].path == "/sandboxes/from-snapshot"
    assert linux_api.specs[0].json_body == {"snapshot": "snap", "wait_seconds": 60}


@pytest.mark.anyio
async def test_daytona_list_returns_one_page_and_iter_follows_cursors_explicitly():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "sandboxes": [_sandbox_page_payload("sbx-1", "running")],
                "next_cursor": "cursor-1",
                "total": 0,
                "total_relation": "eq",
            },
            {
                "sandboxes": [_sandbox_page_payload("sbx-2", "running")],
                "next_cursor": "cursor-2",
                "total": 0,
                "total_relation": "eq",
            },
            {
                "sandboxes": [_sandbox_page_payload("sbx-3", "running")],
                "next_cursor": "cursor-2",
                "total": 0,
                "total_relation": "eq",
            },
        ]
    )
    bind_api(client, api)

    page = await client.list()

    assert [item.id for item in page.items] == ["sbx-1"]
    assert page.next_cursor == "cursor-1"
    iterator_client = AsyncDaytona(DaytonaConfig(api_key="key"))
    bind_api(
        iterator_client,
        RecordingTransport(
            [
                {
                    "sandboxes": [_sandbox_page_payload("sbx-1", "running")],
                    "next_cursor": "cursor-1",
                    "total": 0,
                    "total_relation": "eq",
                },
                {
                    "sandboxes": [_sandbox_page_payload("sbx-2", "running")],
                    "next_cursor": "cursor-2",
                    "total": 0,
                    "total_relation": "eq",
                },
                {
                    "sandboxes": [_sandbox_page_payload("sbx-3", "running")],
                    "next_cursor": "cursor-2",
                    "total": 0,
                    "total_relation": "eq",
                },
            ]
        ),
    )
    assert [sandbox.id async for sandbox in iterator_client.iter()] == ["sbx-1", "sbx-2", "sbx-3"]


@pytest.mark.anyio
async def test_sandbox_start_preserves_resume_conflicts_without_starting():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "paused", sandbox_class="macos", persistent=True),
            create_error("conflicting request", 409, code_names.CONFLICT, "DAYTONA_API"),
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    with pytest.raises(DaytonaError) as exc:
        await sandbox.start(timeout=1)

    assert exc.value.code == code_names.CONFLICT

    assert [spec.path for spec in api.specs] == [
        "/sandboxes/sbx",
        "/sandboxes/sbx/resume",
    ]


@pytest.mark.anyio
async def test_sandbox_timeout_validation_rejects_negative_values_before_network_calls():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    with pytest.raises(Exception, match="timeout must be a non-negative finite number"):
        await sandbox._wait_for_state(["started"], timeout=-1)
    with pytest.raises(Exception, match="timeout must be a non-negative finite number"):
        await sandbox.start(timeout=-1)
    with pytest.raises(Exception, match="timeout must be a non-negative finite number"):
        await sandbox.resume(timeout=-1)
    with pytest.raises(Exception, match="timeout must be a non-negative finite number"):
        await sandbox._wait_for_state(["started"], timeout=float("nan"))
    with pytest.raises(Exception, match="timeout must be a non-negative finite number"):
        await sandbox.start(timeout=float("inf"))
    assert api.specs == []


@pytest.mark.anyio
async def test_sandbox_wait_for_state_reaches_target_via_api_long_poll_slices():
    client = AsyncDaytona(DaytonaConfig(api_key="key", request_timeout=5))
    api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "booting"),
            _sandbox_detail_payload("sbx", "running"),
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="booting")

    await sandbox._wait_for_state(["started"], timeout=60)

    assert api.specs[0].timeout == 35
    assert api.specs[0].params == {"wait": "running", "timeout_ms": 30000}
    assert api.specs[1].timeout == 35
    assert api.specs[1].params == {"wait": "running", "timeout_ms": 30000}


@pytest.mark.anyio
async def test_sandbox_wait_fills_in_the_handle_after_a_socket_decided_outcome() -> None:
    # A socket frame carries only the state; create/start/resume hand back
    # this handle, so the rest (toolbox URL, resources) must come from one
    # GET, as on the polling path. A poll-decided wait already did that GET.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "abc-region-a")
    refreshes = 0
    refresh_timeouts: list[object] = []

    async def counting_refresh(**kwargs: object) -> None:
        nonlocal refreshes
        refreshes += 1
        refresh_timeouts.append(kwargs.get("timeout"))

    sandbox._refresh_data_within = counting_refresh  # type: ignore[method-assign]

    class ScriptedHub:
        enabled = True
        mode = "socket"

        async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
            if self.mode == "poll":
                poll = kwargs["sandbox_poll_fn"]
                assert callable(poll)
                await poll()
                return WaitOutcome("started", "running")
            if self.mode == "terminal":
                raise SandboxTerminalStateError(sandbox_id, "error", raw_state="failed")
            if self.mode == "destroyed":
                return WaitOutcome("destroyed", "deleted")
            return WaitOutcome("started", "running")

    hub = ScriptedHub()
    sandbox._hub = hub  # type: ignore[assignment]

    await sandbox._wait_for_state(["started"], timeout=60)
    assert refreshes == 1
    timeout_used = refresh_timeouts[-1]
    assert isinstance(timeout_used, float) and 0 < timeout_used <= 60, "the refresh shares the wait's budget"

    hub.mode = "destroyed"
    refreshes = 0
    await sandbox._wait_for_state(["destroyed"], timeout=60)
    assert refreshes == 0, "a destroyed sandbox has nothing to fill in (the GET would 404)"

    hub.mode = "poll"
    refreshes = 0
    await sandbox._wait_for_state(["started"], timeout=60)
    assert refreshes == 1, "the poll's own GET is enough"

    hub.mode = "terminal"
    refreshes = 0
    with pytest.raises(SandboxTerminalStateError):
        await sandbox._wait_for_state(["started"], timeout=60)
    assert refreshes == 1, "a terminal outcome fills in the error reason too"


@pytest.mark.anyio
async def test_sandbox_wait_bounds_the_terminal_refresh_and_skips_it_for_destroyed() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "abc-region-a")
    calls = 0

    async def hanging_refresh(**kwargs: object) -> None:
        # Hangs unless it carries a timeout, like a stalled API.
        nonlocal calls
        calls += 1
        timeout = kwargs.get("timeout")
        if not isinstance(timeout, float):
            await asyncio.sleep(3600)
            return
        await asyncio.sleep(timeout)
        raise create_error("timed out", 408, "REQUEST_TIMEOUT", "SDK")

    sandbox._refresh_data_within = hanging_refresh  # type: ignore[method-assign]

    class TerminalHub:
        enabled = True
        state: str = "error"

        async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
            raise SandboxTerminalStateError(sandbox_id, self.state)  # type: ignore[arg-type]

    hub = TerminalHub()
    sandbox._hub = hub  # type: ignore[assignment]

    started = time.monotonic()
    with pytest.raises(SandboxTerminalStateError):
        await asyncio.wait_for(sandbox._wait_for_state(["started"], timeout=0.2), timeout=5)
    assert time.monotonic() - started < 2
    assert calls == 1

    hub.state = "destroyed"
    calls = 0
    with pytest.raises(SandboxTerminalStateError):
        await sandbox._wait_for_state(["started"], timeout=0.2)
    assert calls == 0, "a destroyed sandbox has nothing to fill in (the GET would 404)"


@pytest.mark.anyio
async def test_sandbox_wait_for_state_normalizes_local_target_to_no_region_hint() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "550e8400-e29b-41d4-a716-446655440000")
    sandbox.target = "local"

    class RecordingHub:
        enabled = True

        def __init__(self) -> None:
            self.region_hint: str | None = "sentinel"

        async def wait(
            self,
            sandbox_id: str,
            predicate: object,
            *,
            timeout: float | None = None,
            region_hint: str | None = None,
            sandbox_poll_fn: object = None,
            on_observed: object = None,
        ):
            assert sandbox_id == "550e8400-e29b-41d4-a716-446655440000"
            self.region_hint = region_hint

            class Outcome:
                state = "started"
                raw_state = "running"

            return Outcome()

    hub = RecordingHub()
    sandbox._hub = hub  # type: ignore[assignment]

    async def _no_refresh(**_: object) -> None:
        return None

    sandbox._refresh_data_within = _no_refresh  # type: ignore[method-assign]

    await sandbox._wait_for_state(["started"], timeout=60)

    assert hub.region_hint is None


def test_sandbox_wait_for_state_exports_narrowed_wait_literals() -> None:
    states = typing.get_args(SandboxWaitState)

    assert "creating" not in states
    assert "error" not in states
    assert "unknown" not in states
    assert "started" in states


@pytest.mark.parametrize(
    ("wire_state", "expected_state"),
    # ``deleting`` is terminal-bound: a sandbox on its way out can never reach
    # a wait target, so the wait fails fast instead of re-polling to timeout.
    [("failed", "error"), ("deleted", "destroyed"), ("deleting", "destroying")],
)
@pytest.mark.anyio
async def test_sandbox_wait_for_state_raises_with_the_observed_default_terminal_state(
    wire_state: str,
    expected_state: str,
) -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", wire_state)])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="starting")

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox._wait_for_state(["started"])

    # Typed and importable from the root so callers can catch it and read .state.
    from daytona.v2 import ConflictError
    from daytona.v2 import SandboxTerminalStateError as root_error

    assert root_error is SandboxTerminalStateError
    assert isinstance(exc_info.value, ConflictError)
    assert exc_info.value.state == expected_state
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == code_names.CONFLICT
    assert exc_info.value.source == "SDK"


@pytest.mark.anyio
async def test_sandbox_wait_for_state_polls_plainly_when_several_targets_are_given(monkeypatch) -> None:

    # Two targets: a long-poll on the first would sit out its slice while the
    # second was already reached — so no ``wait``/``timeout_ms`` goes on the wire
    # and a later target returns as soon as it is observed.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [_sandbox_detail_payload("sbx", "booting"), _sandbox_detail_payload("sbx", "stopped")]
    )
    bind_api(client, api)
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _no_sleep)
    sandbox = lazy_sandbox(client, "sbx")
    await sandbox._wait_for_state(["started", "stopped"], timeout=10)
    assert sandbox.state == "stopped"
    assert [spec.params for spec in api.specs] == [{}, {}]


@pytest.mark.anyio
async def test_sandbox_wait_for_state_never_trusts_a_stale_cached_state(monkeypatch) -> None:

    # Another client paused the sandbox while this handle still caches "started":
    # resume() must not return on the cache — the first iteration refreshes.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {},
            _sandbox_detail_payload("sbx", "resuming", sandbox_class="macos", persistent=True),
            _sandbox_detail_payload("sbx", "running", sandbox_class="macos", persistent=True),
        ]
    )
    bind_api(client, api)
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _no_sleep)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="started")  # stale
    sandbox._update(sandbox_class="macos")
    sandbox._update(persistent=True)
    await sandbox.resume(timeout=10)
    assert [spec.method for spec in api.specs] == ["POST", "GET", "GET"]
    assert sandbox.state == "started"


@pytest.mark.anyio
async def test_sandbox_wait_for_destroyed_treats_destroying_as_progress(monkeypatch) -> None:

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [_sandbox_detail_payload("sbx", "deleting"), _sandbox_detail_payload("sbx", "deleted")]
    )
    bind_api(client, api)
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _no_sleep)
    sandbox = lazy_sandbox(client, "sbx")
    # delete → wait for destroyed: the intermediate "destroying" must not raise.
    await sandbox._wait_for_state(["destroyed"], timeout=10)
    assert sandbox.state == "destroyed"
    # ...while waiting for anything else, a sandbox on its way out fails fast.
    fast = RecordingTransport([_sandbox_detail_payload("sbx2", "deleting")])
    bind_api(client, fast)
    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await lazy_sandbox(client, "sbx2")._wait_for_state(["started"], timeout=10)
    assert exc_info.value.state == "destroying"


# The wait engine's long polls and terminal errors (carried from main into
# this engine): a wait for `destroyed` alone holds `wait_exact=deleted`, and a
# terminal error carries the read's wire status and the server's reason.
class _RejectingHub:
    enabled = True

    async def wait(self, *args: object, **kwargs: object) -> object:
        raise SandboxTerminalStateError("sbx", "error", raw_state="failed")


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["wait", "delete"])
async def test_sandbox_wait_for_destroyed_holds_an_exact_long_poll_and_names_a_failed_deletion(
    monkeypatch, call: str
) -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    failed = _sandbox_detail_payload("sbx", "failed")
    failed["error"] = "sandbox deletion did not finish"
    api = RecordingTransport(
        ([{}] if call == "delete" else []) + [_sandbox_detail_payload("sbx", "deleting"), failed]
    )
    bind_api(client, api)
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _no_sleep)
    sandbox = lazy_sandbox(client, "sbx")

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        if call == "wait":
            await sandbox.wait(until="destroyed", timeout=10)
        else:
            await sandbox.delete(timeout=10)

    reads = [spec for spec in api.specs if spec.method == "GET"]
    # `wait=deleted` is already met by `deleting`, so every poll would answer at
    # once while the deletion finishes; the exact form holds it.
    assert [spec.params.get("wait_exact") for spec in reads] == ["deleted", "deleted"]
    assert all("wait" not in spec.params for spec in reads)
    assert exc_info.value.state == "error"
    assert exc_info.value.raw_state == "failed"
    assert exc_info.value.reason == "sandbox deletion did not finish"
    assert str(exc_info.value) == "sandbox sbx reached terminal state error: sandbox deletion did not finish"
    assert sandbox.error == "sandbox deletion did not finish"


@pytest.mark.anyio
async def test_sandbox_wait_keeps_other_targets_on_the_loose_long_poll() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", "running")])
    bind_api(client, api)
    await lazy_sandbox(client, "sbx").wait(until="started", timeout=10)
    assert api.specs[0].params.get("wait") == "running"
    assert "wait_exact" not in api.specs[0].params


@pytest.mark.anyio
async def test_sandbox_wait_names_the_server_reason_after_a_hub_rejection() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    failed = _sandbox_detail_payload("sbx", "failed")
    failed["error"] = "sandbox deletion did not finish"
    api = RecordingTransport([failed])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._hub = _RejectingHub()  # type: ignore[assignment]

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox.wait(until="destroyed", timeout=10)

    assert exc_info.value.state == "error"
    assert exc_info.value.raw_state == "failed"
    assert exc_info.value.reason == "sandbox deletion did not finish"
    assert str(exc_info.value).endswith(": sandbox deletion did not finish")


@pytest.mark.anyio
async def test_sandbox_wait_raises_the_state_the_confirming_read_found_with_its_reason() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    # A late finish turned the row deleted between the frame and the read: the
    # read decides, so the error is the read's state with the read's reason.
    moved_on = _sandbox_detail_payload("sbx", "deleted")
    moved_on["error"] = "stale"
    api = RecordingTransport([moved_on])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._hub = _RejectingHub()  # type: ignore[assignment]

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox.wait(until="started", timeout=10)

    assert (exc_info.value.state, exc_info.value.raw_state, exc_info.value.reason) == (
        "destroyed",
        "deleted",
        "stale",
    )
    assert str(exc_info.value) == "sandbox sbx reached terminal state destroyed: stale"


@pytest.mark.anyio
async def test_sandbox_wait_for_destroyed_keeps_the_loose_long_poll_for_another_terminal_state() -> None:
    # The exact form answers only on deleted, failed and lost: a wait that also
    # rejects on stopped must keep the loose form to see stopped at once.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", "stopped")])
    bind_api(client, api)
    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await lazy_sandbox(client, "sbx")._wait_for_state(["destroyed"], ["stopped", "error"], timeout=10)
    assert (exc_info.value.state, exc_info.value.raw_state) == ("stopped", "stopped")
    assert api.specs[0].params.get("wait") == "deleted"
    assert "wait_exact" not in api.specs[0].params


@pytest.mark.anyio
async def test_sandbox_read_without_exact_keeps_the_loose_wait() -> None:
    # Only a wait for `destroyed` alone holds the exact form; a plain long-poll
    # read for `destroyed` keeps `wait`.
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", "deleting")])
    bind_api(client, api)
    await lazy_sandbox(client, "sbx")._refresh_data_within(wait="destroyed", wait_timeout_ms=1000)
    assert api.specs[0].params.get("wait") == "deleted"
    assert "wait_exact" not in api.specs[0].params


@pytest.mark.anyio
async def test_sandbox_wait_never_labels_a_failed_report_with_a_lost_reason() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    # `failed` and `lost` both read as public `error`: the read decides, so the
    # error is the read's `lost`, with lost's explanation, never `failed` with it.
    lost = _sandbox_detail_payload("sbx", "lost")
    lost["error"] = "the host stopped responding"
    api = RecordingTransport([lost])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._hub = _RejectingHub()  # type: ignore[assignment]

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox.wait(until="destroyed", timeout=10)

    assert (exc_info.value.state, exc_info.value.raw_state) == ("error", "lost")
    assert exc_info.value.reason == "the host stopped responding"


@pytest.mark.anyio
async def test_sandbox_wait_never_attaches_a_reason_it_did_not_just_read() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    # The follow-up read fails; the handle still holds an explanation from an
    # earlier read, which says nothing about this outcome.
    earlier = _sandbox_detail_payload("sbx", "failed")
    earlier["error"] = "an earlier start failure"
    api = RecordingTransport(
        [earlier, create_error("unavailable", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API")]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    await sandbox.refresh_data()
    assert sandbox.error == "an earlier start failure"
    sandbox._hub = _RejectingHub()  # type: ignore[assignment]

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox.wait(until="destroyed", timeout=10)

    assert len(api.specs) == 2
    assert exc_info.value.reason is None
    assert str(exc_info.value) == "sandbox sbx reached terminal state error"


def test_sandbox_terminal_state_error_without_a_reason_keeps_its_message() -> None:
    error = SandboxTerminalStateError("sbx", "error", reason="")
    assert str(error) == "sandbox sbx reached terminal state error"
    assert error.reason is None


@pytest.mark.anyio
async def test_sandbox_wait_for_state_updates_the_cached_terminal_state_before_raising() -> None:
    class _RejectingHub:
        enabled = True

        async def wait(self, *args: object, **kwargs: object) -> object:
            raise SandboxTerminalStateError("sbx", "destroying", raw_state="deleting")

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="started")
    sandbox._update(raw_state="running")
    sandbox._hub = _RejectingHub()  # type: ignore[assignment]

    with pytest.raises(SandboxTerminalStateError) as exc_info:
        await sandbox._wait_for_state(["paused"], timeout=10)

    assert exc_info.value.state == "destroying"
    assert sandbox.state == "destroying"
    assert sandbox.raw_state == "deleting"


@pytest.mark.anyio
async def test_sandbox_wait_for_state_rejects_an_empty_target_list_before_polling() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)
    with pytest.raises(DaytonaError) as exc_info:
        await lazy_sandbox(client, "sbx")._wait_for_state([], timeout=1)
    assert (exc_info.value.code, exc_info.value.source) == (code_names.BAD_REQUEST, "SDK")
    assert api.specs == []


@pytest.mark.anyio
async def test_isolated_interpreter_setup_posts_are_unbounded_for_timeout_zero() -> None:
    transport = RecordingTransport(
        [
            _session_payload("isolated-python-12345678123456781234567812345678", kernel="python"),
            _process_payload("proc", "code", "running"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([], next_cursor="0"),
            {},
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "daytona.v2.interpreter.uuid.uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678")
        )
        await interpreter.run("print(1)", language="python", isolated=True, timeout=0)
    sessions_post, processes_post = transport.specs[0], transport.specs[1]
    assert (sessions_post.path, sessions_post.timeout, sessions_post.unbounded_read) == (
        "/sessions",
        None,
        True,
    )
    assert processes_post.path == "/processes"
    assert (processes_post.timeout, processes_post.unbounded_read) == (None, True)


@pytest.mark.anyio
async def test_computer_use_drag_button_is_optional_and_omitted_when_unset() -> None:
    transport = RecordingTransport([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
    mouse = AsyncComputerUseService(transport).mouse  # type: ignore[arg-type]
    await mouse.drag(start_x=0, start_y=0, end_x=1, end_y=2)
    await mouse.drag(start_x=0, start_y=0, end_x=3, end_y=4, button="right")
    assert transport.specs[0].json_body == {"startX": 0, "startY": 0, "endX": 1, "endY": 2}
    assert transport.specs[1].json_body == {"startX": 0, "startY": 0, "endX": 3, "endY": 4, "button": "right"}


@pytest.mark.anyio
async def test_sandbox_wait_for_state_backs_off_when_the_server_answers_early(monkeypatch) -> None:
    from daytona.v2 import sandbox as sandbox_module

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            _sandbox_detail_payload("sbx", "booting"),
            _sandbox_detail_payload("sbx", "booting"),
            _sandbox_detail_payload("sbx", "running"),
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="starting")
    pauses: list[float] = []
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _record_sleep(pauses))
    # A frozen SDK clock makes each instant answer take zero time, so the pause is
    # exactly the floor; a wall clock let a slow CI machine shrink it past the tolerance.
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 1000.0)

    await sandbox._wait_for_state(["started"], timeout=10)

    # Every refresh still asks the API to long-poll for the target...
    assert all(spec.params.get("wait") == "running" for spec in api.specs)
    # ...but two instant answers must be separated by the back-off floor rather
    # than issued back-to-back.
    assert len(api.specs) == 3
    assert len(pauses) == 2
    assert all(abs(p - sandbox_module.WAIT_FOR_STATE_MIN_INTERVAL_S) < 0.05 for p in pauses)


@pytest.mark.anyio
async def test_sandbox_wait_for_state_times_out_with_sdk_origin() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", "booting")])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="starting")

    with pytest.raises(DaytonaError) as exc_info:
        await sandbox._wait_for_state(["started"], timeout=0.01)

    assert exc_info.value.status_code == 408
    assert exc_info.value.code == code_names.REQUEST_TIMEOUT
    assert exc_info.value.source == "SDK"


@pytest.mark.anyio
async def test_sandbox_wait_for_state_updates_the_cached_state_before_rethrowing_timeout() -> None:
    class _TimingOutHub:
        enabled = True

        async def wait(self, *args: object, **kwargs: object) -> object:
            observe = kwargs.get("on_observed")
            assert callable(observe)
            observe(WaitOutcome("stopped", "stopped"))
            raise RequestTimeoutError(
                "sandbox failed to reach the requested state before timeout",
                status_code=408,
                code="REQUEST_TIMEOUT",
                source="SDK",
            )

    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="started")
    sandbox._update(raw_state="running")
    sandbox._hub = _TimingOutHub()  # type: ignore[assignment]

    with pytest.raises(RequestTimeoutError):
        await sandbox._wait_for_state(["paused"], timeout=10)

    assert sandbox.state == "stopped"
    assert sandbox.raw_state == "stopped"


def test_sandbox_wait_defaults_timeout_to_sixty_seconds() -> None:
    assert inspect.signature(AsyncSandbox.wait).parameters["timeout"].default == 60


@pytest.mark.anyio
async def test_sandbox_wait_raises_request_timeout_error_on_timeout() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([_sandbox_detail_payload("sbx", "booting")])
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(state="starting")

    with pytest.raises(RequestTimeoutError) as exc_info:
        await sandbox.wait(timeout=0.01)

    assert exc_info.value.status_code == 408
    assert exc_info.value.code == code_names.REQUEST_TIMEOUT
    assert exc_info.value.source == "SDK"


@pytest.mark.anyio
async def test_sandbox_delete_sets_destroyed_only_after_follow_up_404():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {},
            create_error("missing", 404, code_names.NOT_FOUND, "DAYTONA_API"),
        ]
    )
    bind_api(client, api)
    sandbox = lazy_sandbox(client, "sbx")

    await sandbox.delete()

    assert sandbox.state == "destroyed"
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx", "/sandboxes/sbx"]


@pytest.mark.anyio
async def test_process_wait_reissues_long_poll_without_client_deadline():
    transport = RecordingTransport(
        [
            {"reason": "timed_out"},
            _process_payload("proc", "exec", "running"),
            {"reason": "exited", "exitCode": 0},
        ]
    )
    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]
    assert (await handle.wait()).exit_code == 0
    # An expired long-poll (the record is still running) is re-issued.
    assert [spec.path for spec in transport.specs] == [
        "/processes/proc/wait",
        "/processes/proc",
        "/processes/proc/wait",
    ]


@pytest.mark.anyio
async def test_process_wait_retries_a_transient_record_read_failure():
    transport = RecordingTransport(
        [
            {"reason": "timed_out"},
            ConnectionError("reset"),
            {"reason": "timed_out"},
            _process_payload("proc", "exec", "terminal", reason="timed_out"),
        ]
    )
    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]
    assert (await handle.wait()).reason == "timed_out"
    assert len(transport.specs) == 4


@pytest.mark.anyio
async def test_process_wait_raises_after_repeated_record_read_failures():
    # The long-poll succeeding does not reset the streak: the unit is the
    # long-poll plus the record read.
    responses: list[object] = []
    from daytona.v2.process import WAIT_TRANSPORT_RETRY_LIMIT

    for _ in range(WAIT_TRANSPORT_RETRY_LIMIT + 1):
        responses += [{"reason": "timed_out"}, ConnectionError("reset")]
    transport = RecordingTransport(responses)
    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]
    with pytest.raises(ConnectionError):
        await handle.wait()
    assert len(transport.specs) == 2 * (WAIT_TRANSPORT_RETRY_LIMIT + 1)


@pytest.mark.anyio
async def test_process_wait_returns_a_command_its_own_deadline_stopped():
    # `/wait` answers `timed_out` for a settled timed-out command too: the
    # terminal record ends the wait instead of re-polling forever.
    transport = RecordingTransport(
        [
            {"reason": "timed_out"},
            _process_payload("proc", "exec", "terminal", reason="timed_out"),
        ]
    )
    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]
    assert await handle.wait() == WaitResult(reason="timed_out", raw_reason="timed_out")
    assert [spec.path for spec in transport.specs] == ["/processes/proc/wait", "/processes/proc"]


@pytest.mark.anyio
async def test_process_run_settles_in_one_request_when_response_is_inline():
    transport = RecordingTransport(
        [
            {
                **_process_payload("proc", "exec", "terminal"),
                "reason": "exited",
                "exitCode": 0,
                "logs": _log_page_payload([_log_frame_payload("c1", "ok")], next_cursor="c1", eof=True),
            }
        ]
    )
    result = await AsyncProcessService("sbx", transport).run("echo ok", timeout=1.234)  # type: ignore[arg-type]
    assert [(spec.method, spec.path) for spec in transport.specs] == [("POST", "/processes")]
    assert transport.specs[0].params == {"waitMs": 1234, "logs": "text"}
    # The HTTP read outlives the requested hold (budget + slack) but stays bounded.
    assert transport.specs[0].timeout == pytest.approx(1.234 + WAIT_READ_SLACK_SECONDS)
    assert transport.specs[0].unbounded_read is False
    assert (result.exit_code, result.reason, result.stdout) == (0, "exited", "ok")


def test_process_create_spec_rejects_terminal_geometry_for_non_pty_processes():
    with pytest.raises(
        UnsupportedOperationError, match="terminal cols/rows are only supported for pty processes"
    ):
        process_create_spec(
            "echo hi",
            argv=None,
            code=None,
            language=None,
            kind="exec",
            name=None,
            session_id=None,
            cwd=None,
            env=None,
            user=None,
            shell=None,
            login=None,
            stdin="closed",
            execution_timeout=None,
            terminal=TerminalSize(cols=80, rows=24),
            keep_logs=None,
        )


@pytest.mark.anyio
async def test_process_run_falls_back_to_split_calls_when_the_inline_page_is_incomplete():
    transport = RecordingTransport(
        [
            {
                **_process_payload("proc", "exec", "terminal"),
                "reason": "exited",
                "exitCode": 0,
                "logs": _log_page_payload([_log_frame_payload("c1", "partial")], next_cursor="c1", eof=False),
            },
            _log_page_payload([_log_frame_payload("c1", "whole ")], next_cursor="c1"),
            _log_page_payload([_log_frame_payload("c2", "output")], next_cursor="c2"),
            _log_page_payload([], next_cursor="c2"),
        ]
    )
    result = await AsyncProcessService("sbx", transport).run("echo whole output")  # type: ignore[arg-type]
    assert result.output == "whole output"
    assert [spec.path for spec in transport.specs][1:] == ["/processes/proc/logs"] * 3


@pytest.mark.anyio
async def test_process_run_falls_back_to_split_wait_when_the_wait_window_elapses():
    transport = RecordingTransport(
        [
            _process_payload("proc", "exec", "running"),
            {"exitCode": 0, "reason": "exited"},
            _log_page_payload([_log_frame_payload("c1", "late")], next_cursor="c1"),
            _log_page_payload([], next_cursor="c1"),
        ]
    )
    result = await AsyncProcessService("sbx", transport).run("sleep 1", timeout=5.0)  # type: ignore[arg-type]
    paths = [spec.path for spec in transport.specs]
    assert paths == ["/processes", "/processes/proc/wait", "/processes/proc/logs", "/processes/proc/logs"]
    # The POST already spent part of the budget, so the wait must ask for less.
    waited = transport.specs[1].params or {}
    assert 0 < int(waited["timeoutMs"]) <= 5000
    assert (result.exit_code, result.reason, result.stdout) == (0, "exited", "late")


@pytest.mark.anyio
async def test_process_list_returns_sdk_list_result_shape_with_cursor():
    transport = RecordingTransport(
        [{"items": [_process_payload("proc", "exec", "running")], "nextCursor": "n"}]
    )
    page = await AsyncProcessService("sbx", transport).list()  # type: ignore[arg-type]
    assert [item.id for item in page.items] == ["proc"]
    assert page.next_cursor == "n"


@pytest.mark.anyio
async def test_pty_service_lists_and_deletes_via_process_handles() -> None:
    transport = RecordingTransport(
        [
            {"items": [_process_payload("pty-1", "pty", "running")], "nextCursor": "p"},
            _process_payload("pty-1", "pty", "running"),
            {},
            {},
        ]
    )
    pty = AsyncPtyService(AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    page = await pty.list()
    await pty.delete("pty-1")

    assert [item.id for item in page.items] == ["pty-1"]
    assert transport.specs[0] == RequestSpec("GET", "/processes", params={"kind": "pty"})
    # delete() verifies the handle is a PTY before signalling it.
    assert transport.specs[1] == RequestSpec("GET", "/processes/pty-1")
    assert transport.specs[2] == RequestSpec(
        "POST",
        "/processes/pty-1/signals",
        json_body={"signal": "SIGTERM"},
    )
    assert transport.specs[3] == RequestSpec("DELETE", "/processes/pty-1")


@pytest.mark.anyio
async def test_process_wait_timeout_zero_sends_unbounded_poll_and_waits_for_terminal() -> None:
    class DelayedTransport(RecordingTransport):
        async def send(self, spec):
            await asyncio.sleep(0.001)
            return await super().send(spec)

    transport = DelayedTransport([{"reason": "exited", "exitCode": 0}])

    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]

    assert await handle.wait(timeout=0) == WaitResult(reason="exited", exit_code=0, raw_reason="exited")
    assert transport.specs[0].params == {"timeoutMs": 0}


@pytest.mark.anyio
async def test_process_stdin_rejects_known_code_handles_without_a_wire_call() -> None:
    transport = RecordingTransport([])
    handle = AsyncProcessHandle("sbx", "proc", transport, kind="code")  # type: ignore[arg-type]

    with pytest.raises(UnsupportedOperationError) as stdin_exc:
        await handle.write_stdin("print(1)")
    assert stdin_exc.value.code == code_names.UNSUPPORTED_OPERATION

    with pytest.raises(UnsupportedOperationError) as eof_exc:
        await handle.close_stdin()
    assert eof_exc.value.code == code_names.UNSUPPORTED_OPERATION
    assert transport.specs == []


@pytest.mark.anyio
async def test_process_stdin_allows_exec_and_unknown_handles_to_call_the_wire() -> None:
    exec_transport = RecordingTransport([{}])
    exec_handle = AsyncProcessHandle("sbx", "exec-proc", exec_transport, kind="exec")  # type: ignore[arg-type]

    await exec_handle.write_stdin("hello")
    assert exec_transport.specs == [
        RequestSpec("POST", "/processes/exec-proc/stdin", json_body={"eof": False, "data": "hello"})
    ]

    unknown_transport = RecordingTransport([{}])
    unknown_handle = AsyncProcessHandle("sbx", "unknown-proc", unknown_transport)  # type: ignore[arg-type]

    await unknown_handle.close_stdin()
    assert unknown_transport.specs == [
        RequestSpec("POST", "/processes/unknown-proc/stdin", json_body={"eof": True, "data": ""})
    ]


@pytest.mark.anyio
async def test_process_wait_computed_exhaustion_returns_timed_out_without_forwarding_zero(monkeypatch):
    now = iter([0.0, 0.0, 0.001, 0.001])
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(now, 0.001))
    transport = RecordingTransport([{"reason": "timed_out"}])

    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]

    assert await handle.wait(timeout=0.001) == WaitResult(reason="timed_out")
    assert [(spec.path, spec.params, spec.unbounded_read) for spec in transport.specs] == [
        ("/processes/proc/wait", {"timeoutMs": 1}, False)
    ]
    assert transport.specs[0].timeout == pytest.approx(0.001 + WAIT_READ_SLACK_SECONDS)


@pytest.mark.anyio
async def test_process_wait_preserves_the_caller_deadline(monkeypatch):
    # deadline, budget, post-poll check, record-read budget, budget, ...
    now = iter([0.0, 0.0, 600.0, 600.0, 600.0, 600.001])
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(now, 600.001))
    transport = RecordingTransport(
        [
            {"reason": "timed_out"},
            _process_payload("proc", "exec", "running"),
            {"reason": "exited", "exitCode": 0},
        ]
    )

    handle = AsyncProcessHandle("sbx", "proc", transport)  # type: ignore[arg-type]

    assert (await handle.wait(timeout=600.001)).exit_code == 0
    assert transport.specs[0].params == {"timeoutMs": 600001}
    assert transport.specs[2].params is not None
    remaining = transport.specs[2].params["timeoutMs"]
    assert isinstance(remaining, int)
    assert 0 < remaining <= 1


@pytest.mark.anyio
async def test_sandbox_snapshot_and_signed_preview_methods_live_on_ports_namespace():
    api = RecordingTransport(
        [
            _snapshot_operation_payload("snap"),
            {
                "id": "snap",
                "class": "linux",
                "kind": "full",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "name": "snap",
                "region_id": "us",
                "status": "ready",
                "region_ids": ["us"],
            },
            {"url": "https://8080-sdeadbeef.preview", "token": "sdeadbeef", "expires_at": _DEFAULT_TIME},
            {},
        ]
    )
    sandbox = AsyncSandbox._from_api(_sandbox_detail_payload("sbx", "running"), api, None, access_token="key")  # type: ignore[arg-type]

    snapshot = await sandbox.create_snapshot("snap")
    assert isinstance(snapshot, Snapshot)
    assert snapshot.id == "snap"
    assert (await sandbox.ports.signed_preview_link(8080)).token == "sdeadbeef"
    assert await sandbox.ports.expire_signed_preview_link(8080, "tok/en") is None
    assert api.specs[2].path == "/sandboxes/sbx/ports/8080/signed-preview-urls"
    assert api.specs[3].path == "/sandboxes/sbx/ports/8080/signed-preview-urls/revoke"
    assert api.specs[3].json_body == {"token": "tok/en"}


@pytest.mark.anyio
async def test_create_snapshot_quotes_the_sandbox_id():
    snapshot_payload = {
        "id": "snap",
        "class": "linux",
        "kind": "full",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "name": "snap",
        "region_id": "us",
        "status": "ready",
        "region_ids": ["us"],
    }
    linux_api = RecordingTransport([_snapshot_operation_payload("snap"), dict(snapshot_payload)])
    linux = AsyncSandbox._from_api(
        _sandbox_detail_payload("sbx!'()*", "running"), linux_api, None, access_token="key"
    )  # type: ignore[arg-type]
    assert (await linux.create_snapshot("snap")).id == "snap"
    assert linux_api.specs[0].path == "/sandboxes/sbx%21%27%28%29%2A/snapshots"
    assert linux_api.specs[0].json_body == {"name": "snap"}


@pytest.mark.anyio
async def test_interpreter_rebinds_explicit_contexts_to_the_current_service_transport() -> None:
    shared_context = AsyncInterpreterService(
        "sbx",
        RecordingTransport([]),
        AsyncProcessService("sbx", RecordingTransport([])),  # type: ignore[arg-type]
    ).connect_context("ctx", language="python")
    transport = RecordingTransport(
        [
            _process_payload("proc", "code", "running", language="python"),
            {"reason": "exited", "exitCode": 0},
            {"frames": [], "nextCursor": None},
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    result = await interpreter.run("print(1)", context=shared_context)

    assert result.process.exit_code == 0
    assert transport.specs[0].path == "/processes"
    assert transport.specs[0].json_body == {
        "code": "print(1)",
        "kind": "code",
        "stdin": "closed",
        "sessionId": "ctx",
        "language": "python",
        "keepLogs": "on_exit_ttl",
    }


@pytest.mark.anyio
async def test_interpreter_timeout_does_not_turn_an_expired_budget_into_an_unbounded_wait(monkeypatch):
    class DelayedRecordingTransport(RecordingTransport):
        async def send(self, spec: RequestSpec):
            if spec.method == "POST" and spec.path == "/processes":
                delayed_clock["now"] = 10.0
            return await super().send(spec)

    delayed_clock = {"now": 0.0}
    transport = DelayedRecordingTransport(
        [
            _process_payload("proc", "code", "running", language="python"),
            {},
            {"reason": "exited", "exitCode": 130},
            {"frames": [], "nextCursor": None},
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("daytona.v2._clock.monotonic", lambda: delayed_clock["now"])
        result = await interpreter.run("print(1)", language="python", timeout=10.0)

    assert result.process.reason == "exited"
    assert transport.specs[1] == RequestSpec(
        "POST", "/processes/proc/signals", json_body={"signal": "SIGINT"}
    )
    assert not any(
        spec.path == "/processes/proc/wait" and spec.params == {"timeoutMs": 0} for spec in transport.specs
    )


@pytest.mark.anyio
async def test_interpreter_rejects_env_on_explicit_context_before_posting_processes():
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("sess", language="python")

    with pytest.raises(
        DaytonaError,
        match="env is only supported when creating a context or running without an explicit context",
    ):
        await context.run("print(1)", env={"A": "B"})

    assert transport.specs == []


@pytest.mark.anyio
async def test_interpreter_recomputes_wait_budget_after_process_creation():
    class DelayedRecordingTransport(RecordingTransport):
        async def send(self, spec: RequestSpec):
            if spec.method == "POST" and spec.path == "/processes":
                delayed_clock["now"] = 5.0
            return await super().send(spec)

    delayed_clock = {"now": 0.0}
    transport = DelayedRecordingTransport(
        [
            _process_payload("proc", "code", "running", language="python"),
            {"reason": "exited", "exitCode": 0},
            {
                "frames": [{"cursor": "1", "channel": "stdout", "data": "ok", "timestamp": 1}],
                "nextCursor": "1",
            },
            {"frames": [], "nextCursor": "1"},
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: delayed_clock["now"])
        result = await interpreter.run("print(1)", language="python", timeout=10.0)

    assert result.logs.stdout == "ok"
    assert (transport.specs[1].path, transport.specs[1].params) == (
        "/processes/proc/wait",
        {"timeoutMs": 5000},
    )
    assert transport.specs[0].timeout == 10.0
    assert transport.specs[1].timeout == pytest.approx(5.0 + WAIT_READ_SLACK_SECONDS)
    assert transport.specs[1].unbounded_read is False


@pytest.mark.anyio
async def test_wait_or_interrupt_tolerates_queued_unsupported_interrupt(monkeypatch):
    from daytona.v2.process import wait_or_interrupt
    from daytona.v2.types import WaitResult

    class Handle:
        def __init__(self):
            self.waits = 0
            self.kills = 0

        async def wait(self, *, timeout=None):
            self.waits += 1
            if timeout == 0.25:
                return (
                    WaitResult(reason="timed_out")
                    if self.waits < 3
                    else WaitResult(exit_code=130, reason="exited")
                )
            return WaitResult(reason="timed_out")

        async def kill(self, **_kwargs):
            self.kills += 1
            raise create_error("queued", 400, code_names.UNSUPPORTED_OPERATION, "DAYTONA_DAEMON")

    now = iter([0.0, 0.0, 0.002, 0.002, 0.003, 0.004])
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: next(now, 0.004))

    assert (await wait_or_interrupt(Handle(), timeout=0.001)).exit_code == 130  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_interpreter_context_serializes_concurrent_runs_before_submitting_the_next_process(
    monkeypatch,
) -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("ctx", language="python")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_submitted = asyncio.Event()
    submitted: list[tuple[str, Optional[float]]] = []

    async def fake_run_bound_context(
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        assert context_id == "ctx"
        assert language == "python"
        submitted.append((code, timeout))
        if code == "first":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), timeout=1)
        if code == "second":
            second_submitted.set()
        return InterpreterResult(
            process=InterpreterProcess(f"proc-{code}", 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_bound_context", fake_run_bound_context)

    first = asyncio.create_task(context.run("first"))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(context.run("second"))
    await asyncio.sleep(0)
    assert second_submitted.is_set() is False
    assert submitted == [("first", None)]

    release_first.set()
    results = await asyncio.gather(first, second)

    assert submitted == [("first", None), ("second", None)]
    assert sorted(result.logs.stdout for result in results) == ["first", "second"]


@pytest.mark.anyio
async def test_interpreter_gate_registry_creation_is_atomic_under_concurrent_rebinds(monkeypatch) -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    created: list[object] = []
    start = asyncio.Event()

    class SlowGate:
        def __init__(self) -> None:
            created.append(self)

        async def run(self, timeout: Optional[float], task):  # type: ignore[no-untyped-def]
            return await task(timeout)

    monkeypatch.setattr("daytona.v2.interpreter._ContextExecutionGate", SlowGate)

    async def connect() -> object:
        await asyncio.wait_for(start.wait(), timeout=1)
        return interpreter.connect_context("ctx", language="python")

    first = asyncio.create_task(connect())
    second = asyncio.create_task(connect())
    await asyncio.sleep(0)
    start.set()
    contexts = await asyncio.gather(first, second)

    assert len(contexts) == 2
    assert len(created) == 1


@pytest.mark.anyio
async def test_interpreter_context_run_returns_timed_out_without_submitting_when_the_queue_wait_expires(
    monkeypatch,
) -> None:
    transport = RecordingTransport([])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("ctx", language="python")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_submitted = asyncio.Event()
    submitted: list[tuple[str, Optional[float]]] = []

    async def fake_run_bound_context(
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        assert context_id == "ctx"
        assert language == "python"
        submitted.append((code, timeout))
        if code == "first":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), timeout=1)
        if code == "second":
            second_submitted.set()
        return InterpreterResult(
            process=InterpreterProcess(f"proc-{code}", 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_bound_context", fake_run_bound_context)

    first = asyncio.create_task(context.run("first"))
    await asyncio.wait_for(first_started.wait(), timeout=1)

    timed_out = await context.run("second", timeout=0.01)

    assert timed_out.process.reason == "timed_out"
    assert timed_out.process.process_id == ""
    assert submitted == [("first", None)]
    assert second_submitted.is_set() is False

    release_first.set()
    await asyncio.wait_for(first, timeout=1)
    assert submitted == [("first", None)]


@pytest.mark.anyio
async def test_interpreter_failed_close_keeps_the_original_gate_for_reconnects(monkeypatch) -> None:
    transport = RecordingTransport(
        [
            create_error("still alive", 503, code_names.SERVICE_UNAVAILABLE, "DAYTONA_DAEMON"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("ctx", language="python")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_submitted = asyncio.Event()
    submitted: list[tuple[str, Optional[float]]] = []

    async def fake_run_bound_context(
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        assert context_id == "ctx"
        assert language == "python"
        submitted.append((code, timeout))
        if code == "first":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), timeout=1)
        if code == "second":
            second_submitted.set()
        return InterpreterResult(
            process=InterpreterProcess(f"proc-{code}", 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_bound_context", fake_run_bound_context)

    first = asyncio.create_task(context.run("first"))
    await asyncio.wait_for(first_started.wait(), timeout=1)

    with pytest.raises(DaytonaError, match="still alive"):
        await context.close()

    rebound = interpreter.connect_context("ctx", language="python")
    second = asyncio.create_task(rebound.run("second"))
    await asyncio.sleep(0)
    assert second_submitted.is_set() is False

    assert submitted == [("first", None)]
    assert len(interpreter._context_gates) == 1
    assert transport.specs == [RequestSpec("DELETE", "/sessions/ctx")]

    release_first.set()
    results = await asyncio.gather(first, second)

    assert submitted == [("first", None), ("second", None)]
    assert sorted(result.logs.stdout for result in results) == ["first", "second"]


@pytest.mark.anyio
async def test_interpreter_delete_context_waits_to_evict_the_gate_until_queued_runs_drain(
    monkeypatch,
) -> None:
    transport = RecordingTransport([{}])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("ctx", language="python")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_submitted = asyncio.Event()
    submitted: list[tuple[str, Optional[float]]] = []

    async def fake_run_bound_context(
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        assert context_id == "ctx"
        assert language == "python"
        submitted.append((code, timeout))
        if code == "first":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), timeout=1)
        if code == "second":
            second_submitted.set()
        return InterpreterResult(
            process=InterpreterProcess(f"proc-{code}", 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_bound_context", fake_run_bound_context)

    first = asyncio.create_task(context.run("first"))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(context.run("second"))
    await asyncio.sleep(0)
    assert second_submitted.is_set() is False

    await interpreter.delete_context(context)

    assert "ctx" in interpreter._context_gates
    assert submitted == [("first", None)]
    assert transport.specs == [RequestSpec("DELETE", "/sessions/ctx")]

    release_first.set()
    results = await asyncio.gather(first, second)

    assert submitted == [("first", None), ("second", None)]
    assert "ctx" not in interpreter._context_gates
    assert sorted(result.logs.stdout for result in results) == ["first", "second"]


@pytest.mark.anyio
async def test_interpreter_isolated_runs_do_not_leak_gate_entries(monkeypatch) -> None:
    isolated_ids = [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ]
    responses: list[object] = []
    for isolated_id in isolated_ids:
        responses.extend(
            [
                _session_payload(f"isolated-python-{uuid.UUID(isolated_id).hex}", kernel="python"),
                {},
            ]
        )
    transport = RecordingTransport(responses)
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    ids = iter(isolated_ids)

    async def fake_run_bound_context(
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        return InterpreterResult(
            process=InterpreterProcess(context_id, 0, None, "exited"),
            logs=InterpreterLogs(stdout=code),
        )

    monkeypatch.setattr(interpreter, "_run_bound_context", fake_run_bound_context)
    monkeypatch.setattr("daytona.v2.interpreter.uuid.uuid4", lambda: uuid.UUID(next(ids)))

    for _ in isolated_ids:
        await interpreter.run("print(1)", language="python", isolated=True)

    assert interpreter._context_gates == {}
    assert [spec.method for spec in transport.specs] == ["POST", "DELETE", "POST", "DELETE", "POST", "DELETE"]


def test_transport_decodes_json_bytes_errors_streams_and_request_helpers():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/json":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/bytes":
            return httpx.Response(200, content=b"raw")
        if request.url.path == "/empty":
            return httpx.Response(204, content=b"")
        if request.url.path == "/bad":
            return httpx.Response(
                409, json={"message": "exists", "code": code_names.NAME_CONFLICT, "source": "DAYTONA_DAEMON"}
            )
        if request.url.path == "/stream":
            return httpx.Response(200, text='event: log\ndata: {"cursor":"c"}\n\n')
        return httpx.Response(200, json={"method": request.method})

    transport = SyncTransport("http://api.invalid", "token", source="tests")
    transport._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.invalid")  # type: ignore[attr-defined]
    assert transport.send(RequestSpec("GET", "/json", params={"q": "a b"})) == {"ok": True}
    assert transport.raw(RequestSpec("GET", "/bytes")) == b"raw"
    assert transport.send(RequestSpec("GET", "/empty")) == b""
    with pytest.raises(Exception) as err:
        transport.send(RequestSpec("GET", "/bad"))
    assert err.value.__class__.__name__ == "NameConflictError"
    assert list(transport.stream_lines(RequestSpec("GET", "/stream"))) == [
        "event: log",
        'data: {"cursor":"c"}',
        "",
    ]
    assert transport.get("/helper").json() == {"method": "GET"}
    assert transport.post("/helper").json() == {"method": "POST"}
    assert transport.put("/helper").json() == {"method": "PUT"}
    assert transport.patch("/helper").json() == {"method": "PATCH"}
    assert transport.delete("/helper").json() == {"method": "DELETE"}
    assert requests[0].url.query == b"q=a+b"
    transport.close()


@pytest.mark.anyio
async def test_open_stream_closes_stream_context_when_error_body_read_fails():
    class BrokenResponse:
        def __init__(self) -> None:
            self.status_code = 409
            self.request = httpx.Request("GET", "http://api.invalid/fail")
            self.headers = httpx.Headers()

        def iter_bytes(self) -> Iterator[bytes]:
            raise httpx.ReadError("broken body", request=self.request)

        def close(self) -> None:
            pass

    class RecordingStreamContext:
        def __init__(self) -> None:
            self.exited = False

        def __enter__(self) -> BrokenResponse:
            return BrokenResponse()

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool | None:
            self.exited = True
            return None

    transport = SyncTransport("http://api.invalid")
    context = RecordingStreamContext()
    transport._client.stream = lambda *_args, **_kwargs: context  # type: ignore[method-assign, assignment]

    with pytest.raises(ConnectionError):
        await transport.open_stream(RequestSpec("GET", "/fail"))

    assert context.exited is True
    transport.close()


def test_transport_request_timeout_zero_builds_unbounded_httpx_timeouts() -> None:
    transport = SyncTransport("http://api.invalid", request_timeout=0)
    client_timeout = transport._client.timeout  # type: ignore[attr-defined]
    wait_timeout = _timeout_with_unbounded_read(0.0)

    assert client_timeout.connect is None
    assert client_timeout.read is None
    assert client_timeout.write is None
    assert client_timeout.pool is None
    assert wait_timeout.connect is None
    assert wait_timeout.read is None
    assert wait_timeout.write is None
    assert wait_timeout.pool is None

    transport.close()
    asyncio.run(AsyncTransport("http://api.invalid", request_timeout=0).close())


def test_root_exports_interpreter_context_json_and_lsp_completion_item() -> None:
    from daytona.v2 import InterpreterContextJSON, LspCompletionItem

    assert InterpreterContextJSON.__name__ == "InterpreterContextJSON"
    assert LspCompletionItem.__name__ == "CompletionItem"


@pytest.mark.anyio
async def test_transport_maps_timeout_and_connection_errors_for_sync_and_async():
    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    def network_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = SyncTransport("http://api.invalid")
    transport._client = httpx.Client(
        transport=httpx.MockTransport(timeout_handler), base_url="http://api.invalid"
    )  # type: ignore[attr-defined]
    with pytest.raises(ClientTimeoutError):
        transport.send(RequestSpec("GET", "/timeout"))
    transport._client = httpx.Client(
        transport=httpx.MockTransport(network_handler), base_url="http://api.invalid"
    )  # type: ignore[attr-defined]
    with pytest.raises(ConnectionError):
        transport.request("GET", "/network")

    async_transport = AsyncTransport("http://api.invalid")
    async_transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler), base_url="http://api.invalid"
    )  # type: ignore[attr-defined]
    with pytest.raises(ClientTimeoutError):
        await async_transport.send(RequestSpec("GET", "/timeout"))
    await async_transport.close()


@pytest.mark.anyio
async def test_async_transport_preserves_request_spec_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: value for key, value in request.headers.items()})
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    async_transport = AsyncTransport("http://api.invalid", "token")
    async_transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://api.invalid"
    )  # type: ignore[attr-defined]

    try:
        payload = await async_transport.send(RequestSpec("GET", "/ok", headers={"X-Test": "yes"}))
    finally:
        await async_transport.close()

    assert payload == {"ok": True}
    assert seen["x-test"] == "yes"


def _cookie_echo_handler(seen_cookies: list[str | None]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("cookie"))
        response = httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})
        if len(seen_cookies) == 1:
            response.headers["set-cookie"] = "sid=abc"
        return response

    return handler


@pytest.mark.anyio
async def test_async_transport_blocks_server_cookies() -> None:
    seen_cookies: list[str | None] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_cookie_echo_handler(seen_cookies)))
    async_transport = AsyncTransport("http://api.invalid", "token", client=client)

    try:
        assert await async_transport.send(RequestSpec("GET", "/one")) == {"ok": True}
        assert await async_transport.send(RequestSpec("GET", "/two")) == {"ok": True}
    finally:
        await client.aclose()

    assert seen_cookies == [None, None]


@pytest.mark.anyio
async def test_transport_rejects_absolute_request_urls():
    transport = SyncTransport("http://api.invalid", "token")

    with pytest.raises(DaytonaError, match="relative SDK path"):
        transport.send(RequestSpec("GET", "https://evil.invalid/steal"))

    async_transport = AsyncTransport("http://api.invalid", "token")
    with pytest.raises(DaytonaError, match="relative SDK path"):
        await async_transport.send(RequestSpec("GET", "//evil.invalid/steal"))
    await async_transport.close()


@pytest.mark.anyio
async def test_delete_by_id_waits_until_destroyed_and_counts_a_404_as_done():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {},
            _sandbox_detail_payload("sb-123", "deleting"),
            create_error("sandbox not found", 404, "NOT_FOUND", "DAYTONA_API"),
        ]
    )
    bind_api(client, api)
    await client.delete("sb-123", timeout=10)
    assert [spec.method for spec in api.specs] == ["DELETE", "GET", "GET"]
    assert all(spec.path == "/sandboxes/sb-123" for spec in api.specs)
    # The wait holds its long poll for the destroyed state exactly.
    assert api.specs[1].params["wait_exact"] == "deleted"
    assert "wait" not in api.specs[1].params

    handle = lazy_sandbox(client, "sb-456")
    handle._api = RecordingTransport([{}, _sandbox_detail_payload("sb-456", "deleted")])  # type: ignore[attr-defined]
    await handle.delete()
    assert handle.state == "destroyed"


@pytest.mark.anyio
async def test_delete_raises_typed_errors_when_its_wait_ends_without_destroyed():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    failed = lazy_sandbox(client, "sb-err")
    failed._api = RecordingTransport([{}, _sandbox_detail_payload("sb-err", "failed")])  # type: ignore[attr-defined]
    with pytest.raises(SandboxTerminalStateError) as terminal:
        await failed.delete(timeout=10)
    assert terminal.value.state == "error"

    slow = lazy_sandbox(client, "sb-slow")
    slow._api = RecordingTransport(  # type: ignore[attr-defined]
        [{}, *[_sandbox_detail_payload("sb-slow", "deleting") for _ in range(50)]]
    )
    with pytest.raises(RequestTimeoutError) as timed_out:
        await slow.delete(timeout=0.05)
    assert (timed_out.value.status_code, timed_out.value.code, timed_out.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    with pytest.raises(BadRequestError, match="timeout"):
        await slow.delete(timeout=-1)


@pytest.mark.anyio
async def test_stop_by_id_posts_then_waits_until_stopped():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {},
            _sandbox_detail_payload("sb-123", "stopping", sandbox_class="macos", persistent=True),
            _sandbox_detail_payload("sb-123", "stopped", sandbox_class="macos", persistent=True),
        ]
    )
    bind_api(client, api)

    await client.stop("sb-123", timeout=10)

    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes/sb-123/stop"),
        ("GET", "/sandboxes/sb-123"),
        ("GET", "/sandboxes/sb-123"),
    ]
    assert api.specs[1].params["wait"] == "stopped"


@pytest.mark.anyio
async def test_stop_raises_request_timeout_when_its_wait_runs_out():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{}, *[_sandbox_detail_payload("sb", "stopping") for _ in range(50)]])
    bind_api(client, api)
    with pytest.raises(RequestTimeoutError):
        await client.stop("sb", timeout=0.05)


def test_size_limit_codes_map_to_typed_conflict_errors_with_byte_limits():
    import daytona.v2 as sdk
    from daytona.v2._core import parse_error_payload

    image = parse_error_payload(
        409,
        {
            "code": "IMAGE_TOO_LARGE",
            "message": "image exceeds the maximum size of 50 GiB",
            "source": "DAYTONA_API",
            "max_bytes": 53687091200,
            "bytes": 60000000000,
        },
    )
    assert isinstance(image, sdk.ImageTooLargeError)
    assert isinstance(image, sdk.ConflictError)
    assert image.max_bytes == 53687091200
    assert image.bytes == 60000000000

    # `bytes` is optional on the wire: absent or malformed reads as None.
    snapshot = parse_error_payload(
        409,
        {
            "code": "SNAPSHOT_TOO_LARGE",
            "message": "snapshot exceeds the maximum size of 8 GiB",
            "source": "DAYTONA_API",
            "max_bytes": 8589934592,
            "bytes": "big",
        },
    )
    assert isinstance(snapshot, sdk.SnapshotTooLargeError)
    assert isinstance(snapshot, sdk.ConflictError)
    assert snapshot.max_bytes == 8589934592
    assert snapshot.bytes is None


def test_error_maps_cover_all_code_and_status_branches():
    for code in codes.DAEMON_ERROR_CODES:
        err = create_error(f"message {code}", None, code, "DAYTONA_DAEMON")
        assert isinstance(err, CODE_ERROR_CLASSES[code])
        assert err.code == code
        assert err.source == "DAYTONA_DAEMON"
    assert error_class_from_status(None) is DaytonaError
    assert error_class_from_status(418) is DaytonaError
    assert error_class_from_status(500).__name__ == "InternalServerError"
    assert isinstance(create_error("generic", 503, None, "DAYTONA_API"), ServiceUnavailableError)


def test_limit_exceeded_code_maps_to_limit_exceeded_error_with_extras():
    from daytona.v2._core import parse_error_payload

    err = parse_error_payload(
        429,
        {
            "code": "LIMIT_EXCEEDED",
            "message": "organization limit reached: 25 of 25 (max_concurrent_sandboxes)",
            "source": "DAYTONA_API",
            "limit": "max_concurrent_sandboxes",
            "current": 25,
            "max": 25,
            "trace_id": "tr-1",
        },
    )
    assert isinstance(err, LimitExceededError)
    assert isinstance(err, RateLimitError)
    assert err.status_code == 429
    assert err.code == "LIMIT_EXCEEDED"
    assert (err.limit, err.current, err.max) == ("max_concurrent_sandboxes", 25, 25)
    assert err.trace_id == "tr-1"

    # Malformed extras cost the extra alone, never the classification.
    tolerant = parse_error_payload(
        429,
        {
            "code": "LIMIT_EXCEEDED",
            "message": "organization limit reached",
            "source": "DAYTONA_API",
            "limit": 42,
            "current": "25",
            "max": 25.5,
        },
    )
    assert isinstance(tolerant, LimitExceededError)
    assert (tolerant.limit, tolerant.current, tolerant.max) == (None, None, None)

    bare = parse_error_payload(
        429, {"code": "LIMIT_EXCEEDED", "message": "organization limit reached", "source": "DAYTONA_API"}
    )
    assert isinstance(bare, LimitExceededError)
    assert (bare.limit, bare.current, bare.max) == (None, None, None)


def test_other_429s_stay_rate_limit_errors_and_dispatch_is_source_scoped():
    from daytona.v2._core import parse_error_payload

    bare = create_error("too many requests", 429)
    assert type(bare) is RateLimitError

    # An envelope-less prose body is a message
    # fallback only — never promoted to a code, never refined.
    prose = parse_error_payload(429, {"error": "request limit reached"})
    assert type(prose) is RateLimitError
    assert prose.code is None
    assert prose.message == "request limit reached"

    # LIMIT_EXCEEDED is a DAYTONA_API code: another source falls back to status.
    foreign = create_error("limit exceeded", 429, code_names.LIMIT_EXCEEDED, "DAYTONA_DAEMON")
    assert type(foreign) is RateLimitError
    assert not isinstance(foreign, LimitExceededError)
    assert foreign.code == code_names.LIMIT_EXCEEDED


def test_error_status_code_is_the_transport_status_never_a_body_field():
    # Driven through the real transport funnel (_decode_response ->
    # _error_from_response), which reads the response's own status and never a
    # body-supplied `statusCode`. Mirror of the TypeScript suite.
    forged_body = {
        "code": "LIMIT_EXCEEDED",
        "statusCode": 429,
        "message": "organization limit reached",
        "source": "DAYTONA_API",
        "limit": "max_concurrent_sandboxes",
        "current": 9,
        "max": 9,
    }
    with pytest.raises(DaytonaError) as forged:
        _decode_response(httpx.Response(500, json=forged_body))
    assert forged.value.status_code == 500
    assert forged.value.code == "LIMIT_EXCEEDED"

    with pytest.raises(LimitExceededError) as plain:
        _decode_response(
            httpx.Response(
                429,
                json={
                    "code": "LIMIT_EXCEEDED",
                    "message": "organization limit reached: 25 of 25 (max_concurrent_sandboxes)",
                    "source": "DAYTONA_API",
                    "limit": "max_concurrent_sandboxes",
                    "current": 25,
                    "max": 25,
                },
            )
        )
    assert plain.value.status_code == 429
    assert (plain.value.limit, plain.value.current, plain.value.max) == (
        "max_concurrent_sandboxes",
        25,
        25,
    )


def test_error_body_round_trip_keeps_absent_optional_fields_absent():
    # Round-tripping a response preserves absent optional fields rather than adding nulls.
    from daytona.v2._gen.api import ErrorBody

    minimal = {"code": "BAD_REQUEST", "message": "bad request: unknown mode", "source": "DAYTONA_API"}
    assert ErrorBody.from_dict(dict(minimal)).to_dict() == minimal

    refusal = {
        "code": "LIMIT_EXCEEDED",
        "message": "concurrent sandbox limit reached",
        "source": "DAYTONA_API",
        "limit": "max_concurrent_sandboxes",
        "current": 25,
        "max": 25,
    }
    assert ErrorBody.from_dict(dict(refusal)).to_dict() == refusal


def test_generated_enums_accept_values_a_newer_server_adds() -> None:
    # Every generated enum is open (enumUnknownDefaultCase): a value this SDK
    # release does not list parses to the UNKNOWN_DEFAULT_OPEN_API pseudo-member
    # carrying the real wire string, so a newer server adding an error code or
    # CPU architecture is not a validation error and round-trips unchanged.
    from daytona.v2._gen.api import ApiErrorCode, CpuArchitecture, ErrorBody
    from daytona.v2._gen.toolbox import ErrorCode

    error = {"code": "FUTURE_ERROR", "message": "a newer API", "source": "DAYTONA_API"}
    parsed = ErrorBody.from_dict(dict(error))
    assert parsed.code.name == "UNKNOWN_DEFAULT_OPEN_API"
    assert parsed.code == "FUTURE_ERROR"
    assert isinstance(parsed.code, ApiErrorCode)
    assert parsed.to_dict() == error
    assert CpuArchitecture("orbital").value == "orbital"
    assert ErrorCode("NEW_DAEMON_CODE").name == "UNKNOWN_DEFAULT_OPEN_API"
    assert CpuArchitecture("amd64") is CpuArchitecture.AMD64


def test_request_model_round_trip_keeps_absent_nested_and_container_fields_absent() -> None:
    from daytona.v2._gen.api import (
        CreateSandboxFromSnapshotRequest,
        CreateSandboxImageRequest,
        CreateSandboxRequest,
        Image,
        ImageReference,
    )

    linux = CreateSandboxRequest.from_dict({"image": "img_1"})
    assert linux.to_dict() == {"image": "img_1"}
    for field in ("env", "labels", "lifecycle_policy", "network_policy", "ports", "secrets", "wait_seconds"):
        assert field not in linux.model_fields_set

    explicit_null = CreateSandboxRequest.from_dict({"image": "img_1", "name": "n", "network_policy": None})
    assert explicit_null.to_dict() == {"image": "img_1", "name": "n", "network_policy": None}
    assert explicit_null.model_fields_set == {"image", "name", "network_policy"}

    restore = CreateSandboxFromSnapshotRequest.from_dict({"snapshot": "snap_1"})
    assert restore.to_dict() == {"snapshot": "snap_1"}
    for field in ("labels", "lifecycle_policy", "name", "ports", "region_id", "wait_seconds"):
        assert field not in restore.model_fields_set

    assert CreateSandboxImageRequest.from_dict({}).to_dict() == {}

    artifact = ImageReference.from_dict({"id": "img_3"})
    assert artifact.to_dict() == {"id": "img_3"}
    assert artifact.model_fields_set == {"id"}

    image = Image.from_dict(
        {
            "anonymous": False,
            "region_ids": ["region-b"],
            "class": "linux",
            "created_at": "2026-08-24T00:00:00Z",
            "format_outdated": False,
            "format_version": 2,
            "id": "img_4",
            "origin": "registry",
            "region_id": "region-b",
            "retention": "permanent",
            "status": "ready",
            "platform": False,
            "default": False,
            "deprecated": False,
            "updated_at": "2026-08-24T00:00:00Z",
        }
    )
    assert image.to_dict() == {
        "anonymous": False,
        "region_ids": ["region-b"],
        "class": "linux",
        "created_at": image.created_at,
        "format_outdated": False,
        "format_version": 2,
        "id": "img_4",
        "platform": False,
        "default": False,
        "deprecated": False,
        "origin": "registry",
        "region_id": "region-b",
        "retention": "permanent",
        "status": "ready",
        "updated_at": image.updated_at,
    }
    for field in ("bytes", "error", "expires_at", "layer_count", "name", "source"):
        assert field not in image.model_fields_set


class RecordingTransport:
    request_timeout_seconds = 30.0

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec):
        self.specs.append(spec)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def raw(self, spec: RequestSpec) -> bytes:
        value = await self.send(spec)
        assert isinstance(value, bytes)
        return value

    async def stream_lines(self, spec: RequestSpec):
        self.specs.append(spec)
        yield "event: log"
        yield 'data: {"cursor":"c","channel":"stdout","data":"line"}'
        yield ""

    async def close(self) -> None:
        pass


class ControlledSseStream:
    """A hand-fed SSE line source with the AsyncResponseStream shape."""

    def __init__(self) -> None:
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    def push(self, line: str) -> None:
        self._lines.put_nowait(line)

    async def iter_lines(self):
        while True:
            line = await self._lines.get()
            if line is None:
                return
            yield line

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._lines.put_nowait(None)


# --- forward compatibility: a newer server must not break an older SDK -------


def test_unlisted_enum_values_normalize_to_unknown_with_raw_kept() -> None:
    from daytona.v2._core import expect_exit_result, expect_process_dto, parse_process, parse_wait

    info = parse_process(
        expect_process_dto(
            _process_payload("p-new", "exec", "hibernating", reason="preempted", keepLogs="forever")
        )
    )
    assert info.state == "unknown"
    assert info.raw_state == "hibernating"
    assert info.reason == "unknown"
    assert info.raw_reason == "preempted"
    assert info.keep_logs == "unknown"
    assert info.raw_keep_logs == "forever"
    # Known values still map to the same public literals.
    assert parse_process(expect_process_dto(_process_payload("p", "exec", "running"))).state == "running"

    wait = parse_wait(expect_exit_result({"exitCode": 0, "reason": "vanished"}))
    assert wait.reason == "unknown"
    assert wait.raw_reason == "vanished"


def test_contract_backed_server_vocabularies_normalize_unknown_to_unknown_and_keep_raw_fields() -> None:
    from daytona.v2._core import expect_docker_status

    status = expect_docker_status({"state": "warming", "owner": "sidecar", "reason": "preparing"})
    assert status.state == "unknown"
    assert status.raw_state == "warming"
    assert status.owner == "unknown"
    assert status.raw_owner == "sidecar"


def test_public_known_vocab_literals_match_generated_contract_enums() -> None:
    from enum import Enum
    from typing import get_args

    from daytona.v2._gen.api import BuildLogDoneReason as BuildLogDoneReasonEnum
    from daytona.v2._gen.api import BuildLogGapReason as BuildLogGapReasonEnum
    from daytona.v2._gen.toolbox import ComputerUseDesktopFailureCode as ComputerUseDesktopFailureCodeEnum
    from daytona.v2._gen.toolbox import ComputerUseDesktopState as ComputerUseDesktopStateEnum
    from daytona.v2._gen.toolbox import DockerOwner as DockerOwnerEnum
    from daytona.v2._gen.toolbox import DockerState as DockerStateEnum
    from daytona.v2._gen.toolbox import KeepLogs as KeepLogsEnum
    from daytona.v2._gen.toolbox import Kind as KindEnum
    from daytona.v2._gen.toolbox import PullVolumeStatus as PullVolumeStatusEnum
    from daytona.v2._gen.toolbox import Reason as ReasonEnum
    from daytona.v2._gen.toolbox import State as StateEnum
    from daytona.v2._gen.toolbox import StdinMode as StdinModeEnum
    from daytona.v2.build_logs import BuildLogDoneReason, BuildLogGapReason
    from daytona.v2.computer_use_types import ComputerUseDesktopFailureCode, ComputerUseDesktopState
    from daytona.v2.sandbox_volumes import PullVolumeStatus
    from daytona.v2.types import (
        DockerOwner,
        DockerState,
        ProcessInfoKind,
        ProcessKeepLogs,
        ProcessState,
        ProcessStdinMode,
        WaitReason,
    )

    def literal_values(alias: object) -> set[str]:
        values: set[str] = set()
        for value in get_args(alias):
            if value is str:
                continue
            nested = get_args(value)
            if nested:
                values.update(item for item in nested if isinstance(item, str) and item != "unknown")
                continue
            if isinstance(value, str) and value != "unknown":
                values.add(value)
        return values

    def enum_values(enum_type: type[Enum]) -> set[str]:
        return {str(member.value) for member in enum_type if member.name != "UNKNOWN_DEFAULT_OPEN_API"}

    assert literal_values(ProcessInfoKind) == enum_values(KindEnum)
    assert literal_values(ProcessState) == enum_values(StateEnum)
    assert literal_values(ProcessStdinMode) == enum_values(StdinModeEnum)
    assert literal_values(ProcessKeepLogs) == enum_values(KeepLogsEnum)
    assert literal_values(WaitReason) == enum_values(ReasonEnum)
    assert literal_values(DockerState) == enum_values(DockerStateEnum)
    assert literal_values(DockerOwner) == enum_values(DockerOwnerEnum)
    assert literal_values(PullVolumeStatus) == enum_values(PullVolumeStatusEnum)
    assert literal_values(BuildLogDoneReason) == enum_values(BuildLogDoneReasonEnum)
    assert literal_values(BuildLogGapReason) == enum_values(BuildLogGapReasonEnum)
    assert literal_values(ComputerUseDesktopState) == enum_values(ComputerUseDesktopStateEnum)
    assert literal_values(ComputerUseDesktopFailureCode) == enum_values(ComputerUseDesktopFailureCodeEnum)


def test_unknown_fields_from_a_newer_server_are_tolerated() -> None:
    from daytona.v2._core import expect_process_dto, parse_process

    payload = _process_payload("p-new", "exec", "running", brandNewField={"nested": [1, 2]}, anotherFlag=True)
    info = parse_process(expect_process_dto(payload))
    assert info.id == "p-new"
    assert info.state == "running"


@pytest.mark.anyio
async def test_process_run_keeps_unknown_wait_reason_and_raw_reason() -> None:
    transport = RecordingTransport(
        [
            _process_payload("proc", "exec", "running"),
            {"exitCode": 0, "reason": "vanished"},
            _log_page_payload([_log_frame_payload("1", "ok")], next_cursor="1"),
            _log_page_payload([], next_cursor="1"),
        ]
    )

    result = await AsyncProcessService("sbx", transport).run("echo ok")  # type: ignore[arg-type]

    assert result.reason == "unknown"
    assert result.raw_reason == "vanished"


@pytest.mark.anyio
async def test_interpreter_run_keeps_unknown_wait_reason_and_raw_reason() -> None:
    transport = RecordingTransport(
        [
            _process_payload("proc", "code", "running", language="python"),
            {"exitCode": 0, "reason": "vanished"},
            _log_page_payload([], next_cursor=None),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    result = await interpreter.run("print(1)", language="python")

    assert result.process.reason == "unknown"
    assert result.process.raw_reason == "vanished"


def test_sync_process_run_keeps_unknown_wait_reason_and_raw_reason() -> None:
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.process import ProcessService

    transport = RecordingTransport(
        [
            _process_payload("proc", "exec", "running"),
            {"exitCode": 0, "reason": "vanished"},
            _log_page_payload([_log_frame_payload("1", "ok")], next_cursor="1"),
            _log_page_payload([], next_cursor="1"),
        ]
    )
    bridge = SyncBridge(name="ProcessService")
    try:
        result = ProcessService(AsyncProcessService("sbx", transport), bridge).run("echo ok")  # type: ignore[arg-type]
    finally:
        bridge.close()

    assert result.reason == "unknown"
    assert result.raw_reason == "vanished"


def test_sync_interpreter_run_keeps_unknown_wait_reason_and_raw_reason() -> None:
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.interpreter import InterpreterService

    transport = RecordingTransport(
        [
            _process_payload("proc", "code", "running", language="python"),
            {"exitCode": 0, "reason": "vanished"},
            _log_page_payload([], next_cursor=None),
        ]
    )
    bridge = SyncBridge(name="InterpreterService")
    try:
        interpreter = InterpreterService(
            AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport)),
            bridge,
        )  # type: ignore[arg-type]
        result = interpreter.run("print(1)", language="python")
    finally:
        bridge.close()

    assert result.process.reason == "unknown"
    assert result.process.raw_reason == "vanished"


@pytest.mark.anyio
async def test_handle_kind_falls_back_to_the_requested_kind_when_unlisted() -> None:
    # An unrecognized response kind normalizes to "unknown" on ProcessInfo, but
    # a handle's kind gates the pty-only verbs and is serialized, so it takes
    # the kind the request asked for (TS: startedProcessKind).
    transport = RecordingTransport(
        [
            _process_payload("pty-1", "service", "running"),
            _process_payload("exec-1", "service", "running"),
        ]
    )
    service = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    pty = await AsyncPtyService(service).start("bash")
    started = await service.start("echo hi")

    assert pty.kind == "pty"
    assert pty.to_json()["kind"] == "pty"
    assert started.kind == "exec"


def test_sync_handle_kind_falls_back_to_the_requested_kind_when_unlisted() -> None:
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.process import ProcessService

    transport = RecordingTransport([_process_payload("exec-1", "service", "running")])
    bridge = SyncBridge(name="ProcessService")
    try:
        handle = ProcessService(AsyncProcessService("sbx", transport), bridge).start("echo hi")  # type: ignore[arg-type]
    finally:
        bridge.close()

    assert handle.kind == "exec"


def test_new_public_aliases_are_exported_from_v2_namespace() -> None:
    import daytona.v2 as daytona
    from daytona.v2 import (
        BuildLogGapReason,
        ProcessInfoKind,
        ProcessKeepLogs,
        ProcessStdinMode,
        PullVolumeStatus,
    )

    assert daytona.ProcessInfoKind is ProcessInfoKind
    assert daytona.ProcessKeepLogs is ProcessKeepLogs
    assert daytona.ProcessStdinMode is ProcessStdinMode
    assert daytona.PullVolumeStatus is PullVolumeStatus
    assert daytona.BuildLogGapReason is BuildLogGapReason


def test_input_vocabularies_are_exported_from_v2_namespace() -> None:
    import daytona.v2 as daytona
    from daytona.v2 import types

    assert daytona.ProcessKind is types.ProcessKind
    assert daytona.StdinMode is types.StdinMode
    assert daytona.KeepLogs is types.KeepLogs
    assert daytona.ProcessStateFilter is types.ProcessStateFilter


@pytest.mark.anyio
async def test_computer_use_status_maps_invalid_payloads_to_sdk_internal_errors():
    class Transport:
        async def send(self, spec):
            assert spec.path == "/computeruse/status"
            return {"desktop": "ready"}

    with pytest.raises(InternalServerError, match="Invalid computer-use status response"):
        await AsyncComputerUseService(Transport()).status()  # type: ignore[arg-type]


def test_generated_oneof_variants_discriminate_and_models_serialize() -> None:
    from daytona.v2._gen.api import CreateImageRequest, Image, PublicImageSource

    variants = {
        "registry": {"type": "registry", "ref": "python:3.12"},
        "named": {"type": "named", "name": "base"},
    }
    seen = {
        kind: type(PublicImageSource.from_dict(payload).actual_instance).__name__
        for kind, payload in variants.items()
    }
    assert len(set(seen.values())) == len(variants), seen

    request = CreateImageRequest.from_dict({"source": variants["registry"], "name": "py"})
    assert request.to_dict() == {"source": variants["registry"], "name": "py"}

    image = Image.from_dict(
        {
            "anonymous": True,
            "region_ids": ["region-b"],
            "class": "linux",
            "created_at": "2026-08-24T00:00:00Z",
            "format_outdated": False,
            "format_version": 2,
            "id": "img_5",
            "origin": "registry",
            "region_id": "region-b",
            "retention": "idle",
            "status": "ready",
            "platform": False,
            "default": False,
            "deprecated": False,
            "updated_at": "2026-08-24T00:00:00Z",
        }
    )
    assert '"created_at": "2026-08-24T00:00:00+00:00"' in image.to_json()


@pytest.mark.anyio
async def test_generated_models_reject_unknown_values_only_where_they_discriminate() -> None:
    """Forward-compatibility invariant on the generated models: a strict
    inline-enum validator (which raises on a value this SDK does not know) may
    exist only on `oneOf` variant classes, where the `type` field is how the
    wrapper picks the variant. Every other server-reported vocabulary must be
    an open enum (unknown values become a pseudo-member) or a plain string."""
    import re
    from pathlib import Path

    models = Path(__file__).resolve().parents[1] / "src" / "daytona" / "v2" / "_gen"
    strict = sorted(
        str(path.relative_to(models))
        for path in models.rglob("*.py")
        if re.search(r"def \w+_validate_enum\(", path.read_text())
    )
    assert all("_one_of" in name for name in strict), strict


_SEARCH_BASE = "https://api.example.com"


def _search_client() -> AsyncDaytona:
    return AsyncDaytona(_config_at(_SEARCH_BASE, api_key="key"))


@respx.mock
@pytest.mark.anyio
async def test_search_encodes_every_parameter_and_tolerates_unknown_response_fields():
    route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [],
                "next_cursor": None,
                "total": 0,
                "total_relation": "eq",
                "future_server_field": {"version": 2},
            },
        )
    )
    client = _search_client()
    try:
        await client.search(
            q="build",
            name_prefix="ci-",
            id_prefix="sbx-",
            states=["started", "stopped", "error"],
            sandbox_class=["linux", "macos"],
            region=["region-b", "region-c"],
            image_ref="python:3.12",
            image_ref_prefix="python:",
            labels={"team": "ml", "tier": "gold"},
            spot=False,
            forked_from="sbx-parent",
            min_cores=0.5,
            max_cores=8,
            min_memory_mib=512,
            max_memory_mib=16384,
            min_disk_mib=1024,
            max_disk_mib=65536,
            created_start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            created_end_at="2026-02-01T00:00:00Z",
            updated_start_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).replace(
                tzinfo=None
            ),  # naive on purpose: taken as UTC
            updated_end_at=datetime(2026, 1, 3, 1, 0, 0, tzinfo=timezone(timedelta(hours=1))),
            last_activity_start_at="2026-01-04T00:00:00Z",
            last_activity_end_at="2026-01-05T00:00:00Z",
            sort="memory_mib",
            order="asc",
            limit=25,
            cursor="abc",
        )
    finally:
        await client.close()

    assert route.call_count == 1
    params = dict(route.calls[0].request.url.params.items())
    assert params == {
        "q": "build",
        "name_prefix": "ci-",
        "id_prefix": "sbx-",
        "status": "running,stopped,failed,lost",
        "class": "linux,macos",
        "region_ids": "region-b,region-c",
        "image_ref": "python:3.12",
        "image_ref_prefix": "python:",
        "labels": '{"team":"ml","tier":"gold"}',
        "spot": "false",
        "forked_from": "sbx-parent",
        "min_cores": "0.5",
        "max_cores": "8",
        "min_memory_mib": "512",
        "max_memory_mib": "16384",
        "min_disk_mib": "1024",
        "max_disk_mib": "65536",
        "created_start_at": "2026-01-01T00:00:00Z",
        "created_end_at": "2026-02-01T00:00:00Z",
        "updated_start_at": "2026-01-02T03:04:05Z",
        "updated_end_at": "2026-01-03T00:00:00Z",
        "last_activity_start_at": "2026-01-04T00:00:00Z",
        "last_activity_end_at": "2026-01-05T00:00:00Z",
        "sort": "memory_mib",
        "order": "asc",
        "limit": "25",
        "cursor": "abc",
    }


def test_list_and_search_options_are_keyword_only_and_list_has_no_filters():
    query = ListSandboxesOptions(cursor="c", limit=10)
    assert (query.cursor, query.limit) == ("c", 10)
    assert [f.name for f in fields(ListSandboxesOptions)] == ["cursor", "limit"]
    assert all(f.kw_only for f in fields(SandboxSearchFilters))
    with pytest.raises(TypeError):
        ListSandboxesOptions("c", 10)  # type: ignore[misc]

    # ListResult is keyword-only and not iterable: `.items` is the one way in.
    result: ListResult[str] = ListResult(items=["a"], next_cursor="next")
    assert (result.items, result.next_cursor) == (("a",), "next")
    with pytest.raises(TypeError):
        ListResult(["a"], "next")  # type: ignore[misc]
    with pytest.raises(TypeError):
        iter(result)  # type: ignore[call-overload]
    assert not hasattr(result, "total") and not hasattr(result, "total_relation")

    # SearchSandboxesOptions is fully keyword-only.
    with pytest.raises(TypeError):
        SearchSandboxesOptions("q")  # type: ignore[misc]


def test_search_params_omits_unset_fields_and_rejects_comma_entries():
    assert search_params(SearchSandboxesOptions()) == {}
    assert search_params(SearchSandboxesOptions(states=["started"], limit=5)) == {
        "status": "running",
        "limit": 5,
    }
    assert search_timestamp("2026-01-01T00:00:00+02:00") == "2026-01-01T00:00:00+02:00"
    naive = datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=timezone.utc).replace(tzinfo=None)  # taken as UTC
    assert search_timestamp(naive) == "2026-01-01T12:00:00.500000Z"
    with pytest.raises(BadRequestError, match="without commas"):
        search_params(SearchSandboxesOptions(region=["a,b"]))
    # An empty list / mapping is not "no filter" — the server would read the
    # empty parameter as absent and answer an unfiltered search.
    with pytest.raises(BadRequestError, match="at least one sandbox state"):
        search_params(SearchSandboxesOptions(states=[]))
    with pytest.raises(BadRequestError, match="at least one value"):
        search_params(SearchSandboxesOptions(region=[]))
    with pytest.raises(BadRequestError, match="at least one pair"):
        search_params(SearchSandboxesOptions(labels={}))


@pytest.mark.parametrize(
    ("states", "wire"),
    [
        ("queued", "queued"),
        ("creating", "pending,assigned"),
        ("starting", "booting"),
        ("started", "running"),
        ("pausing", "pausing"),
        ("paused", "paused"),
        ("stopping", "stopping"),
        ("stopped", "stopped"),
        ("destroying", "deleting"),
        ("destroyed", "deleted"),
        ("error", "failed,lost"),
        (["started", "creating", "started"], "running,pending,assigned"),
    ],
)
def test_search_states_are_sent_as_the_api_status_names(states: object, wire: str) -> None:
    assert search_params(SearchSandboxesOptions(states=states)) == {"status": wire}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "states",
    ["unknown", ["started", "unknown"], ["unknown", "started"], ["started", "running"], [7], "future-state"],
)
def test_search_refuses_states_the_sdk_does_not_define(states: object) -> None:
    with pytest.raises(BadRequestError, match="known sandbox states"):
        search_params(SearchSandboxesOptions(states=states))  # type: ignore[arg-type]


@respx.mock
@pytest.mark.anyio
async def test_search_parses_summary_and_tolerates_unknown_fields():
    respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [
                    {
                        **_sandbox_detail_payload("sbx-1", "running", name="ci-1"),
                        "updated_at": "2026-01-01T00:00:00Z",
                        "last_activity_at": None,
                        "started_at": "2026-01-01T00:00:00Z",
                        "stopped_at": None,
                        "forked_from": None,
                        "cursor": "item-cursor",
                        "toolbox_proxy_url": "https://proxy.search.example/sandboxes",
                        "future_field": {"nested": True},
                    }
                ],
                "next_cursor": "page-2",
                "total": 10000,
                "total_relation": "gte",
                "future_top_level": 1,
            },
        )
    )
    client = _search_client()
    try:
        page = await client.search(states=["started"])
    finally:
        await client.close()

    assert isinstance(page, SearchResult)
    assert [sandbox.id for sandbox in page.items] == ["sbx-1"]
    assert page.items[0].name == "ci-1"
    assert page.items[0].state == "started"
    assert page.next_cursor == "page-2"
    assert page.total == 10000
    assert page.total_relation == "gte"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"sandboxes": [], "total_relation": "eq"}, "search response is missing its total"),
        (
            {"sandboxes": [], "total": 1.5, "total_relation": "eq"},
            "search response has a total that is not an integer",
        ),
        (
            {"sandboxes": [], "total": 0},
            "search response is missing its total_relation",
        ),
        (
            {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": 7},
            "search response has a next_cursor that is not a string",
        ),
    ],
)
async def test_search_refuses_a_page_without_its_summary_fields(
    body: dict[str, object], message: str
) -> None:
    # The contract requires total and total_relation; a mistyped cursor would end
    # search() / find_by_name() early. As in TypeScript, each is a contract error.
    with respx.mock:
        respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(return_value=httpx.Response(200, json=body))
        client = _search_client()
        try:
            with pytest.raises(BadRequestError, match=f"^{message}$") as raised:
                await client.search()
        finally:
            await client.close()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


# Search responses require a sandboxes array of objects. Invalid shapes raise
# BadRequestError rather than returning an empty page; extra fields remain tolerated.
@respx.mock
@pytest.mark.anyio
async def test_search_rejects_response_without_sandboxes_array_matches_typescript():
    for body in (
        {"items": [{"id": "sbx", "status": "running"}]},
        {"sandboxes": "sbx", "total": 0, "total_relation": "eq"},
        {"sandboxes": None, "total": 0, "total_relation": "eq"},
        {},
        {"sandboxes": {"id": "sbx"}},
    ):
        with pytest.raises(BadRequestError, match="search response is missing the sandboxes array"):
            parse_search_page(body, expect_sandbox_detail)
    for body in ([{"id": "sbx"}], "sandboxes", None):
        with pytest.raises(BadRequestError, match="search response is not a JSON object"):
            parse_search_page(body, expect_sandbox_detail)
    item = {**_sandbox_detail_payload("sbx", "running"), "future_field": 1}
    with pytest.raises(BadRequestError, match="expected sandbox detail response"):
        parse_search_page(
            {"sandboxes": [item, "sbx-2"], "total": 2, "total_relation": "eq"}, expect_sandbox_detail
        )
    # Each item is a sandbox, checked as a detail is: no id is a contract error.
    without_id = {k: v for k, v in item.items() if k != "id"}
    with pytest.raises(BadRequestError, match="id"):
        parse_search_page(
            {"sandboxes": [without_id], "total": 1, "total_relation": "eq"}, expect_sandbox_detail
        )
    summary = {"total": 1, "total_relation": "eq", "future_top_level": True}
    assert parse_search_page({"sandboxes": [item], **summary}, expect_sandbox_detail) == (
        [item],
        None,
        1,
        "eq",
        "eq",
    )
    assert parse_search_page(
        {"sandboxes": [], "total": 0, "total_relation": "future"}, expect_sandbox_detail
    )[3:] == (
        "unknown",
        "future",
    )
    page = {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": "page-2"}
    assert parse_search_page(page, expect_sandbox_detail)[1] == "page-2"

    route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "sbx", "status": "running"}]})
    )
    client = _search_client()
    try:
        with pytest.raises(
            BadRequestError, match="search response is missing the sandboxes array"
        ) as excinfo:
            await client.search(q="build")
        assert excinfo.value.source == "SDK"
        with pytest.raises(BadRequestError, match="search response is missing the sandboxes array"):
            await client.search(states=["started"])
        route.mock(
            return_value=httpx.Response(
                200,
                json={
                    "sandboxes": [
                        _sandbox_detail_payload(
                            "sbx", "running", toolbox_proxy_url="https://proxy.search.example/sandboxes"
                        )
                    ],
                    "total": 1,
                    "total_relation": "eq",
                    "future_field": {"nested": True},
                },
            )
        )
        assert [sandbox.id for sandbox in (await client.search(q="build")).items] == ["sbx"]
    finally:
        await client.close()
    assert route.call_count == 3


# Timestamp-bound parity fixture — the SAME values are pinned in
# sdk-typescript/tests/unit.test.ts ("rejects an invalid Date and non-timestamp
# bounds with BadRequestError, never RangeError"): a non-``datetime``/non-``str``
# bound is the SDK's own BadRequestError (never an ``AttributeError`` from the
# formatter) and no request goes out. TS additionally pins an invalid ``Date``,
# which has no Python analogue (a ``datetime`` cannot be NaN).
@respx.mock
@pytest.mark.anyio
async def test_search_timestamp_rejects_non_timestamp_values_matches_typescript():
    route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(200, json={"sandboxes": [], "total": 0, "total_relation": "eq"})
    )
    client = _search_client()
    try:
        for value in (
            1700000000,
            1700000000.5,
            ["2026-01-01T00:00:00Z"],
            {"iso": "2026-01-01T00:00:00Z"},
            True,
        ):
            with pytest.raises(
                BadRequestError,
                match="timestamp bounds must be a datetime or an RFC 3339 timestamp without a leap second",
            ):
                search_timestamp(value)  # type: ignore[arg-type]
            with pytest.raises(
                BadRequestError,
                match="timestamp bounds must be a datetime or an RFC 3339 timestamp without a leap second",
            ):
                search_params(SearchSandboxesOptions(created_start_at=value))  # type: ignore[arg-type]
            with pytest.raises(
                BadRequestError,
                match="timestamp bounds must be a datetime or an RFC 3339 timestamp without a leap second",
            ) as excinfo:
                await client.search(last_activity_end_at=value)  # type: ignore[arg-type]
            assert excinfo.value.source == "SDK"
        # Valid bounds still go out: an aware datetime is normalized to UTC ``Z``.
        await client.search(
            created_start_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))),
            created_end_at="2026-02-01T00:00:00Z",
        )
    finally:
        await client.close()
    assert route.call_count == 1
    assert dict(route.calls[0].request.url.params) == {
        "created_start_at": "2026-01-01T00:00:00Z",
        "created_end_at": "2026-02-01T00:00:00Z",
    }


# Input-boundary parity fixture — the SAME values are pinned in
# sdk-typescript/tests/unit.test.ts ("handles the input-boundary fixture
# exactly like the Python SDK"): (a) a bare string for a list filter is one
# entry, (b) a non-mapping ``labels`` and a non-string list entry are the SDK's
# own BadRequestError with no request sent.
@respx.mock
@pytest.mark.anyio
async def test_search_input_boundary_matches_typescript():
    route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [],
                "next_cursor": None,
                "total": 0,
                "total_relation": "eq",
            },
        )
    )

    # (a) a single value is one entry, never split into characters — ``Sequence[str]``
    # includes ``str``, and ``sandbox_class`` accepts either a class or a sequence,
    # so a bare ``"linux"`` type-checks too.
    assert search_params(SearchSandboxesOptions(states="started", sandbox_class="linux", region="local")) == {
        "status": "running",
        "class": "linux",
        "region_ids": "local",
    }
    assert search_params(SearchSandboxesOptions(region=["region-a", "region-b"])) == {
        "region_ids": "region-a,region-b"
    }

    client = _search_client()
    try:
        # (b) client-side rejections, none of them reach the transport.
        for labels in ("team=ml", ["team=ml"], 42):
            with pytest.raises(BadRequestError, match="labels must be a mapping"):
                search_params(SearchSandboxesOptions(labels=labels))  # type: ignore[arg-type]
            with pytest.raises(BadRequestError, match="labels must be a mapping"):
                await client.search(labels=labels)  # type: ignore[arg-type]
        for region in (["region-a", 7], ["region-a", ""], ["a,b"]):
            with pytest.raises(
                BadRequestError, match="region entries must be non-empty strings without commas"
            ):
                search_params(SearchSandboxesOptions(region=region))  # type: ignore[arg-type]
            with pytest.raises(
                BadRequestError, match="region entries must be non-empty strings without commas"
            ):
                await client.search(region=region)  # type: ignore[arg-type]
        with pytest.raises(BadRequestError, match="known sandbox states"):
            await client.search(states=["started", ""])  # type: ignore[list-item]
        assert not route.called

        page = await client.search(states="started")
        assert route.calls.last.request.url.params["status"] == "running"
        assert page.total == 0
    finally:
        await client.close()


@pytest.mark.anyio
async def test_list_takes_no_filters():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    for keyword in ("id", "name", "status", "states", "labels", "sort"):
        with pytest.raises(TypeError):
            await client.list(**{keyword: "x"})  # type: ignore[arg-type]


@respx.mock
@pytest.mark.anyio
async def test_search_forwards_cursor_sort_and_order():
    search_route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(200, json={"sandboxes": [], "total": 0, "total_relation": "eq"})
    )
    client = _search_client()
    try:
        await client.search(cursor="c1", sort="name", order="asc")
    finally:
        await client.close()
    assert dict(search_route.calls[0].request.url.params.items()) == {
        "cursor": "c1",
        "sort": "name",
        "order": "asc",
    }


@respx.mock
@pytest.mark.anyio
async def test_list_is_one_plain_page_and_prefixes_are_search_filters():
    # Plain list(): one response page (iter() follows cursors), with no search totals.
    search_route = respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [_sandbox_page_payload("sbx-1", "running", name="ci-1")],
                "total": 1,
                "total_relation": "eq",
            },
        )
    )
    plain_list = respx.get(f"{_SEARCH_BASE}/sandboxes").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "sandboxes": [
                        _sandbox_page_payload("sbx-1", "running", name="ci-1"),
                        _sandbox_page_payload("other", "running", name="x"),
                    ],
                    "next_cursor": "p2",
                    "total": 0,
                    "total_relation": "eq",
                },
            ),
            httpx.Response(
                200,
                json={
                    "sandboxes": [_sandbox_page_payload("sbx-2", "running", name="ci-2")],
                    "next_cursor": None,
                    "total": 0,
                    "total_relation": "eq",
                },
            ),
        ]
    )
    client = _search_client()
    try:
        page = await client.list()
        assert search_route.call_count == 0
        assert plain_list.call_count == 1
        assert plain_list.calls[0].request.url == httpx.URL(f"{_SEARCH_BASE}/sandboxes")
        assert [sandbox.id for sandbox in page.items] == ["sbx-1", "other"]
        assert page.next_cursor == "p2"
        assert not hasattr(page, "total")

        # iter() is how you walk: it follows the cursor page by page (the mocked
        # sequence continues with page 2, so one more GET drains it).
        assert [sandbox.id async for sandbox in client.iter(cursor="p2")] == ["sbx-2"]
        assert plain_list.call_count == 2
        assert plain_list.calls[1].request.url == httpx.URL(f"{_SEARCH_BASE}/sandboxes?cursor=p2")

        # Prefixes are search filters.
        filtered = await client.search(id_prefix="sbx", name_prefix="ci")
    finally:
        await client.close()

    assert search_route.call_count == 1
    assert dict(search_route.calls[0].request.url.params.items()) == {"id_prefix": "sbx", "name_prefix": "ci"}
    assert [sandbox.id for sandbox in filtered.items] == ["sbx-1"]
    assert filtered.total == 1


@respx.mock
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (501, "SEARCH_NOT_CONFIGURED", SearchNotConfiguredError),
        (503, "SEARCH_UNAVAILABLE", SearchUnavailableError),
    ],
)
@pytest.mark.anyio
async def test_search_maps_not_configured_and_unavailable_to_status_errors(
    status: int, code: str, expected: type
):
    respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            status,
            json={
                "code": code,
                "message": code,
                "source": "DAYTONA_API",
                "trace_id": "0af7651916cd43dd8448eb211c80319c",
            },
        )
    )
    client = _search_client()
    try:
        with pytest.raises(expected) as info:
            await client.search(q="x")
        with pytest.raises(expected):
            await client.search(states=["started"])
    finally:
        await client.close()
    assert info.value.status_code == status
    assert info.value.code == code
    assert str(info.value) == code
    assert isinstance(info.value, InternalServerError) is (status == 501)


@respx.mock
@pytest.mark.anyio
async def test_search_mismatched_cursor_is_a_bad_request():
    respx.get(f"{_SEARCH_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(
            400,
            json={
                "code": "BAD_REQUEST",
                "message": "cursor does not match this query",
                "source": "DAYTONA_API",
                "trace_id": "0af7651916cd43dd8448eb211c80319c",
            },
        )
    )
    client = _search_client()
    try:
        with pytest.raises(BadRequestError, match="cursor does not match") as info:
            await client.search(cursor="stale", sort="name")
    finally:
        await client.close()
    assert info.value.code == "BAD_REQUEST"


def test_sync_transport_blocks_server_cookies() -> None:
    seen_cookies: list[str | None] = []
    client = httpx.Client(transport=httpx.MockTransport(_cookie_echo_handler(seen_cookies)))
    transport = SyncTransport("http://api.invalid", "token", client=client)

    try:
        assert transport.send(RequestSpec("GET", "/one")) == {"ok": True}
        assert transport.send(RequestSpec("GET", "/two")) == {"ok": True}
    finally:
        client.close()

    assert seen_cookies == [None, None]


@pytest.mark.anyio
async def test_daytona_create_sends_the_caller_idempotency_key_verbatim_and_mints_one_when_omitted():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "sbx", "status": "running", "wait_cap_seconds": 3},
            {"id": "a", "status": "running", "wait_cap_seconds": 3},
            {"id": "b", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    await client.create(Image.base("python:3.12"), idempotency_key="run-42/attempt-1")
    await client.create(Image.base("python:3.12"))
    await client.create(Image.base("python:3.12"))

    posts = [spec for spec in api.specs if spec.method == "POST"]
    assert posts[0].headers == {"Idempotency-Key": "run-42/attempt-1"}
    assert posts[0].json_body is not None and "idempotency_key" not in posts[0].json_body
    minted = [spec.headers["Idempotency-Key"] for spec in posts[1:] if spec.headers is not None]
    assert len(minted) == 2 and minted[0] and minted[0] != minted[1]


@pytest.mark.anyio
async def test_daytona_create_forwards_the_idempotency_key_verbatim_and_only_type_checks_it():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "sbx", "status": "running", "wait_cap_seconds": 3}])
    bind_api(client, api)

    with pytest.raises(BadRequestError) as exc_info:
        await client.create(Image.base("python:3.12"), idempotency_key=42)  # type: ignore[arg-type]
    assert exc_info.value.source == "SDK"
    assert api.specs == []

    # Length rules are the server's (400), not the SDK's: the key is the
    # create's identity and goes out exactly as given (the SDK refuses only a
    # header value no client sends as-is: padded, blank or not printable ASCII).
    await client.create(Image.base("python:3.12"), idempotency_key="k  k" * 100)
    assert api.specs[0].headers == {"Idempotency-Key": "k  k" * 100}


@pytest.mark.anyio
async def test_daytona_create_surfaces_the_idempotency_key_when_the_post_itself_fails_in_flight():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            ClientTimeoutError("operation timed out"),
            ConnectionError("Server disconnected without sending a response."),
            BadRequestError("bad image", status_code=400, code="BAD_REQUEST"),
        ]
    )
    bind_api(client, api)

    with pytest.raises(SandboxCreateRequestTimeoutError) as timed_out:
        await client.create(Image.base("python:3.12"), idempotency_key="run-42")
    assert isinstance(timed_out.value, ClientTimeoutError)
    assert timed_out.value.idempotency_key == "run-42"
    assert (timed_out.value.status_code, timed_out.value.code, timed_out.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    assert "run-42" in str(timed_out.value)
    assert isinstance(timed_out.value.__cause__, ClientTimeoutError)

    with pytest.raises(SandboxCreateConnectionError) as dropped:
        await client.create(Image.base("python:3.12"))
    assert isinstance(dropped.value, ConnectionError)
    minted_key = api.specs[1].headers["Idempotency-Key"] if api.specs[1].headers is not None else None
    assert dropped.value.idempotency_key == minted_key
    assert "Server disconnected" in str(dropped.value)

    # A server-side rejection is a known outcome and passes through untouched.
    with pytest.raises(BadRequestError) as rejected:
        await client.create(Image.base("python:3.12"))
    assert type(rejected.value) is BadRequestError
    assert [spec.method for spec in api.specs] == ["POST", "POST", "POST"]


@pytest.mark.anyio
async def test_daytona_create_retried_with_the_surfaced_idempotency_key_replays_the_same_sandbox():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            ClientTimeoutError("operation timed out"),
            {"id": "sbx-first", "access_token": None, "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    with pytest.raises(SandboxCreateRequestTimeoutError) as exc_info:
        await client.create(Image.base("python:3.12"))
    sandbox = await client.create(Image.base("python:3.12"), idempotency_key=exc_info.value.idempotency_key)

    assert sandbox.id == "sbx-first"
    assert api.specs[1].json_body == api.specs[0].json_body
    assert api.specs[0].headers == api.specs[1].headers == {"Idempotency-Key": exc_info.value.idempotency_key}


# --- sandbox.docker ----------------------------------------------------------


def _docker_status(state: str, **extra: object) -> dict[str, object]:
    return {"state": state, "owner": "other", **extra}


@pytest.mark.anyio
async def test_docker_namespace_is_cached_on_the_sandbox() -> None:
    transport = RecordingTransport([_docker_status("ready")])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, transport)  # type: ignore[arg-type]
    assert isinstance(sandbox.docker, AsyncDockerService)
    assert sandbox.docker is sandbox.docker
    assert (await sandbox.docker.status()).state == "ready"


@pytest.mark.anyio
async def test_docker_status_reads_the_status_route_without_waiting() -> None:
    transport = RecordingTransport([_docker_status("failed", reason="dockerd exited", processId="p-1")])
    status = await AsyncDockerService(transport).status()  # type: ignore[arg-type]
    assert status.state == "failed"
    assert status.raw_state == "failed"
    assert status.raw_owner == "other"
    assert status.reason == "dockerd exited"
    assert status.process_id == "p-1"
    assert [(spec.method, spec.path, spec.params) for spec in transport.specs] == [
        ("GET", "/docker/status", {})
    ]


@pytest.mark.anyio
async def test_docker_wait_until_ready_long_polls_through_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += 1.0
            return await super().send(spec)

    transport = Advancing(
        [_docker_status("starting", processId="p-1"), _docker_status("ready", processId="p-1")]
    )
    status = await AsyncDockerService(transport).wait_until_ready(timeout=10)  # type: ignore[arg-type]
    assert status.state == "ready"
    assert status.process_id == "p-1"
    assert [(spec.path, spec.params, spec.timeout) for spec in transport.specs] == [
        ("/docker/status", {"waitMs": 1000}, 6.0),
        ("/docker/status", {"waitMs": 1000}, 6.0),
    ]


@pytest.mark.anyio
async def test_docker_wait_until_ready_spaces_early_answers_and_keeps_polling_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("daytona.v2.docker.asyncio.sleep", fake_sleep)
    transport = RecordingTransport(
        [_docker_status("deferred", owner="none", reason="deferred"), _docker_status("ready")]
    )
    status = await AsyncDockerService(transport).wait_until_ready()  # type: ignore[arg-type]
    assert status.state == "ready"
    assert sleeps == [0.5]
    assert len(transport.specs) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["failed", "not_present", "disabled", "unmanaged", "unsupported"])
async def test_docker_wait_until_ready_raises_on_terminal_states(state: str) -> None:
    transport = RecordingTransport([_docker_status(state, reason="why")])
    with pytest.raises(DockerNotReadyError) as info:
        await AsyncDockerService(transport).wait_until_ready(timeout=5)  # type: ignore[arg-type]
    err = info.value
    assert isinstance(err, ConflictError)
    assert (err.status_code, err.code, err.source) == (409, "CONFLICT", "SDK")
    assert err.status.state == state
    assert err.status.reason == "why"
    assert len(transport.specs) == 1


@pytest.mark.anyio
async def test_docker_wait_until_ready_times_out_with_the_last_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += 1.0
            return await super().send(spec)

    transport = Advancing([_docker_status("starting"), _docker_status("starting")])
    with pytest.raises(DockerWaitTimeoutError) as info:
        await AsyncDockerService(transport).wait_until_ready(timeout=1.5)  # type: ignore[arg-type]
    err = info.value
    assert isinstance(err, RequestTimeoutError)
    assert (err.status_code, err.code, err.source) == (408, "REQUEST_TIMEOUT", "SDK")
    assert err.status is not None and err.status.state == "starting"
    # The second poll holds only for what is left of the budget.
    assert [spec.params for spec in transport.specs] == [{"waitMs": 1000}, {"waitMs": 500}]


@pytest.mark.anyio
async def test_docker_wait_until_ready_timeout_message_uses_the_raw_wire_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += 1.0
            return await super().send(spec)

    transport = Advancing([_docker_status("paused"), _docker_status("paused")])
    with pytest.raises(DockerWaitTimeoutError, match=r"last state: paused"):
        await AsyncDockerService(transport).wait_until_ready(timeout=1.5)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_docker_wait_until_ready_caps_request_timeouts_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += 1.0
            return await super().send(spec)

    transport = Advancing([_docker_status("starting"), _docker_status("starting")])
    with pytest.raises(DockerWaitTimeoutError):
        await AsyncDockerService(transport).wait_until_ready(timeout=1.5)  # type: ignore[arg-type]
    assert [spec.timeout for spec in transport.specs] == [
        pytest.approx(1.5 + DOCKER_WAIT_DEADLINE_GRACE_SECONDS),
        pytest.approx(0.5 + DOCKER_WAIT_DEADLINE_GRACE_SECONDS),
    ]


@pytest.mark.anyio
async def test_docker_wait_until_ready_maps_a_transport_timeout_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += spec.timeout if isinstance(self.responses[0], Exception) else 1.0
            return await super().send(spec)

    stalled = ClientTimeoutError("operation timed out")
    transport = Advancing([_docker_status("starting"), stalled])
    with pytest.raises(DockerWaitTimeoutError) as info:
        await AsyncDockerService(transport).wait_until_ready(timeout=1.5)  # type: ignore[arg-type]
    assert info.value.status is not None and info.value.status.state == "starting"
    assert info.value.__cause__ is stalled


@pytest.mark.anyio
async def test_docker_wait_until_ready_reraises_a_transport_timeout_before_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])

    class Advancing(RecordingTransport):
        async def send(self, spec: RequestSpec):
            clock["now"] += 6.0
            return await super().send(spec)

    stalled = ClientTimeoutError("operation timed out")
    with pytest.raises(ClientTimeoutError) as info:
        await AsyncDockerService(Advancing([stalled])).wait_until_ready(timeout=60)  # type: ignore[arg-type]
    assert info.value is stalled


@pytest.mark.anyio
async def test_docker_wait_until_ready_against_a_stalling_server_ends_near_the_deadline() -> None:
    import socket
    import time

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    done = threading.Event()
    held: list[socket.socket] = []

    def serve() -> None:
        # Accept and read without returning a response.
        while not done.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            held.append(conn)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    transport = AsyncTransport(f"http://127.0.0.1:{port}")
    try:
        started = time.monotonic()
        with pytest.raises(DockerWaitTimeoutError) as info:
            await AsyncDockerService(transport).wait_until_ready(timeout=1.0)
        overshoot = time.monotonic() - started - 1.0
        assert info.value.status is None
        assert (info.value.status_code, info.value.code) == (408, "REQUEST_TIMEOUT")
        assert 0 <= overshoot <= 0.5
    finally:
        done.set()
        server.join(timeout=2)
        for conn in held:
            conn.close()
        listener.close()
        await transport.close()


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf")])
async def test_docker_wait_until_ready_validates_timeout(timeout: float) -> None:
    with pytest.raises(BadRequestError, match="timeout must be a non-negative finite number"):
        await AsyncDockerService(RecordingTransport([])).wait_until_ready(timeout=timeout)  # type: ignore[arg-type]


def test_sandbox_reads_cpu_guarantee_and_fractional_cores_max() -> None:
    """``cpu`` is the wire guarantee and ``cpu_max`` the wire ``cores_max``, both in cores."""
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    payload = {**_sandbox_detail_payload("sbx", "running", cpu=0.25, cores_max=1.5)}
    for sandbox_payload in (payload, SandboxDto.from_dict(payload)):
        sandbox = client._sandbox_from_sandbox(sandbox_payload)
        assert (sandbox.cpu, sandbox.cpu_max) == (0.25, 1.5)
    assert client._sandbox_from_sandbox({**payload, "cores_max": None}).cpu_max is None


def test_sandbox_to_json_is_the_camel_case_handle_json_without_urls_or_credentials() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = client._sandbox_from_sandbox(
        {
            **_sandbox_detail_payload(
                "sbx",
                "running",
                toolbox_proxy_url="https://ignored.example.com/sandboxes",
                persistent=True,
                sandbox_class="macos",
                memory_mib=8192,
                disk_mib=16384,
            ),
            "image_build_id": "img-build-1",
            "labels": {"team": "sdk"},
            "cores_max": 8,
            "memory_max_mib": 12288,
            "disk_max_mib": 32768,
            "spot": True,
            "spot_evicted_at": "2026-09-01T00:00:00Z",
            "lifecycle_policy": {
                "idle_expiry_seconds": 120,
                "on_idle_timeout": "pause",
                "idle_timeout_at": "2026-08-27T09:15:00Z",
            },
            "forked_from": "parent",
            "user": "daytona",
            "error": "boot failed once",
        },
        toolbox_url="https://tb.example.com/sandboxes/{sandbox_id}",
        access_token="sandbox-token",
    )

    serialized = sandbox.to_json()
    # The exact key set the TypeScript SDK's toJSON() writes for the same response.
    assert serialized == {
        "sandboxId": "sbx",
        "name": "sbx",
        "state": "started",
        "rawState": "running",
        "image": {"id": "img_py", "name": "python:3.12"},
        "region": "region-b",
        "labels": {"team": "sdk"},
        "cpu": 1.0,
        "cpuMax": 8.0,
        "memory": 8.0,
        "memoryMax": 12.0,
        "disk": 16.0,
        "diskMax": 32.0,
        "sandboxClass": "macos",
        "rawSandboxClass": "macos",
        "persistent": True,
        "spot": True,
        "spotEvictedAt": "2026-09-01T00:00:00Z",
        "lifecyclePolicy": {
            "idleExpirySeconds": 120,
            "onIdleTimeout": "pause",
            "idleTimeoutAt": "2026-08-27T09:15:00Z",
        },
        "forkedFrom": "parent",
        "user": "daytona",
        "error": "boot failed once",
        "imageBuildId": "img-build-1",
    }
    text = json.dumps(serialized)
    assert "sandbox-token" not in text
    assert "tb.example.com" not in text and "ignored.example.com" not in text


def test_sandbox_to_json_writes_only_known_keys_for_a_handle_without_metadata() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx")
    assert sandbox.to_json() == {"sandboxId": "sbx"}


@pytest.mark.anyio
async def test_sandbox_entrypoint_logs_reads_plain_text() -> None:
    transport = RecordingTransport([b"boot\nready\n"])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), transport, None)

    assert await sandbox.entrypoint_logs() == "boot\nready\n"
    assert transport.specs == [RequestSpec("GET", "/sandboxes/sbx/logs")]


def test_daytona_config_rejects_jwt_without_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DAYTONA_API_KEY", "DAYTONA_JWT_TOKEN", "DAYTONA_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BadRequestError, match="jwt_token requires project_id"):
        AsyncDaytona(DaytonaConfig(jwt_token="jwt"))


def test_daytona_config_rejects_api_key_and_jwt_together() -> None:
    with pytest.raises(BadRequestError, match="exactly one of api_key or jwt_token"):
        AsyncDaytona(
            DaytonaConfig(
                api_key="key",
                jwt_token="jwt",
                project_id="project-1",
            )
        )


def test_daytona_config_requires_api_key_or_jwt_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DAYTONA_API_KEY", "DAYTONA_JWT_TOKEN", "DAYTONA_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AuthenticationError, match="api_key or jwt_token is required"):
        AsyncDaytona(DaytonaConfig())


def test_daytona_config_explicit_credential_wins_over_the_other_env_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
    monkeypatch.setenv("DAYTONA_JWT_TOKEN", "env-jwt")
    jwt_client = AsyncDaytona(DaytonaConfig(jwt_token="jwt", project_id="project-1"))
    key_client = AsyncDaytona(DaytonaConfig(api_key="key"))
    try:
        assert jwt_client._api._auth is not None
        assert key_client._api._auth is None
        assert key_client._api.token == "key"
    finally:
        asyncio.run(jwt_client.close())
        asyncio.run(key_client.close())
    with pytest.raises(DaytonaError, match="exactly one"):
        AsyncDaytona(DaytonaConfig(api_key="key", jwt_token="jwt", project_id="p"))
    with pytest.raises(DaytonaError, match="exactly one"):
        AsyncDaytona(DaytonaConfig())


@pytest.mark.parametrize(
    "config",
    [
        DaytonaConfig(api_key=""),
        DaytonaConfig(api_key="key", jwt_token="", project_id="project-1"),
        DaytonaConfig(jwt_token="", project_id="project-1"),
        DaytonaConfig(api_key="key", project_id=""),
    ],
)
def test_daytona_config_rejects_explicitly_empty_values_instead_of_reading_the_environment(
    monkeypatch: pytest.MonkeyPatch, config: DaytonaConfig
) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
    monkeypatch.setenv("DAYTONA_JWT_TOKEN", "env-jwt")
    monkeypatch.setenv("DAYTONA_PROJECT_ID", "env-project")
    with pytest.raises(BadRequestError, match="must be a non-empty string when provided") as excinfo:
        AsyncDaytona(config)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


def test_daytona_config_treats_blank_env_jwt_as_absent_when_api_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_JWT_TOKEN", "")
    client = AsyncDaytona(DaytonaConfig(api_key="key", project_id="project-1"))

    try:
        assert client._api._auth is None
        assert client._toolbox._auth is None
        assert client._api.token == "key"
    finally:
        asyncio.run(client.close())


@pytest.mark.anyio
async def test_jwt_client_hub_mints_tickets_with_the_project_token_auth() -> None:
    # A JWT-configured client has no API key: its events hub must authenticate
    # the ticket mint through the same project-token exchange, and be keyed by
    # the project, never the raw JWT.
    client = AsyncDaytona(DaytonaConfig(jwt_token="secret-jwt", project_id="project-1"))
    try:
        hub = client._acquire_hub()
        assert hub is not None
        from daytona.v2._auth import ProjectTokenAuth as _PTA

        assert isinstance(hub._api._auth, _PTA) and hub._api._auth is not client._api._auth
        from daytona.v2 import _events

        keys = [k[1] for k in _events._HUB_REGISTRY if _events._HUB_REGISTRY[k] is hub]
        assert keys and "jwt:project-1" in repr(keys[0]) and "secret-jwt" not in repr(keys)

        # Another user's JWT for the same project gets its own hub (its own
        # credentials); the same JWT shares.
        other = AsyncDaytona(DaytonaConfig(jwt_token="other-jwt", project_id="project-1"))
        same = AsyncDaytona(DaytonaConfig(jwt_token="secret-jwt", project_id="project-1"))
        try:
            assert other._acquire_hub() is not hub
            assert same._acquire_hub() is hub
        finally:
            await other.close()
            await same.close()
    finally:
        await client.close()


def test_daytona_jwt_constructor_shares_one_project_token_auth_object() -> None:
    client = AsyncDaytona(DaytonaConfig(jwt_token="jwt", project_id="project-1"))

    try:
        assert isinstance(client._api._auth, ProjectTokenAuth)
        assert client._api._auth is client._toolbox._auth
        assert client._api._auth._client is client._api._client
    finally:
        asyncio.run(client.close())


@pytest.mark.anyio
async def test_jwt_auth_exchanges_once_for_concurrent_requests(monkeypatch) -> None:
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.exchange_started = asyncio.Event()
            self.release_exchange = asyncio.Event()
            self.exchange_calls = 0
            self.list_calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/token":
                self.exchange_calls += 1
                self.exchange_started.set()
                await self.release_exchange.wait()
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "access_token": "platform-1",
                        "expires_in": 300,
                        "token_type": "Bearer",
                        "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    },
                )
            if request.url.path == "/sandboxes":
                self.list_calls += 1
                assert request.headers["authorization"] == "Bearer platform-1"
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"sandboxes": [], "total": 0, "total_relation": "eq"},
                )
            raise AssertionError(request.url.path)

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    transport = Transport()
    http = httpx.AsyncClient(transport=transport, base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    try:
        tasks = [asyncio.create_task(client.list()), asyncio.create_task(client.list())]
        await asyncio.wait_for(transport.exchange_started.wait(), timeout=1)
        transport.release_exchange.set()
        await asyncio.gather(*tasks)
    finally:
        await http.aclose()
        await client.close()

    assert (transport.exchange_calls, transport.list_calls) == (1, 2)


@pytest.mark.anyio
async def test_jwt_auth_refreshes_near_expiry_with_monotonic_clock(monkeypatch) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])
    exchange_calls = 0
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path == "/auth/token":
            exchange_calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "access_token": f"platform-{exchange_calls}",
                    "expires_in": 40,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path == "/sandboxes":
            seen_tokens.append(request.headers["authorization"])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"sandboxes": [], "total": 0, "total_relation": "eq"},
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    try:
        await client.list()
        # TTL 40 s → refresh skew min(30, 40/2) = 20 s: still cached at t=19 …
        clock["now"] = 19.0
        await client.list()
        # … and refreshed once fewer than 20 s remain.
        clock["now"] = 21.0
        await client.list()
    finally:
        await http.aclose()
        await client.close()

    assert exchange_calls == 2
    assert seen_tokens == ["Bearer platform-1", "Bearer platform-1", "Bearer platform-2"]


@pytest.mark.anyio
async def test_jwt_auth_short_ttl_still_reuses_the_token_between_requests(monkeypatch) -> None:
    # A token TTL <= 30 s must not force a fresh exchange on every request.
    clock = {"now": 0.0}
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock["now"])
    exchange_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path == "/auth/token":
            exchange_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"p-{exchange_calls}",
                    "expires_in": 10,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        return httpx.Response(200, json={"sandboxes": [], "total": 0, "total_relation": "eq"})

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]
    try:
        for _ in range(5):
            await client.list()
        clock["now"] = 4.0
        await client.list()
        assert exchange_calls == 1
        clock["now"] = 6.0  # past half of the 10 s lifetime
        await client.list()
        assert exchange_calls == 2
    finally:
        await http.aclose()
        await client.close()


@pytest.mark.anyio
@pytest.mark.parametrize("expires_in", [0, -1])
async def test_project_token_exchange_rejects_non_positive_expires_in(expires_in: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "p",
                "expires_in": expires_in,
                "token_type": "Bearer",
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
    )
    try:
        with pytest.raises(BadRequestError):
            await auth.get_token()
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_one_shot_streaming_body_is_not_replayed_on_401() -> None:
    from daytona.v2._transport import AsyncTransport

    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return httpx.Response(
            401, json={"code": "UNAUTHORIZED", "message": "expired", "source": "DAYTONA_DAEMON"}
        )

    class RefreshingAuth:
        async def bearer(self) -> str:
            return "old"

        async def on_unauthorized(self, failed_token: str | None) -> str:
            return "new"

    transport = AsyncTransport("http://toolbox.invalid", None)
    transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://toolbox.invalid"
    )  # type: ignore[attr-defined]
    transport.auth_provider = RefreshingAuth()  # type: ignore[assignment]

    async def once():
        yield b"stream-once"

    try:
        with pytest.raises(AuthenticationError):
            await transport.request("POST", "/files/upload", content=once())
    finally:
        await transport.close()
    # One send only: no silent empty second request.
    assert bodies == [b"stream-once"]


@pytest.mark.anyio
async def test_jwt_auth_retries_once_after_a_401(monkeypatch) -> None:
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    exchange_calls = 0
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path == "/auth/token":
            exchange_calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "access_token": f"platform-{exchange_calls}",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path == "/sandboxes":
            seen_tokens.append(request.headers["authorization"])
            if len(seen_tokens) == 1:
                return httpx.Response(
                    401,
                    headers={"content-type": "application/json"},
                    json={"message": "expired", "code": "UNAUTHORIZED", "source": "DAYTONA_API"},
                )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"sandboxes": [], "total": 0, "total_relation": "eq"},
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    try:
        page = await client.list()
    finally:
        await http.aclose()
        await client.close()

    assert list(page.items) == []
    assert exchange_calls == 2
    assert seen_tokens == ["Bearer platform-1", "Bearer platform-2"]


@pytest.mark.anyio
async def test_jwt_auth_mints_a_sandbox_token_for_toolbox_requests(monkeypatch) -> None:
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    exchange_calls = 0
    mint_authorization: list[str] = []
    seen_authorization: list[str] = []
    trace_headers: list[Optional[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path == "/auth/token":
            exchange_calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "access_token": "platform-toolbox",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path == "/sandboxes/sbx/access-token":
            mint_authorization.append(request.headers["authorization"])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "access_token": "sandbox-toolbox",
                    "expires_at": "2026-08-27T09:15:00Z",
                    "token_type": "Bearer",
                    "operations": ["toolbox"],
                    "ports": None,
                },
            )
        if request.url.path == "/sandboxes/sbx/system/metrics":
            seen_authorization.append(request.headers["authorization"])
            trace_headers.append(request.headers.get("traceparent"))
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=_metrics_payload(cpu_count=2)
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(
        _config_at(
            "http://api.invalid",
            jwt_token="jwt",
            project_id="project-1",
        )
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    try:
        metrics = await lazy_sandbox(client, "sbx", toolbox_url="http://api.invalid/sandboxes/sbx").metrics()
    finally:
        await http.aclose()
        await client.close()

    assert metrics.cpu_count == 2
    assert exchange_calls == 1
    assert mint_authorization == ["Bearer platform-toolbox"]
    assert seen_authorization == ["Bearer sandbox-toolbox"]
    assert trace_headers == [None]


@pytest.mark.anyio
async def test_sandbox_access_token_bypasses_platform_token_exchange(monkeypatch) -> None:
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: 0.0)
    auth_calls = 0
    seen_authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal auth_calls
        if request.url.path == "/auth/token":
            auth_calls += 1
            return httpx.Response(500)
        if request.url.path == "/sandboxes/sbx/system/metrics":
            seen_authorization.append(request.headers["authorization"])
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=_metrics_payload(cpu_count=3)
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(
        _config_at(
            "http://api.invalid",
            jwt_token="jwt",
            project_id="project-1",
        )
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    try:
        metrics = await lazy_sandbox(
            client,
            "sbx",
            toolbox_url="http://api.invalid/sandboxes/sbx",
            access_token="dtnv-token",
        ).metrics()
    finally:
        await http.aclose()
        await client.close()

    assert metrics.cpu_count == 3
    assert auth_calls == 0
    assert seen_authorization == ["Bearer dtnv-token"]


def test_otel_enabled_without_optional_dependency_raises_clear_error(monkeypatch) -> None:
    import builtins

    original_import = builtins.__import__
    removed = {name: sys.modules.pop(name) for name in list(sys.modules) if name.startswith("opentelemetry")}

    def fake_import(name: str, *args: object, **kwargs: object):
        if name.startswith("opentelemetry"):
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        with pytest.raises(BadRequestError, match=r"daytona\[otel\]"):
            AsyncDaytona(DaytonaConfig(api_key="key", otel_enabled=True))
    finally:
        sys.modules.update(removed)


@pytest.mark.anyio
async def test_otel_enabled_records_spans_and_injects_trace_headers(monkeypatch) -> None:
    import opentelemetry.trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace_api, "get_tracer", lambda *args, **kwargs: provider.get_tracer(*args, **kwargs))
    seen_trace_headers: list[str] = []

    _MINT_PAYLOAD = {
        "access_token": "otel-minted-tok",
        "token_type": "Bearer",
        "expires_at": "2030-01-01T00:00:00Z",
        "operations": ["toolbox"],
        "ports": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        trace_header = request.headers.get("traceparent")
        if trace_header is not None:
            seen_trace_headers.append(trace_header)
        if request.url.path == "/api-keys/current":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=_CURRENT_API_KEY_PAYLOAD
            )
        if request.url.path == "/sandboxes/sbx/access-token":
            return httpx.Response(200, headers={"content-type": "application/json"}, json=_MINT_PAYLOAD)
        if request.url.path == "/sandboxes/sbx/system/metrics":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=_metrics_payload(cpu_count=4)
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(
        _config_at(
            "http://api.invalid",
            api_key="key",
            otel_enabled=True,
        )
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]

    try:
        await client.current_api_key()
        await lazy_sandbox(client, "sbx", toolbox_url="http://api.invalid/sandboxes/sbx").metrics()
    finally:
        await http.aclose()
        await client.close()

    spans = exporter.get_finished_spans()
    assert len(seen_trace_headers) == 3
    assert all(header.startswith("00-") for header in seen_trace_headers)
    assert [span.name for span in spans] == [
        "daytona.get /api-keys/current",
        "daytona.post /sandboxes/{id}/access-token",
        "daytona.get /sandboxes/{id}/system/metrics",
    ]
    toolbox_span = spans[2]
    assert toolbox_span.attributes["daytona.sandbox_id"] == "sbx"
    assert toolbox_span.attributes["http.response.status_code"] == 200


@pytest.mark.anyio
async def test_open_stream_records_error_span_when_body_read_fails(monkeypatch) -> None:
    import opentelemetry.trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace.status import StatusCode

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace_api, "get_tracer", lambda *args, **kwargs: provider.get_tracer(*args, **kwargs))

    request = httpx.Request("GET", "http://api.invalid/logs")

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            raise httpx.ReadError("broken body", request=request)

        async def aclose(self) -> None:
            return None

    def handler(http_request: httpx.Request) -> httpx.Response:
        del http_request
        return httpx.Response(200, stream=BrokenBody(), request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    transport = AsyncTransport("http://api.invalid", client=http, otel=load_opentelemetry_support())

    stream = await transport.open_stream(RequestSpec("GET", "/logs"))
    try:
        with pytest.raises(ConnectionError, match="broken body"):
            async for _chunk in stream.iter_bytes():
                pass
    finally:
        await stream.close()
        await http.aclose()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "daytona.get /logs"
    assert spans[0].status.status_code is StatusCode.ERROR
    assert spans[0].attributes["http.response.status_code"] == 200


@pytest.mark.anyio
async def test_open_stream_records_ok_span_when_body_is_fully_read(monkeypatch) -> None:
    import opentelemetry.trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace.status import StatusCode

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace_api, "get_tracer", lambda *args, **kwargs: provider.get_tracer(*args, **kwargs))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"done"), request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    transport = AsyncTransport("http://api.invalid", client=http, otel=load_opentelemetry_support())

    stream = await transport.open_stream(RequestSpec("GET", "/logs"))
    try:
        assert [chunk async for chunk in stream.iter_bytes()] == [b"done"]
    finally:
        await stream.close()
        await http.aclose()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "daytona.get /logs"
    assert spans[0].status.status_code is StatusCode.OK
    assert spans[0].attributes["http.response.status_code"] == 200


def test_load_opentelemetry_support_keeps_opentelemetry_status_module_intact() -> None:
    import opentelemetry.trace as trace_api

    original_status_module = trace_api.status

    support = load_opentelemetry_support()

    assert support is not None
    assert trace_api.status is original_status_module


def test_route_template_keeps_static_segments_and_templates_resource_ids() -> None:
    assert _route_template(["sandboxes", "macos"]) == "/sandboxes/macos"
    assert (
        _route_template(["sandboxes", "sbx", "ports", "8080", "preview-url"])
        == "/sandboxes/{id}/ports/{port}/preview-url"
    )
    assert _route_template(["sandboxes", "sbx", "ports", "8080", "signed-preview-urls"]) == (
        "/sandboxes/{id}/ports/{port}/signed-preview-urls"
    )
    assert _route_template(["sandboxes", "sbx", "ports", "8080", "signed-preview-urls", "revoke"]) == (
        "/sandboxes/{id}/ports/{port}/signed-preview-urls/revoke"
    )
    # computeruse is a namespace, not an id collection; only process/<name> and recordings/<id> carry ids
    assert (
        _route_template(["computeruse", "recordings", "rec-1", "download"])
        == "/computeruse/recordings/{id}/download"
    )
    assert _route_template(["computeruse", "mouse", "click"]) == "/computeruse/mouse/click"
    assert _route_template(["computeruse", "process", "xvfb", "logs"]) == "/computeruse/process/{id}/logs"
    assert _route_template(["sandboxes", "sbx", "labels"]) == "/sandboxes/{id}/labels"
    assert _route_template(["sandboxes", "sbx", "logs"]) == "/sandboxes/{id}/logs"
    assert _route_template(["sandboxes", "search"]) == "/sandboxes/search"
    assert _route_template(["sandboxes", "from-snapshot"]) == "/sandboxes/from-snapshot"
    assert _route_template(["invitations", "accept"]) == "/invitations/accept"
    assert _route_template(["invitations", "decline"]) == "/invitations/decline"
    assert _route_template(["invitations", "lookup"]) == "/invitations/lookup"
    assert _route_template(["orgs", "org-1", "invitations", "inv-123"]) == "/orgs/{id}/invitations/{id}"
    assert _route_template(["api-keys", "current"]) == "/api-keys/current"
    assert _route_template(["projects", "project-1", "api-keys", "key-1"]) == "/projects/{id}/api-keys/{id}"
    assert _route_template(["orgs", "org-1", "members", "u-1"]) == "/orgs/{id}/members/{id}"
    assert _route_template(["port", "3999", "in-use"]) == "/port/{port}/in-use"
    assert _route_template(["images", "build-logs-url"]) == "/images/{id}"
    assert _route_template(["images", "img-1", "build-logs-url"]) == "/images/{id}/build-logs-url"
    assert _route_template(["processes", "proc-1", "logs"]) == ("/processes/{id}/logs")


def test_sandbox_id_from_segments_scans_past_api_prefixes() -> None:
    assert _sandbox_id_from_segments(["api", "v2", "sandboxes", "sbx", "labels"]) == "sbx"
    assert _sandbox_id_from_segments(["prefix", "sandboxes", "sbx", "logs"]) == "sbx"


@pytest.mark.anyio
async def test_token_exchange_finishes_span_on_base_exception() -> None:
    class SentinelCancelled(BaseException):
        pass

    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise SentinelCancelled()

    @dataclass
    class RecordingSpan:
        finished: list[BaseException | None]

        def finish(self, *, status_code: int | None = None, error: BaseException | None = None) -> None:
            self.finished.append(error)

    class RecordingOtel:
        def __init__(self, span: RecordingSpan) -> None:
            self._span = span

        def start_request_span(self, *, method: str, url: str, headers: dict[str, str]) -> RecordingSpan:
            return self._span

    span = RecordingSpan(finished=[])
    http = httpx.AsyncClient(transport=FailingTransport(), base_url="http://api.invalid")
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
        otel=RecordingOtel(span),  # type: ignore[arg-type]
    )

    try:
        with pytest.raises(SentinelCancelled):
            await auth._exchange_token()
    finally:
        await http.aclose()

    assert len(span.finished) == 1
    assert isinstance(span.finished[0], SentinelCancelled)


@pytest.mark.anyio
async def test_project_token_exchange_invalid_model_raises_typed_bad_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/token"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"access_token": 123, "expires_in": "soon"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
    )

    try:
        with pytest.raises(BadRequestError, match="token exchange response") as exc_info:
            await auth._exchange_token()
    finally:
        await http.aclose()

    assert (exc_info.value.code, exc_info.value.source) == (code_names.BAD_REQUEST, "SDK")


# The TypeScript SDK's unit.test.ts asserts the same messages for the same bodies.
_TOKEN_EXCHANGE_CASES = [
    (b"not json", "token exchange response is not a JSON object"),
    (b"[]", "token exchange response is not a JSON object"),
    (b"", "token exchange response is not a JSON object"),
    (b'{"expires_in": 300}', "token exchange response access_token must be a non-empty string"),
    (
        b'{"access_token": "", "expires_in": 300}',
        "token exchange response access_token must be a non-empty string",
    ),
    (b'{"access_token": "t"}', "token exchange response expires_in must be a positive integer"),
    (
        b'{"access_token": "t", "expires_in": 0}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": -1}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": "soon"}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": 9007199254740992}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": 1.5}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": 60.5}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": true}',
        "token exchange response expires_in must be a positive integer",
    ),
    (
        b'{"access_token": "t", "expires_in": 9007199254740992.0}',
        "token exchange response expires_in must be a positive integer",
    ),
]


@pytest.mark.anyio
async def test_project_token_exchange_reads_only_the_fields_it_uses() -> None:
    # token_type / issued_token_type are informational: neither SDK refuses an
    # exchange without them (the TypeScript SDK's unit.test.ts checks the same).
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"access_token": "t", "expires_in": 9007199254740991})
        ),
        base_url="http://api.invalid",
    )
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
    )
    try:
        exchange = await auth._exchange_token()
    finally:
        await http.aclose()
    assert (exchange.access_token, exchange.expires_in) == ("t", 9007199254740991)


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [b"60", b"60.0", b"1e2"])
async def test_project_token_exchange_takes_an_integral_float_like_typescript(raw: bytes) -> None:
    # JSON.parse cannot tell 60.0 from 60, so TypeScript reads them alike; the
    # TypeScript SDK's unit.test.ts pins the same cases.
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b'{"access_token": "t", "expires_in": ' + raw + b"}")
        ),
        base_url="http://api.invalid",
    )
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
    )
    try:
        exchange = await auth._exchange_token()
    finally:
        await http.aclose()
    assert exchange.expires_in == int(float(raw))
    assert type(exchange.expires_in) is int


@pytest.mark.anyio
@pytest.mark.parametrize(("body", "message"), _TOKEN_EXCHANGE_CASES)
async def test_project_token_exchange_names_what_is_wrong(body: bytes, message: str) -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body)),
        base_url="http://api.invalid",
    )
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
    )
    try:
        with pytest.raises(BadRequestError) as exc_info:
            await auth._exchange_token()
    finally:
        await http.aclose()
    assert str(exc_info.value) == message
    assert (exc_info.value.status_code, exc_info.value.code, exc_info.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


@pytest.mark.anyio
async def test_project_token_exchange_records_payload_parse_errors_on_span() -> None:
    class RecordingSpan:
        def __init__(self) -> None:
            self.status_codes: list[int | None] = []
            self.errors: list[BaseException | None] = []

        def finish(self, *, status_code: int | None = None, error: BaseException | None = None) -> None:
            self.status_codes.append(status_code)
            self.errors.append(error)

    class RecordingOtel:
        def __init__(self, span: RecordingSpan) -> None:
            self._span = span

        def start_request_span(self, *, method: str, url: str, headers: dict[str, str]) -> RecordingSpan:
            return self._span

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/token"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"access_token": 123, "expires_in": "soon"},
        )

    span = RecordingSpan()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    auth = ProjectTokenAuth(
        api_url="http://api.invalid",
        jwt_token="jwt",
        project_id="project-1",
        source="sdk-python-async",
        client=http,
        otel=RecordingOtel(span),  # type: ignore[arg-type]
    )

    try:
        with pytest.raises(BadRequestError, match="token exchange response"):
            await auth._exchange_token()
    finally:
        await http.aclose()

    assert span.status_codes == [200]
    assert len(span.errors) == 1
    assert isinstance(span.errors[0], BadRequestError)


@pytest.mark.anyio
async def test_connection_pool_maxsize_sets_httpx_limits() -> None:
    sync_transport = SyncTransport("http://api.invalid", connection_pool_maxsize=7)
    async_transport = AsyncTransport("http://api.invalid", connection_pool_maxsize=7)

    try:
        assert sync_transport._client._transport._pool._max_connections == 7  # type: ignore[attr-defined]
        assert sync_transport._client._transport._pool._max_keepalive_connections == 7  # type: ignore[attr-defined]
        assert async_transport._client._transport._pool._max_connections == 7  # type: ignore[attr-defined]
        assert async_transport._client._transport._pool._max_keepalive_connections == 7  # type: ignore[attr-defined]
    finally:
        sync_transport.close()
        await async_transport.close()


@pytest.mark.anyio
async def test_open_stream_finishes_span_on_cancellation_during_error_body_read() -> None:
    """CancelledError during response.aread() must finish the span."""
    finished_errors: list[BaseException | None] = []

    class CapturingSpan:
        _closed = False

        def finish(self, *, status_code: int | None = None, error: BaseException | None = None) -> None:
            if not self._closed:
                self._closed = True
                finished_errors.append(error)

    class CapturingOtel:
        def start_request_span(self, *, method: str, url: str, headers: dict[str, str]) -> CapturingSpan:
            return CapturingSpan()

    class CancelDuringBodyRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise asyncio.CancelledError()
            yield b""

        async def aclose(self) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, stream=CancelDuringBodyRead())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    transport = AsyncTransport("http://api.invalid", client=http, otel=CapturingOtel())  # type: ignore[arg-type]

    try:
        with pytest.raises(asyncio.CancelledError):
            await transport.open_stream(RequestSpec("GET", "/error"))
    finally:
        await http.aclose()

    assert len(finished_errors) == 1
    assert isinstance(finished_errors[0], asyncio.CancelledError)


@pytest.mark.anyio
async def test_open_stream_injects_traceparent_into_wire_headers(monkeypatch) -> None:
    """OTel traceparent must appear in the wire headers for streaming requests."""
    import opentelemetry.trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace_api, "get_tracer", lambda *args, **kwargs: provider.get_tracer(*args, **kwargs))

    seen_traceparents: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_traceparents.append(request.headers.get("traceparent"))
        return httpx.Response(200, content=b"streamed")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    transport = AsyncTransport("http://api.invalid", client=http, otel=load_opentelemetry_support())

    stream = await transport.open_stream(RequestSpec("GET", "/logs"))
    try:
        await stream.close()
    finally:
        await http.aclose()

    assert len(seen_traceparents) == 1
    assert seen_traceparents[0] is not None, "traceparent must be injected before stream() builds the Request"
    assert seen_traceparents[0].startswith("00-")


@pytest.mark.anyio
async def test_git_commit_only_sends_allow_empty_when_true() -> None:
    transport = RecordingTransport([{"hash": "h1"}, {"hash": "h2"}])
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    await git.commit("/repo", "msg", author="Ada", email="ada@example.com")
    await git.commit("/repo", "msg", author="Ada", email="ada@example.com", allow_empty=True)

    assert transport.specs[0] == RequestSpec(
        "POST",
        "/git/commit",
        json_body={"path": "/repo", "message": "msg", "author": "Ada", "email": "ada@example.com"},
    )
    assert transport.specs[1] == RequestSpec(
        "POST",
        "/git/commit",
        json_body={
            "path": "/repo",
            "message": "msg",
            "author": "Ada",
            "email": "ada@example.com",
            "allow_empty": True,
        },
    )


@respx.mock
@pytest.mark.anyio
async def test_one_unreadable_fallback_detail_keeps_the_page_and_caps_concurrency() -> None:
    base_url = "https://api.example.com"
    ids = [f"s{i}" for i in range(20)]
    respx.get(f"{base_url}/sandboxes").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandboxes": [_sandbox_page_payload(i, "running", toolbox_proxy_url=None) for i in ids],
                "next_cursor": None,
                "total": 0,
                "total_relation": "eq",
            },
        )
    )
    in_flight = 0
    peak = 0

    async def detail(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        sandbox_id = request.url.path.rsplit("/", 1)[-1]
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        if sandbox_id == "s3":
            return httpx.Response(404, json={"code": "NOT_FOUND", "message": "unknown sandbox"})
        return httpx.Response(
            200,
            json=_sandbox_detail_payload(
                sandbox_id, "running", toolbox_proxy_url="https://p.example/sandboxes"
            ),
        )

    respx.get(url__regex=rf"^{base_url}/sandboxes/s\d+$").mock(side_effect=detail)
    client = AsyncDaytona(_config_at(base_url, api_key="key"))
    try:
        page = await client.list()
    finally:
        await client.close()

    assert [s.id for s in page.items] == ids
    assert page.items[3]._toolbox_proxy_url is None
    assert page.items[4]._toolbox_proxy_url == "https://p.example/sandboxes/s4"
    with pytest.raises(DaytonaError, match="has no toolbox URL"):
        _ = page.items[3].process
    assert 1 < peak <= 8


def _mac_reservation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "res-1",
        "org_id": "org",
        "project_id": "proj",
        "region_id": "mac-region",
        "status": "active",
        "sandboxes_per_mac": 2,
        "model": "m4-10c-16g",
        "capacity": {"cores": 10, "memory_mib": 16384, "disk_mib": 235520},
        "resources_used": {"cores": 5, "memory_mib": 8192, "disk_mib": 117760},
        "period_n": 1,
        "period_seconds": 86400,
        "pending_mac_started_at": "2026-09-21T23:30:00Z",
        "period_started_at": "2026-09-22T00:00:00Z",
        "period_end": "2026-09-23T00:00:00Z",
        "created_at": "2026-09-22T00:00:00Z",
        "slots_used": 1,
        "renewal_pending": False,
        "renewal_error": None,
        "auto_renew": True,
        "origin": "explicit",
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_sandbox_reads_the_wire_class_first():
    # `class` alone decides the class. The contract requires it: a search item
    # without it is a malformed response, and partial internal data leaves it unknown.
    mac_payload = {**_sandbox_detail_payload("mac", "stopped"), "class": "macos"}
    running = {**mac_payload, "status": "running"}
    api = RecordingTransport(
        [
            mac_payload,
            {},
            running,
            {},
            {**mac_payload, "status": "stopped"},
            {},
            {**mac_payload, "status": "deleted"},
        ]
    )
    mac = AsyncSandbox(sandbox_info_from_id("mac"), api, None)
    await mac.start(timeout=1)
    assert mac.sandbox_class == "macos"
    await mac.stop()
    await mac.delete()
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("GET", "/sandboxes/mac"),
        ("POST", "/sandboxes/mac/start"),
        ("GET", "/sandboxes/mac"),
        ("POST", "/sandboxes/mac/stop"),
        ("GET", "/sandboxes/mac"),
        ("DELETE", "/sandboxes/mac"),
        ("GET", "/sandboxes/mac"),
    ]
    payload = {k: v for k, v in _sandbox_detail_payload("lin", "running").items() if k != "class"}
    assert AsyncSandbox._from_api(payload, None, None).sandbox_class is None
    with pytest.raises(BadRequestError, match="class"):
        parse_search_page({"sandboxes": [payload], "total": 1, "total_relation": "eq"}, expect_sandbox_detail)


@pytest.mark.anyio
async def test_macos_pause_and_resume_use_the_sandbox_routes_and_wait_through_transitions(monkeypatch):
    monkeypatch.setattr("daytona.v2.sandbox.asyncio.sleep", _no_sleep)
    mac_payload = _sandbox_detail_payload("mac", "running", sandbox_class="macos", persistent=True)
    api = RecordingTransport(
        [
            {"status": "pausing", "id": "mac"},
            {**mac_payload, "status": "pausing"},
            {**mac_payload, "status": "paused"},
            {"status": "assigned", "id": "mac"},
            {**mac_payload, "status": "assigned"},
            {**mac_payload, "status": "running", "mac_boot_mode": "restored"},
        ]
    )
    mac = AsyncSandbox._from_api(mac_payload, api, None)
    await mac.pause(timeout=10)
    assert mac.state == "paused"
    assert mac.mac_boot_mode is None
    await mac.resume(timeout=10)
    assert mac.state == "started"
    assert mac.mac_boot_mode == "restored"
    assert [(spec.method, spec.path, spec.params.get("wait")) for spec in api.specs] == [
        ("POST", "/sandboxes/mac/pause", None),
        ("GET", "/sandboxes/mac", "paused"),
        ("GET", "/sandboxes/mac", "paused"),
        ("POST", "/sandboxes/mac/resume", None),
        ("GET", "/sandboxes/mac", "running"),
        ("GET", "/sandboxes/mac", "running"),
    ]


@pytest.mark.anyio
async def test_start_resumes_a_paused_macos_sandbox():
    mac_payload = _sandbox_detail_payload("mac", "paused", sandbox_class="macos", persistent=True)
    api = RecordingTransport(
        [
            mac_payload,
            {"status": "assigned", "id": "mac"},
            {**mac_payload, "status": "running", "mac_boot_mode": "restored"},
        ]
    )
    mac = AsyncSandbox(sandbox_info_from_id("mac"), api, None)
    await mac.start(timeout=10)
    assert [spec.path for spec in api.specs] == ["/sandboxes/mac", "/sandboxes/mac/resume", "/sandboxes/mac"]
    assert mac.mac_boot_mode == "restored"


@pytest.mark.anyio
async def test_pause_that_returns_to_running_and_resume_that_ends_stopped_fail():
    mac_payload = _sandbox_detail_payload("mac", "running", sandbox_class="macos", persistent=True)
    api = RecordingTransport(
        [
            {},
            {**mac_payload, "status": "running", "error": "snapshot failed: disk full"},
            {},
            {**mac_payload, "status": "stopped"},
        ]
    )
    mac = AsyncSandbox._from_api(mac_payload, api, None)
    with pytest.raises(SandboxTerminalStateError) as paused:
        await mac.pause(timeout=10)
    assert paused.value.state == "started"
    assert mac.error == "snapshot failed: disk full"
    with pytest.raises(SandboxTerminalStateError) as resumed:
        await mac.resume(timeout=10)
    assert resumed.value.state == "stopped"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["pause", "resume", "stop"])
async def test_lifecycle_support_is_the_servers_decision(operation: str):
    # A Linux sandbox's stop/pause/resume goes to the API, and its refusal
    # surfaces as the API's typed error: the SDK checks no class or persistence.
    linux_payload = _sandbox_detail_payload("lin", "running")
    refusal = create_error("not supported for class linux", 409, "NOT_SUPPORTED_FOR_CLASS", "DAYTONA_API")
    api = RecordingTransport([refusal])
    lin = AsyncSandbox._from_api(linux_payload, api, None)
    with pytest.raises(type(refusal)) as raised:
        await getattr(lin, operation)()
    assert raised.value.code == "NOT_SUPPORTED_FOR_CLASS"
    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", f"/sandboxes/lin/{operation}")]


def test_mac_boot_mode_is_read_from_the_response_as_an_open_string():
    sandbox = AsyncSandbox._from_api(
        {
            **_sandbox_detail_payload("mac", "running", sandbox_class="macos"),
            "mac_boot_mode": "future-boot-mode",
            "error": "example failure",
        },
        None,
        None,
    )
    assert sandbox.mac_boot_mode == "future-boot-mode"
    assert sandbox.error == "example failure"
    assert not hasattr(sandbox, "error_reason")
    assert AsyncSandbox._from_api(_sandbox_detail_payload("lin", "running"), None, None).mac_boot_mode is None


@pytest.mark.parametrize("wire_class", ["future-class-a", "future-class-b"])
@pytest.mark.parametrize("generated_response", [False, True])
def test_sandbox_accepts_unrecognized_classes_and_extra_response_fields(
    wire_class: str, generated_response: bool
):
    # Given a response with an unrecognized public class and an extra field.
    payload = {**_sandbox_detail_payload("odd", "running"), "class": wire_class, "future_field": 2}

    # When parsing both the generated response model and the public sandbox.
    response = SandboxDto.model_validate(payload)
    sandbox = AsyncSandbox._from_api(response if generated_response else payload, None, None)

    # Then neither boundary rejects the response or assumes a supported class.
    assert response.var_class.value == wire_class
    assert sandbox.id == "odd"
    assert (sandbox.sandbox_class, sandbox.raw_sandbox_class) == ("unknown", wire_class)
    assert sandbox.state == "started"


@pytest.mark.anyio
async def test_sandbox_refuses_class_gated_calls_for_an_unknown_class_instead_of_assuming_linux():
    api = RecordingTransport([])
    odd = AsyncSandbox._from_api(
        {**_sandbox_detail_payload("odd", "running"), "class": "future-class"}, api, None
    )
    assert (odd.sandbox_class, odd.raw_sandbox_class) == ("unknown", "future-class")
    assert odd.to_json()["sandboxClass"] == "unknown"
    assert odd.to_json()["rawSandboxClass"] == "future-class"
    with pytest.raises(UnsupportedOperationError, match="unsupported sandbox class future-class"):
        await odd.update_lifecycle_policy(on_idle_timeout="delete")
    with pytest.raises(UnsupportedOperationError, match="unsupported sandbox class future-class"):
        await odd.fork()
    assert api.specs == []


@pytest.mark.anyio
async def test_sandbox_keeps_class_blind_calls_for_an_unknown_class(monkeypatch):
    odd_payload = {**_sandbox_detail_payload("odd", "running"), "class": "future-class"}
    snapshot_payload = {
        "id": "snap",
        "class": "future-class",
        "kind": "full",
        "name": "snap",
        "region_id": "us",
        "status": "pending",
        "region_ids": [],
        "created_at": _DEFAULT_TIME,
        "updated_at": _DEFAULT_TIME,
    }
    odd, api = _presigned_sandbox(
        monkeypatch,
        [
            odd_payload,
            _snapshot_operation_payload("snap"),
            snapshot_payload,
            _sign_response(),
            None,
            {**odd_payload, "status": "stopped"},
            {},
            odd_payload,
        ],
        sandbox_id="odd",
    )
    # Already started: start() returns after its refresh, whatever the class.
    await odd.start(timeout=10)
    assert odd.sandbox_class == "unknown"
    # Snapshot and signed-URL routes do not depend on the class.
    assert (await odd.create_snapshot("snap")).id == "snap"
    assert "/files/download?path=%2Ftmp%2Fdemo.txt" in await odd.fs.presigned_download_url("/tmp/demo.txt")
    await odd.rotate_signing_key()
    # Lifecycle requests go to the API whatever the class: it decides.
    await odd.start(timeout=10)
    assert [(spec.method, spec.path) for spec in api.specs if spec.method == "POST"] == [
        ("POST", "/sandboxes/odd/snapshots"),
        ("POST", "/sandboxes/odd/files/sign"),
        ("POST", "/sandboxes/odd/signing-key/rotate"),
        ("POST", "/sandboxes/odd/start"),
    ]
    assert api.responses == []


@pytest.mark.anyio
@respx.mock
async def test_reservations_namespace_paths_idempotency_header_and_parsing():
    reservation = _mac_reservation_payload()
    create_route = respx.post(f"{_SEARCH_BASE}/reservations").mock(
        return_value=httpx.Response(201, json=reservation)
    )
    list_route = respx.get(f"{_SEARCH_BASE}/reservations").mock(
        return_value=httpx.Response(200, json={"reservations": [reservation], "next_cursor": "page-2"})
    )
    get_route = respx.get(f"{_SEARCH_BASE}/reservations/res-1").mock(
        return_value=httpx.Response(200, json=reservation)
    )
    renew_route = respx.post(f"{_SEARCH_BASE}/reservations/res-1/renew").mock(
        return_value=httpx.Response(200, json=_mac_reservation_payload(period_n=2, future_field=1))
    )
    client = AsyncDaytona(_config_at(_SEARCH_BASE, api_key="key"))
    try:
        assert isinstance(client.reservations, AsyncReservationsService)
        created = await client.reservations.create(
            region="mac-region",
            model="m4pro-12c-24g",
            idempotency_key="res-key",
            auto_renew=False,
        )
        await client.reservations.create(region="mac-region")
        await client.reservations.create(region="mac-region")
        listed = await client.reservations.list(limit=1)
        fetched = await client.reservations.get("res-1")
        renewed = await client.reservations.renew("res-1")
    finally:
        await client.close()

    requests = [call.request for call in create_route.calls]
    assert json.loads(requests[0].content) == {
        "region_id": "mac-region",
        "model": "m4pro-12c-24g",
        "auto_renew": False,
    }
    assert requests[0].headers["Idempotency-Key"] == "res-key"
    # Without a caller key every request still carries one, fresh per call.
    assert json.loads(requests[1].content) == {"region_id": "mac-region"}
    generated = [requests[1].headers["Idempotency-Key"], requests[2].headers["Idempotency-Key"]]
    assert all(generated) and generated[0] != generated[1]

    assert created.status == "active"
    assert created.raw_status == "active"
    assert (created.origin, created.auto_renew, created.renewal_error) == ("explicit", True, None)
    assert created.raw_origin == "explicit"
    assert created.period_end == datetime(2026, 9, 23, tzinfo=timezone.utc)
    assert (created.sandboxes_per_mac, created.period_seconds) == (2, 86400)
    assert created.period_started_at == datetime(2026, 9, 22, tzinfo=timezone.utc)
    assert created.pending_mac_started_at == datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
    # `list()` hands back exactly one page: its items plus the server's cursor.
    assert ([item.id for item in listed.items], listed.next_cursor) == (["res-1"], "page-2")
    assert dict(list_route.calls.last.request.url.params) == {"limit": "1"}
    assert fetched.slots_used == 1
    assert renewed.period_n == 2
    assert (list_route.call_count, get_route.call_count, renew_route.call_count) == (1, 1, 1)
    assert created.model == "m4-10c-16g"
    assert created.capacity is not None
    assert (created.capacity.cores, created.capacity.memory_mib, created.capacity.disk_mib) == (
        10,
        16384,
        235520,
    )
    assert (
        created.resources_used.cores,
        created.resources_used.memory_mib,
        created.resources_used.disk_mib,
    ) == (5, 8192, 117760)
    historical = await AsyncReservationsService(
        RecordingTransport([_mac_reservation_payload(model=None, capacity=None)])
    ).get("res-1")
    assert (historical.model, historical.capacity) == (None, None)


@pytest.mark.anyio
async def test_reservations_renew_sends_the_caller_key_or_a_fresh_one_per_call():
    api = RecordingTransport([_mac_reservation_payload(period_n=2) for _ in range(4)])
    service = AsyncReservationsService(api)
    await service.renew("mac-region:res 1", idempotency_key="renew-1")
    await service.renew("mac-region:res 1", idempotency_key="renew-1")
    await service.renew("mac-region:res 1")
    renewed = await service.renew("mac-region:res 1")
    assert [(spec.method, spec.path, spec.json_body) for spec in api.specs] == [
        ("POST", "/reservations/mac-region%3Ares%201/renew", None)
    ] * 4
    keys = [spec.headers["Idempotency-Key"] if spec.headers is not None else None for spec in api.specs]
    assert keys[:2] == ["renew-1", "renew-1"]
    assert uuid.UUID(str(keys[2])) and uuid.UUID(str(keys[3])) and keys[2] != keys[3]
    assert renewed.period_n == 2
    with pytest.raises(BadRequestError, match="idempotency_key must be a string"):
        await service.renew("res-1", idempotency_key=7)  # type: ignore[arg-type]
    assert len(api.specs) == 4


@pytest.mark.anyio
async def test_reservations_create_bulk_posts_the_bulk_body_with_an_idempotency_key():
    batch = {
        "requested": 3,
        "reserved": 2,
        "reservations": [
            _mac_reservation_payload(id="res-1", status="pending_charge"),
            _mac_reservation_payload(id="res-2", status="pending_charge"),
        ],
    }
    api = RecordingTransport([batch, batch, batch])
    service = AsyncReservationsService(api)
    bought = await service.create_bulk(
        region="mac-region",
        count=3,
        min_count=2,
        model="m4pro-12c-24g",
        auto_renew=True,
        idempotency_key="nightly-macs",
    )
    await service.create_bulk(region="mac-region", count=3)
    await service.create_bulk(region="mac-region", count=3)
    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/reservations/bulk")] * 3
    assert api.specs[0].json_body == {
        "region_id": "mac-region",
        "count": 3,
        "min_count": 2,
        "model": "m4pro-12c-24g",
        "auto_renew": True,
    }
    assert api.specs[0].headers == {"Idempotency-Key": "nightly-macs"}
    # Omitted options stay off the body; every request still carries a key, fresh per call.
    assert api.specs[1].json_body == {"region_id": "mac-region", "count": 3}
    keys = [spec.headers["Idempotency-Key"] if spec.headers is not None else None for spec in api.specs[1:]]
    assert uuid.UUID(str(keys[0])) and uuid.UUID(str(keys[1])) and keys[0] != keys[1]
    assert isinstance(bought, MacReservationBatch)
    assert (bought.requested, bought.reserved) == (3, 2)
    assert [item.id for item in bought.reservations] == ["res-1", "res-2"]


_MAC_MODELS = [
    {
        "id": "m4-10c-16g",
        "name": "Apple M4",
        "cores": 10,
        "memory_mib": 16384,
        "disk_mib": 244198,
        "sandbox_disk_mib": 235520,
        "max_sandboxes": 2,
        "default_size": {"cores": 5, "memory_mib": 8192, "disk_mib": 117760},
    },
    {
        "id": "m4pro-12c-24g",
        "name": "Apple M4 Pro",
        "cores": 12,
        "memory_mib": 24576,
        "disk_mib": 488386,
        "sandbox_disk_mib": 479232,
        "max_sandboxes": 2,
        "default_size": {"cores": 6, "memory_mib": 12288, "disk_mib": 239616},
    },
    {
        "id": "tiny-2c-4g",
        "name": "Apple Tiny",
        "cores": 2,
        "memory_mib": 4096,
        "disk_mib": 65536,
        "sandbox_disk_mib": 40960,
        "max_sandboxes": 0,
        "default_size": None,
    },
]


@pytest.mark.anyio
async def test_mac_models_list_reads_the_registry():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    try:
        assert isinstance(client.mac_models, AsyncMacModelsService)
    finally:
        await client.close()
    api = RecordingTransport([{"mac_models": _MAC_MODELS}])
    listed = await AsyncMacModelsService(api).list()
    assert [(spec.method, spec.path) for spec in api.specs] == [("GET", "/mac-models")]
    assert [(m.id, m.name, m.cores, m.memory_mib, m.disk_mib, m.sandbox_disk_mib) for m in listed] == [
        ("m4-10c-16g", "Apple M4", 10, 16384, 244198, 235520),
        ("m4pro-12c-24g", "Apple M4 Pro", 12, 24576, 488386, 479232),
        ("tiny-2c-4g", "Apple Tiny", 2, 4096, 65536, 40960),
    ]
    assert [m.max_sandboxes for m in listed] == [2, 2, 0]
    assert listed[0].default_size is not None and listed[0].default_size.disk_mib == 117760
    assert listed[1].default_size is not None and listed[1].default_size.disk_mib == 239616
    assert listed[2].default_size is None


@pytest.mark.anyio
async def test_mac_models_catalog_reads_free_macs_and_prices():
    catalog = {
        "regions": [
            {
                "id": "mac-region",
                "default_model": "m4-10c-16g",
                "models": [
                    {
                        "model": _MAC_MODELS[0],
                        "available": 3,
                        "price": {
                            "cents_per_hour": 135.12,
                            "period_cents": 3243,
                            "currency": "usd",
                            "period_seconds": 86400,
                        },
                    },
                    {"model": _MAC_MODELS[1], "available": 0, "price": None},
                ],
            }
        ]
    }
    api = RecordingTransport([catalog, catalog])
    service = AsyncMacModelsService(api)
    read = await service.catalog(region="mac-region")
    await service.catalog()
    assert [(spec.method, spec.path, spec.params) for spec in api.specs] == [
        ("GET", "/mac-catalog", {"region_ids": "mac-region"}),
        ("GET", "/mac-catalog", {}),
    ]
    assert isinstance(read, MacCatalog)
    region = read.regions[0]
    assert (region.id, region.default_model) == ("mac-region", "m4-10c-16g")
    priced, unpriced = region.models
    assert (priced.model.id, priced.available) == ("m4-10c-16g", 3)
    assert priced.price is not None
    assert (priced.price.cents_per_hour, priced.price.period_cents, priced.price.period_seconds) == (
        135.12,
        3243,
        86400,
    )
    assert (unpriced.available, unpriced.price) == (0, None)


@pytest.mark.anyio
async def test_reservation_contract_enums_preserve_unknown_wire_values() -> None:
    reservations = AsyncReservationsService(
        RecordingTransport(
            [
                _mac_reservation_payload(
                    status="future_status",
                    origin="future_origin",
                    end_reason="future_reason",
                )
            ]
        )
    )  # type: ignore[arg-type]

    reservation = await reservations.get("res-1")

    assert (reservation.status, reservation.raw_status) == ("unknown", "future_status")
    assert (reservation.origin, reservation.raw_origin) == ("unknown", "future_origin")
    assert (reservation.end_reason, reservation.raw_end_reason) == ("unknown", "future_reason")


@pytest.mark.anyio
async def test_reservation_list_and_iter_decode_contract_enums_like_get() -> None:
    known = _mac_reservation_payload(id="res-known", status="active", origin="auto", end_reason="expired")
    future = _mac_reservation_payload(
        id="res-future", status="future_status", origin="future_origin", end_reason="future_reason"
    )
    page = {"reservations": [known, future]}
    reservations = AsyncReservationsService(RecordingTransport([page, page]))  # type: ignore[arg-type]

    listed = list((await reservations.list()).items)
    drained = [item async for item in reservations.iter()]

    for items in (listed, drained):
        assert [type(r) for r in items] == [MacReservation, MacReservation]
        assert [(r.status, r.raw_status) for r in items] == [
            ("active", "active"),
            ("unknown", "future_status"),
        ]
        assert [(r.origin, r.raw_origin) for r in items] == [
            ("auto", "auto"),
            ("unknown", "future_origin"),
        ]
        assert [(r.end_reason, r.raw_end_reason) for r in items] == [
            ("expired", "expired"),
            ("unknown", "future_reason"),
        ]


@pytest.mark.anyio
@respx.mock
async def test_reservations_iter_drains_every_page_and_stops_on_a_repeated_cursor():
    pages = [
        httpx.Response(
            200, json={"reservations": [_mac_reservation_payload(id="res-1")], "next_cursor": "c2"}
        ),
        httpx.Response(
            200, json={"reservations": [_mac_reservation_payload(id="res-2")], "next_cursor": "c2"}
        ),
    ]
    route = respx.get(f"{_SEARCH_BASE}/reservations").mock(side_effect=pages)
    client = AsyncDaytona(_config_at(_SEARCH_BASE, api_key="key"))
    try:
        drained = [item.id async for item in client.reservations.iter(limit=1)]
    finally:
        await client.close()

    # The second page repeats its own cursor, so the cycle guard stops there.
    assert drained == ["res-1", "res-2"]
    assert route.call_count == 2
    assert [dict(call.request.url.params) for call in route.calls] == [
        {"limit": "1"},
        {"cursor": "c2", "limit": "1"},
    ]


@pytest.mark.anyio
@respx.mock
async def test_reservations_update_patches_auto_renew_and_parses_the_reservation():
    route = respx.patch(f"{_SEARCH_BASE}/reservations/mac-region%3Ares%201").mock(
        return_value=httpx.Response(
            200, json=_mac_reservation_payload(id="mac-region:res 1", origin="auto", auto_renew=False)
        )
    )
    client = AsyncDaytona(_config_at(_SEARCH_BASE, api_key="key"))
    try:
        updated = await client.reservations.update("mac-region:res 1", auto_renew=False)
    finally:
        await client.close()
    assert json.loads(route.calls.last.request.content) == {"auto_renew": False}
    assert (updated.id, updated.origin, updated.auto_renew) == ("mac-region:res 1", "auto", False)


@pytest.mark.anyio
async def test_create_macos_posts_the_create_macos_sandbox_request_body():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "mac", "status": "running", "wait_cap_seconds": 3},
            {"id": "macos-2", "status": "running", "wait_cap_seconds": 3},
            {"id": "mac-3", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    client._api = api  # type: ignore[attr-defined]
    sandbox = await client.create_macos("base-macos")
    assert api.specs[0].path == "/sandboxes/macos"
    assert api.specs[0].json_body == {"image": "base-macos", "wait_seconds": 60}
    assert sandbox.sandbox_class == "macos"

    await client.create_macos(
        "sequoia-xcode",
        name="mac-1",
        # macOS keeps persistent (stop/start, snapshots, disk saved at reservation end).
        persistent=True,
        reservation="new",
        auto_renew=False,
        ports=[PortMapping(port=8080)],
        env={"A": "B"},
        labels={"team": "sdk"},
        secrets=["API_KEY"],
        idempotency_key="mac-key",
        create_timeout_seconds=600,
    )
    assert api.specs[1].json_body == {
        "image": "sequoia-xcode",
        "name": "mac-1",
        # macOS keeps persistent (stop/start, snapshots, disk saved at reservation end).
        "persistent": True,
        "env": {"A": "B"},
        "ports": [{"port": 8080, "proto": "tcp"}],
        "labels": {"team": "sdk", "name": "mac-1"},
        "secrets": ["API_KEY"],
        "reservation": "new",
        "auto_renew": False,
        "wait_seconds": 60,
        "create_timeout_seconds": 600,
    }
    assert api.specs[1].headers is not None and api.specs[1].headers["Idempotency-Key"] == "mac-key"

    # A reservation id places the sandbox on exactly that lease.
    await client.create_macos("base-macos", reservation="mac-region:res-1")
    assert api.specs[2].json_body == {
        "image": "base-macos",
        "reservation": "mac-region:res-1",
        "wait_seconds": 60,
    }


@pytest.mark.parametrize("region", [None, "region-c"])
@pytest.mark.anyio
@respx.mock
async def test_create_macos_on_the_default_api_raises_the_api_region_conflict_unchanged(
    monkeypatch: pytest.MonkeyPatch, region: Optional[str]
):
    from daytona.v2.errors import ConflictError

    monkeypatch.delenv("DAYTONA_V2_API_URL", raising=False)
    not_served = "conflict: region 'region-c' is not available at this API URL"
    create = respx.post("https://api.daytona.com/sandboxes/macos").mock(
        return_value=httpx.Response(
            409, json={"code": "CONFLICT", "message": not_served, "source": "DAYTONA_API"}
        )
    )
    regions = respx.get("https://api.daytona.com/regions")
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    try:
        with pytest.raises(ConflictError) as err:
            await client.create_macos("base-macos", region=region)
    finally:
        await client.close()
    assert (err.value.message, err.value.code) == (not_served, "CONFLICT")
    assert (create.call_count, regions.call_count) == (1, 0)


@pytest.mark.anyio
async def test_create_macos_sends_network_policy():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {"id": "mac", "status": "running", "wait_cap_seconds": 3},
            {"id": "mac2", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    client._api = api  # type: ignore[attr-defined]
    await client.create_macos(
        "base-macos",
        network_policy={"block_all": True, "allow_cidrs": ["93.184.215.14/32"]},
    )
    assert api.specs[0].json_body == {
        "image": "base-macos",
        "network_policy": {"block_all": True, "allow_cidrs": ["93.184.215.14/32"]},
        "wait_seconds": 60,
    }
    # Omitted: no key at all.
    await client.create_macos("base-macos")
    assert "network_policy" not in api.specs[1].json_body


@pytest.mark.anyio
async def test_create_macos_hands_back_the_reservation_a_running_create_claimed():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [{"id": "mac", "status": "running", "wait_cap_seconds": 3, "reservation_id": "mac-region:res-1"}]
    )
    client._api = api  # type: ignore[attr-defined]
    sandbox = await client.create_macos("base-macos")
    # The create answers running, so no wait runs: the lease comes from this response alone.
    assert len(api.specs) == 1
    assert sandbox.reservation_id == "mac-region:res-1"


@pytest.mark.anyio
async def test_create_macos_sends_supplied_size_axes_exactly_and_omits_absent_ones():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [{"id": f"mac-{n}", "status": "running", "wait_cap_seconds": 3} for n in range(6)]
    )
    client._api = api  # type: ignore[attr-defined]
    await client.create_macos("base-macos")
    await client.create_macos(
        "base-macos",
        reservation="mac-region:res-1",
        resources=MacosResources(cpu=10, memory=16),
        # macOS keeps persistent (stop/start, snapshots, disk saved at reservation end).
        persistent=True,
    )
    await client.create_macos("base-macos", resources=MacosResources(memory=4.5))
    await client.create_macos("base-macos", resources=MacosResources(cpu=4))
    await client.create_macos("base-macos", resources=MacosResources(disk=115))
    await client.create_macos("base-macos", resources=MacosResources(disk=80.5))
    bodies = [spec.json_body for spec in api.specs]
    assert bodies[0] == {"image": "base-macos", "wait_seconds": 60}
    assert bodies[1] == {
        "image": "base-macos",
        # macOS keeps persistent (stop/start, snapshots, disk saved at reservation end).
        "persistent": True,
        "reservation": "mac-region:res-1",
        "cores": 10,
        "memory_mib": 16384,
        "wait_seconds": 60,
    }
    assert bodies[2] == {"image": "base-macos", "memory_mib": 4608, "wait_seconds": 60}
    assert bodies[3] == {"image": "base-macos", "cores": 4, "wait_seconds": 60}
    assert bodies[4] == {"image": "base-macos", "disk_mib": 117760, "wait_seconds": 60}
    assert bodies[5] == {"image": "base-macos", "disk_mib": 82432, "wait_seconds": 60}


_CPU_MESSAGE = "cpu must be an integer between 4 and 2147483647"
_MEMORY_MESSAGE = (
    "memory must be a finite GiB value that converts to an integer MiB value between 4096 and 2147483647"
)
_DISK_MESSAGE = (
    "disk must be a finite GiB value that converts to an integer MiB value between 81920 and 2147483647"
)


@pytest.mark.parametrize(
    ("size", "message"),
    [
        ({"cpu": 3}, _CPU_MESSAGE),
        ({"cpu": 4.5}, _CPU_MESSAGE),
        ({"cpu": 4.0}, _CPU_MESSAGE),
        ({"cpu": True}, _CPU_MESSAGE),
        ({"cpu": 2147483648}, _CPU_MESSAGE),
        ({"memory": 3.999}, _MEMORY_MESSAGE),
        ({"memory": 4.0001}, _MEMORY_MESSAGE),
        ({"memory": True}, _MEMORY_MESSAGE),
        ({"memory": float("inf")}, _MEMORY_MESSAGE),
        ({"memory": float("nan")}, _MEMORY_MESSAGE),
        ({"memory": 2097152}, _MEMORY_MESSAGE),
        ({"memory": 10**400}, _MEMORY_MESSAGE),
        ({"disk": 79.999}, _DISK_MESSAGE),
        ({"disk": 80.0001}, _DISK_MESSAGE),
        ({"disk": True}, _DISK_MESSAGE),
        ({"disk": float("nan")}, _DISK_MESSAGE),
        ({"disk": 2097152}, _DISK_MESSAGE),
    ],
)
@pytest.mark.anyio
async def test_create_macos_refuses_an_invalid_size_before_posting(size: dict[str, object], message: str):
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    client._api = api  # type: ignore[attr-defined]
    with pytest.raises(BadRequestError) as info:
        await client.create_macos("base-macos", resources=MacosResources(**size))  # type: ignore[arg-type]
    assert (info.value.status_code, info.value.code, info.value.source, str(info.value)) == (
        400,
        "BAD_REQUEST",
        "SDK",
        message,
    )
    assert api.specs == []


@pytest.mark.anyio
async def test_sizes_read_exact_gib_and_keep_the_reservation_id_through_refresh_and_serialization():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    unclaimed = {
        **_sandbox_detail_payload(
            "mac", "pending", sandbox_class="macos", cores_max=0, cpu=0, memory_mib=0, disk_mib=0
        ),
        "reservation_id": None,
    }
    claimed = {
        **_sandbox_detail_payload(
            "mac", "running", sandbox_class="macos", cores_max=7, cpu=7, memory_mib=4608, disk_mib=117760
        ),
        "memory_max_mib": 4608,
        "disk_max_mib": 82432,
        "reservation_id": "mac-region:res-1",
    }
    client._api = RecordingTransport([unclaimed, claimed])  # type: ignore[attr-defined]
    sandbox = lazy_sandbox(client, "mac")
    await sandbox.refresh_data()
    assert (sandbox.memory, sandbox.disk, sandbox.reservation_id) == (0, 0, None)
    await sandbox.refresh_data()
    assert (sandbox.memory, sandbox.memory_max, sandbox.reservation_id) == (4.5, 4.5, "mac-region:res-1")
    assert (sandbox.disk, sandbox.disk_max) == (115, 80.5)
    serialized = sandbox.to_json()
    assert (serialized["memory"], serialized["memoryMax"], serialized["reservationId"]) == (
        4.5,
        4.5,
        "mac-region:res-1",
    )
    assert (serialized["disk"], serialized["diskMax"]) == (115, 80.5)
    # Linux reads back exactly too: MiB / 1024, never rounded.
    linux = AsyncSandbox._from_api(
        _sandbox_detail_payload("lin", "running", memory_mib=4608, disk_mib=1536), None, None
    )
    assert (linux.memory, linux.disk) == (4.5, 1.5)


@pytest.mark.anyio
async def test_create_macos_rejects_invalid_requests_before_posting():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    client._api = api  # type: ignore[attr-defined]
    cases: list[tuple[Callable[[], Awaitable[object]], str]] = [
        (
            lambda: client.create_macos("base-macos", auto_renew=True),
            'macos create takes auto_renew only with reservation="new"',
        ),
        (
            lambda: client.create_macos("base-macos", reservation="mac-region:res-1", auto_renew=True),
            'macos create takes auto_renew only with reservation="new"',
        ),
        # Unknown lifecycle fields and unsupported actions are rejected.
        (
            lambda: client.create_macos(
                "base-macos",
                # macOS keeps persistent (stop/start, snapshots, disk saved at reservation end).
                persistent=True,
                lifecycle_policy={"future_option": True},
            ),
            "lifecycle_policy contains unknown fields: future_option",
        ),
        (
            lambda: client.create_macos(
                "base-macos", lifecycle_policy={"timeout_seconds": 60, "on_timeout": "stop"}
            ),
            "macos create does not accept: lifecycle_policy.on_timeout=stop",
        ),
        (
            lambda: client.create_macos(
                "base-macos",
                persistent=True,
                lifecycle_policy={"timeout_seconds": 60, "on_timeout": "pause"},
            ),
            "macos create does not accept: lifecycle_policy.on_timeout=pause",
        ),
    ]
    for action, message in cases:
        with pytest.raises(BadRequestError, match=re.escape(message)):
            await action()
    assert api.specs == []


@respx.mock
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "NO_ACTIVE_RESERVATION"),
        (409, "AUTO_RESERVE_LIMIT_REACHED"),
        (409, "MAC_DISK_FULL"),
        (404, "RESERVATION_NOT_FOUND"),
        (402, "INSUFFICIENT_BALANCE"),
    ],
)
@pytest.mark.anyio
async def test_create_macos_surfaces_coded_failures(status: int, code: str):
    # macOS capacity errors use the unified API envelope.
    respx.post(f"{_SEARCH_BASE}/sandboxes/macos").mock(
        return_value=httpx.Response(
            status, json={"code": code, "message": f"refused: {code}", "source": "DAYTONA_API"}
        )
    )
    client = AsyncDaytona(_config_at(_SEARCH_BASE, api_key="key"))
    try:
        with pytest.raises(DaytonaError) as info:
            await client.create_macos("base-macos")
    finally:
        await client.close()
    assert info.value.status_code == status
    assert info.value.code == code
    assert info.value.source == "DAYTONA_API"
    assert str(info.value) == f"refused: {code}"
    if status == 409:
        assert isinstance(info.value, ConflictError)


# --- Platform images + imageless create -------------


@pytest.mark.anyio
async def test_daytona_create_without_image_omits_image_and_reads_booted_image() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(
        [
            {
                "id": "sbx",
                "status": "running",
                "wait_cap_seconds": 3,
                "image": {"id": "img_default", "name": "daytona-base"},
            },
            {"id": "sbx2", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    sandbox = await client.create()
    named = await client.create(name="example-user")

    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/sandboxes")] * 2
    assert api.specs[0].json_body is not None and "image" not in api.specs[0].json_body
    assert api.specs[1].json_body is not None and "image" not in api.specs[1].json_body
    assert api.specs[1].json_body["name"] == "example-user"
    assert sandbox.image is not None
    assert (sandbox.image.id, sandbox.image.name) == ("img_default", "daytona-base")
    # An absent response image leaves the handle's image unknown.
    assert named.image is None


@pytest.mark.anyio
async def test_daytona_create_prefers_the_response_image_on_every_create() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    echoed = {"id": "img_py", "name": "python:3.12"}
    api = RecordingTransport(
        [
            {"id": "named", "status": "running", "wait_cap_seconds": 3, "image": echoed},
            {"id": "by-id", "status": "running", "wait_cap_seconds": 3, "image": echoed},
            {
                "id": "bare",
                "status": "running",
                "wait_cap_seconds": 3,
                "image": {"id": "img_default", "name": "daytona-base"},
            },
            {
                "id": "mac",
                "status": "running",
                "wait_cap_seconds": 3,
                "image": {"id": "img_mac", "name": "base-macos"},
            },
            # Responses may omit the optional image field.
            {"id": "fallback-named", "status": "running", "wait_cap_seconds": 3},
            {"id": "fallback-id", "status": "running", "wait_cap_seconds": 3},
            {"id": "fallback-bare", "status": "running", "wait_cap_seconds": 3},
        ]
    )
    bind_api(client, api)

    named = await client.create("python:3.12")
    by_id = await client.create("img_py")
    bare = await client.create()
    macos = await client.create_macos("base-macos")
    fallback_named = await client.create("python:3.12")
    fallback_id = await client.create("img_py")
    fallback_bare = await client.create()

    def ref(sandbox: AsyncSandbox) -> tuple[str, str | None] | None:
        return None if sandbox.image is None else (sandbox.image.id, sandbox.image.name)

    # The response's {id, name} is canonical: a named create resolves to its img_ id.
    assert ref(named) == ("img_py", "python:3.12")
    assert ref(by_id) == ("img_py", "python:3.12")
    assert ref(bare) == ("img_default", "daytona-base")
    assert ref(macos) == ("img_mac", "base-macos")
    # No echo: only an img_ id the caller passed becomes a reference; a name never is one.
    assert ref(fallback_named) is None
    assert ref(fallback_id) == ("img_py", None)
    assert ref(fallback_bare) is None
    # The class is known only when the call fixes it.
    assert (named.sandbox_class, macos.sandbox_class) == (None, "macos")


@pytest.mark.anyio
async def test_daytona_create_blank_image_still_fails_before_any_request() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([])
    bind_api(client, api)

    for blank in ("", "   "):
        with pytest.raises(DaytonaError, match="image must be a non-empty string"):
            await client.create(blank)
    assert api.specs == []


def test_create_body_omits_image_only_for_an_absent_linux_source() -> None:
    assert _create_body({"route": "linux", "source_kind": "image"}) == {}
    with pytest.raises(Exception, match="requires a non-empty image"):
        _create_body({"route": "linux", "source_kind": "image", "source": ""})
    with pytest.raises(Exception, match="requires a non-empty image"):
        _create_body({"route": "macos", "source_kind": "image"})
    with pytest.raises(Exception, match="requires a non-empty snapshot"):
        _create_body({"route": "linux", "source_kind": "snapshot"})


@pytest.mark.anyio
async def test_image_list_forwards_platform_and_parses_platform_fields() -> None:
    platform_payload = {
        **_image_page_payload("img_base"),
        "platform": True,
        "default": True,
        "deprecated": False,
    }
    transport = RecordingTransport(
        [
            {"images": [platform_payload], "next_cursor": None},
            {
                "images": [
                    {
                        key: value
                        for key, value in _image_page_payload("img_own").items()
                        if key not in ("platform", "default", "deprecated")
                    }
                ],
                "next_cursor": None,
            },
        ]
    )
    service = AsyncImagesService(transport)  # type: ignore[arg-type]

    page = await service.list(platform="true")
    assert transport.specs[0].params == {"anonymous": False, "platform": "true"}
    assert (page.items[0].platform, page.items[0].default, page.items[0].deprecated) == (True, True, False)

    # The flags are contract-required: a list item or a detail without one is malformed.
    with pytest.raises(BadRequestError, match="default|platform|deprecated"):
        await service.list(platform="any")
    minimal_payload = {
        key: value
        for key, value in _image_page_payload("img_own").items()
        if key not in ("platform", "default", "deprecated")
    }
    single = AsyncImagesService(RecordingTransport([minimal_payload]))  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match="default|platform|deprecated"):
        await single.get("img_own")


@pytest.mark.anyio
async def test_image_list_and_get_parse_disk_mib_of_a_macos_snapshot() -> None:
    snapshot_payload = {
        **_image_page_payload("img_mac"),
        "class": "macos",
        "origin": "macos",
        "disk_mib": 61440,
    }
    linux_payload = {**_image_page_payload("img_linux"), "disk_mib": None}
    transport = RecordingTransport([{"images": [snapshot_payload, linux_payload], "next_cursor": None}])

    page = await AsyncImagesService(transport).list(anonymous=True)  # type: ignore[arg-type]
    assert [(item.id, item.disk_mib) for item in page.items] == [("img_mac", 61440), ("img_linux", None)]

    # An absent optional disk size defaults to None.
    minimal_payload = {key: value for key, value in _image_page_payload("img_old").items() if key != "cursor"}
    got = await AsyncImagesService(RecordingTransport([minimal_payload])).get("img_old")  # type: ignore[arg-type]
    assert got.disk_mib is None


def _volume_payload(volume_id: str) -> dict[str, object]:
    return {
        "created_at": _DEFAULT_TIME,
        "id": volume_id,
        "name": volume_id,
        "size_gib": 1,
        "status": "ready",
        "type": "disk",
        "updated_at": _DEFAULT_TIME,
    }


def test_create_body_sends_read_only_volume_mounts():
    assert _create_body(
        {
            "route": "linux",
            "source_kind": "image",
            "source": "python:3.12",
            "volumes": [
                VolumeMount(volume_id="bucket-vol", mount_path="/mnt/models", subpath="v1/", read_only=True),
                VolumeMount(volume_id="disk-vol", mount_path="/data"),
                VolumeMount(volume_id="rw-bucket", mount_path="/mnt/rw", read_only=False),
            ],
        }
    ) == {
        "image": "python:3.12",
        "volumes": [
            {"volume": "bucket-vol", "mount_path": "/mnt/models", "subpath": "v1/", "read_only": True},
            {"volume": "disk-vol", "mount_path": "/data"},
            {"volume": "rw-bucket", "mount_path": "/mnt/rw"},
        ],
    }


@pytest.mark.anyio
async def test_volume_service_shapes_routes():
    missing = create_error("missing", 404, code_names.NOT_FOUND, "DAYTONA_API")
    transport = RecordingTransport(
        [
            {"volumes": [_volume_payload("vol")], "nextCursor": "vn"},
            missing,
            _volume_payload("created-vol"),
            {},
        ]
    )

    volume = AsyncVolumesService(transport)  # type: ignore[arg-type]
    listed_volume = (await volume.list()).items[0]
    assert (listed_volume.type, listed_volume.raw_type) == ("disk", "disk")
    assert (listed_volume.status, listed_volume.raw_status) == ("ready", "ready")
    assert transport.specs[0].params == {}
    assert (await volume.get("vol", create=True)).id == "created-vol"
    assert [spec.path for spec in transport.specs[1:3]] == ["/volumes/vol", "/volumes"]
    await volume.delete("vol")


@pytest.mark.anyio
async def test_volume_delete_reports_acceptance_and_id_only_is_opt_in():
    volume_id = "volume-id"
    transport = RecordingTransport([{"id": volume_id, "state": "deleting"}, _volume_payload(volume_id), {}])
    volume = AsyncVolumesService(transport)  # type: ignore[arg-type]

    assert await volume.delete("named-volume") is None
    assert transport.specs[0].params == {}
    assert (await volume.get(volume_id, id_only=True)).id == volume_id
    await volume.delete(volume_id, id_only=True)
    assert [spec.params for spec in transport.specs] == [{}, {"id_only": True}, {"id_only": True}]


@pytest.mark.anyio
async def test_volume_get_does_not_create_when_id_only_lookup_is_missing():
    missing = create_error("missing", 404, code_names.NOT_FOUND, "DAYTONA_API")
    transport = RecordingTransport([missing])
    volume = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        await volume.get("volume-id", create=True, id_only=True)
    assert [spec.method for spec in transport.specs] == ["GET"]


@pytest.mark.anyio
async def test_service_objects_preserve_nullable_volume_fields() -> None:
    payload = {**_volume_payload("vol"), "error": None, "used_bytes": None}
    transport = RecordingTransport([payload])
    volume = AsyncVolumesService(transport)  # type: ignore[arg-type]
    result = await volume.get("vol")
    assert result.error is None
    assert result.used_bytes is None
    assert result.last_used_at is None
    assert result.id == payload["id"]
    assert result.name == payload["name"]


@pytest.mark.anyio
async def test_volume_contract_enums_preserve_unknown_wire_values() -> None:
    volumes = AsyncVolumesService(
        RecordingTransport(
            [{"volumes": [{**_volume_payload("vol"), "status": "future_status", "type": "future_type"}]}]
        )
    )  # type: ignore[arg-type]

    volume = (await volumes.list()).items[0]
    assert (volume.status, volume.raw_status) == ("unknown", "future_status")
    assert (volume.type, volume.raw_type) == ("unknown", "future_type")


@pytest.mark.anyio
async def test_create_macos_sends_disk_volumes():
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport([{"id": "mac", "status": "running", "wait_cap_seconds": 3}])
    client._api = api  # type: ignore[attr-defined]
    await client.create_macos(
        "base-macos",
        volumes=[VolumeMount(volume_id="data", mount_path="/Users/example/data", subpath="proj")],
    )
    assert api.specs[0].json_body == {
        "image": "base-macos",
        "volumes": [{"volume": "data", "mount_path": "/Users/example/data", "subpath": "proj"}],
        "wait_seconds": 60,
    }


@pytest.mark.anyio
async def test_image_build_and_pull_submissions_are_ordinary_requests_with_one_option_set() -> None:
    # The submission and its first read keep the client's total request
    # timeout (no own timeout, no unbounded read), with or without a pull budget.
    api = RecordingTransport(
        [
            _image_operation_payload("img-b"),
            _image_page_payload("img-b"),
            _image_operation_payload("img-p"),
            _image_page_payload("img-p"),
            _image_operation_payload("img-q"),
            _image_page_payload("img-q"),
        ]
    )
    images = AsyncImagesService(api)  # type: ignore[arg-type]

    await images.build(ImageBuilder.from_dockerfile("FROM python:3.12"), name="built", cpu_arch="arm64")
    await images.pull("python:3.12", idle_expiry_seconds=60, cpu_arch="amd64", region="eu", timeout=3600)
    await images.pull("python:3.12", timeout=0)

    assert [(spec.timeout, spec.unbounded_read) for spec in api.specs] == [(None, False)] * 6
    assert api.specs[2].json_body == {
        "source": {"type": "registry", "ref": "python:3.12"},
        "idle_expiry_seconds": 60,
        "region_id": "eu",
        "cpu_arch": "amd64",
    }
    with pytest.raises(TypeError):
        await images.build(ImageBuilder.from_dockerfile("FROM x"), timeout=5)  # type: ignore[call-arg]
