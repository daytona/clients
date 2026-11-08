# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The synchronous surface is derived from the async one — prove it stays so.

Three guards:

1. **Drift** — ``src/daytona/v2/_sync/`` is byte-identical to what the
   generator emits from the current async modules.
2. **Mirror** — every public method on every wrapped async class has a sync
   twin with the same parameters; coroutines become plain methods, async
   iterators become iterators, and SDK handles in return types become their
   ``Sync*`` twins.
3. **Conformance** — one behavioural test per template shape the generator
   emits (call, iterator, handle-wrapping, containers, properties, context
   managers, rehydration), driven against a mock transport. Per-method sync
   tests would only re-test the generator 269 times; these test it once per
   shape it can get wrong.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from _handles import lazy_sandbox
from gen_sync import (
    _LOOP_FREE,
    _SOURCE_ANNOTATIONS,
    _index_source,
    _is_internal_binder,
    _is_public_name,
    _sync_src_type,
)
from gen_sync import TARGETS as TARGETS_BY_MODULE

from daytona.v2 import _sync as sync_module
from daytona.v2._bridge import BridgeError, SyncBridge
from daytona.v2._sync import Daytona
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.process import AsyncProcessHandle, AsyncProcessService
from daytona.v2.types import DaytonaConfig, ListResult


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


ROOT = Path(__file__).resolve().parents[1]


# --- 1. drift -----------------------------------------------------------------


def test_generated_sync_surface_is_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "gen" / "gen_sync.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        "src/daytona/v2/_sync/ is stale; run `python gen/gen_sync.py`\n" + result.stdout + result.stderr
    )


# --- 2. mirror ----------------------------------------------------------------


def _pairs() -> list[tuple[type, type]]:
    from daytona.v2._sync import _runtime

    registry: dict[type, type] = _runtime.REGISTRY
    return sorted(registry.items(), key=lambda pair: pair[0].__name__)


def _pair_id(value: object) -> str:
    # pytest calls `ids` once per parametrized VALUE (async_cls, then sync_cls),
    # so a class comes in here — rendered as "AsyncX-X" in the test id.
    return getattr(value, "__name__", repr(value))


def _public_methods(cls: type) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, raw in vars(cls).items():
        if not _is_public_name(name) and name not in ("__aenter__", "__aexit__", "__enter__", "__exit__"):
            continue
        if isinstance(raw, (property, classmethod, staticmethod)) or callable(raw):
            out[name] = raw
    return out


def _sync_name(name: str) -> str:
    return {"__aenter__": "__enter__", "__aexit__": "__exit__"}.get(name, name)


@pytest.mark.parametrize(("async_cls", "sync_cls"), _pairs(), ids=_pair_id)
def test_every_async_public_method_has_a_sync_twin(async_cls: type, sync_cls: type) -> None:
    async_methods = _public_methods(async_cls)
    sync_methods = _public_methods(sync_cls)
    # from_api-style constructors and bind()/attach() wiring take transports or
    # async callables and are deliberately not on the sync surface
    internal = {
        name
        for name, raw in async_methods.items()
        if _is_internal_binder(raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw)
    }
    missing = {_sync_name(n) for n in async_methods} - set(sync_methods) - {_sync_name(n) for n in internal}
    assert not missing, f"{sync_cls.__name__} lacks twins for {sorted(missing)}"


@pytest.mark.parametrize(("async_cls", "sync_cls"), _pairs(), ids=_pair_id)
def test_sync_twins_are_plain_callables_with_matching_parameters(async_cls: type, sync_cls: type) -> None:
    for name, raw in _public_methods(async_cls).items():
        if isinstance(raw, property) or name.startswith("__"):
            continue
        fn = getattr(raw, "__func__", raw)
        if _is_internal_binder(fn):
            continue
        if isinstance(raw, (classmethod, staticmethod)):
            # Rehydrators: the twin must take exactly the async parameters —
            # no bridge, no extra kwarg — or the sync surface is unusable
            # without reaching for internals.
            twin_raw = inspect.getattr_static(sync_cls, name)
            assert isinstance(twin_raw, (classmethod, staticmethod)), (
                f"{sync_cls.__name__}.{name} lost its classmethod"
            )
            raw, twin = fn, twin_raw.__func__
            async_params = list(inspect.signature(raw).parameters.values())[1:]
            sync_params = list(inspect.signature(twin).parameters.values())[1:]
            assert [(p.name, p.kind, p.default) for p in sync_params] == [
                (p.name, p.kind, p.default) for p in async_params
            ], f"{sync_cls.__name__}.{name} parameters (name/kind/default) diverge from the async twin"
            continue
        twin = getattr(sync_cls, _sync_name(name))
        assert not inspect.iscoroutinefunction(twin), f"{sync_cls.__name__}.{name} is still a coroutine"
        assert not inspect.isasyncgenfunction(twin), f"{sync_cls.__name__}.{name} is still an async generator"
        async_params = list(inspect.signature(raw).parameters.values())[1:]
        sync_params = list(inspect.signature(twin).parameters.values())[1:]
        # name, kind (positional-only / keyword-only / ...) and default must all agree
        assert [(p.name, p.kind, p.default) for p in sync_params] == [
            (p.name, p.kind, p.default) for p in async_params
        ], f"{sync_cls.__name__}.{name} parameters (name/kind/default) diverge from the async twin"


@pytest.mark.parametrize(("async_cls", "sync_cls"), _pairs(), ids=_pair_id)
def test_plain_methods_run_on_the_bridge_loop_unless_loop_free(async_cls: type, sync_cls: type) -> None:
    """Plain (non-coroutine) methods on an async class may touch loop-owned
    state (a gate registry, an asyncio.Queue). The generated twin must dispatch
    them onto the bridge loop — except the pure serialisers in _LOOP_FREE,
    which must NOT (they are safe anywhere and must work after close()).
    Asserted on the emitted source so the routing rule cannot silently drop
    out of the generator again."""
    import textwrap

    for name, raw in _public_methods(async_cls).items():
        if isinstance(raw, (classmethod, staticmethod)) or name.startswith("__"):
            continue
        if isinstance(raw, property):
            # Computed state the loop mutates is read on the loop; a field of an
            # immutable snapshot the class lists in _SYNC_DIRECT_PROPERTIES is
            # read directly (and stays readable after the client closes).
            twin_prop = inspect.getattr_static(sync_cls, name)
            assert isinstance(twin_prop, property), f"{sync_cls.__name__}.{name} is no longer a property"
            body = textwrap.dedent(inspect.getsource(twin_prop.fget))  # type: ignore[arg-type]
            if name in getattr(async_cls, "_SYNC_DIRECT_PROPERTIES", frozenset()):
                assert "call_sync" not in body, (
                    f"{sync_cls.__name__}.{name} should read the snapshot directly"
                )
            else:
                assert "self._bridge.call_sync(" in body, (
                    f"{sync_cls.__name__}.{name} reads loop-owned state off the loop"
                )
            continue
        if _is_internal_binder(raw) or inspect.iscoroutinefunction(raw) or inspect.isasyncgenfunction(raw):
            continue
        body = textwrap.dedent(inspect.getsource(getattr(sync_cls, name)))
        routed = "self._bridge.call_sync(" in body
        if name in _LOOP_FREE:
            assert not routed, f"{sync_cls.__name__}.{name} is loop-free but goes through the bridge"
        else:
            assert routed, f"{sync_cls.__name__}.{name} touches an async object off the bridge loop"


def test_generator_has_no_per_method_knowledge_beyond_two_shared_rules() -> None:
    """The generator emits the same templates for every method and knows no
    method names, except the two rules the
    mirror test imports and shares (`_LOOP_FREE`, `_is_internal_binder`).
    If either needs to grow, fix the async class instead."""
    import re

    assert len(_LOOP_FREE) <= 8, (
        f"_LOOP_FREE is growing ({sorted(_LOOP_FREE)}); fix the async class, not the generator"
    )
    source = (ROOT / "gen" / "gen_sync.py").read_text()
    # Per-method knowledge would show up as a comparison of the method name
    # being emitted (`m.name`) against a literal. Module keys in TARGETS,
    # container-kind tags and constructor-param conventions are not that.
    comparisons = re.findall(
        r"""(?<![\w.])m\.name\s*(?:==|!=|in|not in)\s*\(?\s*(["'][^"')]+["'](?:\s*,\s*["'][^"')]+["'])*)""",
        source,
    )
    literal_names = {name.strip("\"' ") for group in comparisons for name in group.split(",")}
    allowed = {"close", "__aenter__", "__aexit__"}  # the root client's lifecycle, wired by hand on purpose
    leaked = sorted(literal_names - allowed - set(_LOOP_FREE))
    assert "_LOOP_FREE" in source and "_is_internal_binder" in source
    assert leaked == [], f"generator mentions specific SDK methods: {leaked}"


def test_every_cross_module_twin_construction_has_a_runtime_import() -> None:
    """mypy does NOT catch a TYPE_CHECKING-only name used at runtime, so the
    function-local import the generator emits before a foreign-twin
    construction is the sole guard against a NameError at call time.
    Assert it structurally on every generated module."""
    import ast

    sync_dir = ROOT / "src" / "daytona" / "v2" / "_sync"
    assert any(sync_dir.glob("*.py")), sync_dir
    twin_names = {n.removeprefix("Async") for names in TARGETS_BY_MODULE.values() for n in names}
    problems: list[str] = []
    for path in sorted(sync_dir.glob("*.py")):
        if path.name in ("__init__.py", "_runtime.py"):
            continue
        tree = ast.parse(path.read_text())
        local = {n.removeprefix("Async") for n in TARGETS_BY_MODULE[path.stem]}
        type_checking_only: set[str] = set()
        for node in tree.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for stmt in node.body:
                    if isinstance(stmt, ast.ImportFrom):
                        type_checking_only |= {a.name for a in stmt.names}
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            imported_here = {
                a.name for stmt in ast.walk(fn) if isinstance(stmt, ast.ImportFrom) for a in stmt.names
            }
            # names that appear anywhere inside an annotation are not runtime uses
            annotation_nodes: set[int] = set()
            for arg in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs:
                if arg.annotation is not None:
                    annotation_nodes |= {id(n) for n in ast.walk(arg.annotation)}
            if fn.returns is not None:
                annotation_nodes |= {id(n) for n in ast.walk(fn.returns)}
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id in twin_names and node.id not in local:
                    if id(node) in annotation_nodes:
                        continue
                    if node.id in type_checking_only and node.id not in imported_here:
                        problems.append(
                            f"{path.name}:{node.lineno} {fn.name} uses {node.id} at runtime without a local import"
                        )
    assert problems == []


def test_loop_free_methods_are_really_loop_free() -> None:
    """The _LOOP_FREE exemptions must be pure construction/serialisation: no
    awaits, no asyncio primitives, no shared mutable containers touched."""
    import textwrap

    forbidden = (
        "asyncio.",
        "await ",
        "_context_gates",
        "_events",
        ".put",
        ".get_nowait",
        "_gate_for",
    )
    for async_cls, _ in _pairs():
        for name in _LOOP_FREE:
            raw = getattr(async_cls, name, None)
            if raw is None or isinstance(raw, (classmethod, staticmethod)):
                continue
            body = textwrap.dedent(inspect.getsource(raw))
            hits = [token for token in forbidden if token in body]
            assert not hits, f"{async_cls.__name__}.{name} is in _LOOP_FREE but touches {hits}"


def test_sync_return_annotations_are_the_generators_transform_of_the_async_ones() -> None:
    """Structural, not just 'no async names left': every sync return annotation
    — methods, properties AND classmethods — must equal what the generator's
    own type rewrite produces from the async source annotation, so a lost or
    broadened Sync handle type (e.g. `Sandbox.process`,
    `ProcessHandle.from_json`) cannot pass."""
    offenders: list[str] = []
    checked = 0
    for async_cls, sync_cls in _pairs():
        module = async_cls.__module__.rsplit(".", 1)[-1]
        if not any(k[0] == module for k in _SOURCE_ANNOTATIONS):
            _index_source(module)
        for name, raw in _public_methods(async_cls).items():
            if name.startswith("__"):
                continue
            fn = raw.fget if isinstance(raw, property) else getattr(raw, "__func__", raw)
            if _is_internal_binder(fn):
                continue
            _, async_ret = _SOURCE_ANNOTATIONS.get((module, async_cls.__name__, name), ({}, None))
            if async_ret is None:
                continue
            twin = inspect.getattr_static(sync_cls, name)
            twin_fn = twin.fget if isinstance(twin, property) else getattr(twin, "__func__", twin)
            sync_ret = inspect.signature(twin_fn).return_annotation
            expected = _sync_src_type(async_ret)
            checked += 1
            if str(sync_ret).replace(" ", "") != expected.replace(" ", ""):
                offenders.append(f"{sync_cls.__name__}.{name}: {sync_ret!r} != expected {expected!r}")
    assert offenders == []
    assert checked > 200, f"only {checked} members checked; the guard has lost coverage"


# --- 3. conformance -----------------------------------------------------------


def _sandbox(
    sandbox_id: str,
    status: str = "running",
    *,
    toolbox_proxy_url: str | None = "http://toolbox.invalid/sandboxes",
) -> dict[str, object]:
    return {
        "class": "linux",
        "cpu": 1,
        "id": sandbox_id,
        "image": {"id": "img_py", "name": "python:3.12"},
        "snapshot": None,
        "memory_mib": 1024,
        "name": sandbox_id,
        "port_map": [],
        "region_id": "region-b",
        "disk_mib": 2048,
        "status": status,
        "cores_max": 1,
        "persistent": False,
        "spot": False,
        "created_at": "2026-08-24T00:00:00Z",
        "cursor": sandbox_id,
        "toolbox_proxy_url": toolbox_proxy_url,
    }


def _sandboxes(*sandboxes: dict[str, object], next_cursor: str | None = None) -> dict[str, object]:
    return {"sandboxes": list(sandboxes), "next_cursor": next_cursor, "total": 0, "total_relation": "eq"}


def _process(process_id: str, state: str = "running") -> dict[str, object]:
    return {
        "id": process_id,
        "kind": "exec",
        "state": state,
        "createdAt": "2026-01-01T00:00:00Z",
        "cwd": "/workspace",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "truncatedHead": False,
    }


class _Server:
    """A route table over httpx.MockTransport, shared by API and toolbox."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.calls.append(key)
        body = self.routes.get(key)
        if body is None:
            return httpx.Response(404, json={"message": f"no route {key}", "code": "NOT_FOUND"})
        if callable(body):
            return body(request)
        return httpx.Response(200, json=body)


@pytest.fixture
def server() -> _Server:
    return _Server()


@pytest.fixture
def sync_client(server: _Server):
    client = Daytona(DaytonaConfig(api_key="k"))
    mock = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    impl = client._impl
    # the transports Daytona(config) built eagerly are replaced below; close
    # them too or their (never-used) pools outlive the test
    originals = (impl._api, impl._toolbox)
    impl._api = AsyncTransport("http://api.invalid", "k", client=mock)
    impl._toolbox = AsyncTransport(None, None, client=mock)
    try:
        yield client
    finally:
        # an injected client is the caller's to close (the SDK never closes
        # what it did not create), so close it on the bridge before the bridge goes
        if not client._bridge.closed:

            async def release() -> None:
                await mock.aclose()
                for transport in originals:
                    await transport.close()

            client._bridge.call(release)
        client.close()


def test_call_shape_blocks_and_returns_the_plain_value(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    sandbox = sync_client.get("sb-1")
    assert type(sandbox).__name__ == "Sandbox"
    assert sandbox.id == "sb-1"
    assert sandbox.state == "started"


def test_handle_shape_wraps_returned_sdk_objects_recursively(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    server.routes[("POST", "/sandboxes/sb-1/processes")] = _process("p-1")
    sandbox = sync_client.get("sb-1")
    handle = sandbox.process.start("true")
    assert type(handle).__name__ == "ProcessHandle"
    assert type(sandbox.process).__name__ == "ProcessService"
    assert handle.process_id == "p-1"
    assert handle.to_json() == {"sandboxId": "sb-1", "processId": "p-1", "kind": "exec"}


def test_container_shape_wraps_pages_of_handles(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes")] = _sandboxes(_sandbox("a"), _sandbox("b"))
    page = sync_client.list()
    assert isinstance(page, ListResult)
    assert [type(item).__name__ for item in page.items] == ["Sandbox", "Sandbox"]
    assert [item.id for item in page.items] == ["a", "b"]


def test_search_shape_wraps_items_and_keeps_every_page_field(server: _Server, sync_client: Daytona) -> None:
    """SearchResult and filtered list() are pages too: their items must be
    Sync twins, and total / total_relation must survive."""
    from daytona.v2.types import SearchResult

    server.routes[("GET", "/sandboxes/search")] = {
        "sandboxes": [_sandbox("a"), _sandbox("b")],
        "next_cursor": "c2",
        "total": 41,
        "total_relation": "gte",
    }

    found = sync_client.search(q="worker")
    assert isinstance(found, SearchResult)
    assert [type(item).__name__ for item in found.items] == ["Sandbox", "Sandbox"]
    assert (found.next_cursor, found.total, found.total_relation) == (
        "c2",
        41,
        "gte",
    )

    page = sync_client.search(states=["started"])
    assert isinstance(page, SearchResult)
    assert [type(item).__name__ for item in page.items] == ["Sandbox", "Sandbox"]
    assert (page.total, page.total_relation) == (41, "gte")


def test_iterator_shape_streams_lazily(server: _Server, sync_client: Daytona) -> None:
    pages = iter([_sandboxes(_sandbox("a"), next_cursor="c2"), _sandboxes(_sandbox("b"))])
    server.routes[("GET", "/sandboxes")] = lambda request: httpx.Response(200, json=next(pages))
    iterator = sync_client.iter()
    assert not isinstance(iterator, list)
    assert next(iterator).id == "a"
    assert len(server.calls) == 1, "second page fetched before it was pulled"
    assert next(iterator).id == "b"
    assert len(server.calls) == 2


def test_property_shape_passes_data_through(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    sandbox = sync_client.get("sb-1")
    assert (sandbox.id, sandbox.region, sandbox.memory) == ("sb-1", "region-b", 1.0)
    assert sandbox.sandbox_class == "linux"
    # The toolbox URL is a transport detail, not handle metadata.
    assert not hasattr(sandbox, "toolbox_proxy_url")


def test_sync_create_uses_the_create_response_toolbox_url_without_a_detail_fetch(
    server: _Server, sync_client: Daytona
) -> None:
    server.routes[("POST", "/sandboxes")] = {
        "access_token": "create-token",
        "status": "running",
        "toolbox_proxy_url": "http://toolbox.invalid/sandboxes",
        "id": "sb-1",
        "wait_cap_seconds": 60,
    }
    server.routes[("GET", "/sandboxes/sb-1/system/metrics")] = {
        "timestamp": "2026-10-08T00:00:00Z",
        "cpuCount": 7,
        "cpuUtilization": 0,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }

    sandbox = sync_client.create("python:3.12", wait_seconds=60)

    assert sandbox.metrics().cpu_count == 7
    assert server.calls == [("POST", "/sandboxes"), ("GET", "/sandboxes/sb-1/system/metrics")]


def test_sync_fork_takes_its_toolbox_from_the_fork_response(server: _Server, sync_client: Daytona) -> None:
    server.routes[("POST", "/sandboxes/parent/fork")] = {
        "forked_from": "parent",
        "status": "running",
        "toolbox_proxy_url": "http://toolbox.invalid/sandboxes",
        "id": "forked",
    }
    # A distinct detail URL: the toolbox must come from the fork response.
    server.routes[("GET", "/sandboxes/forked")] = _sandbox(
        "forked", toolbox_proxy_url="http://detail.invalid/elsewhere"
    )
    server.routes[("POST", "/sandboxes/forked/access-token")] = {
        "access_token": "jwt",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00Z",
        "operations": [],
    }
    server.routes[("GET", "/sandboxes/forked/system/metrics")] = {
        "timestamp": "2026-10-08T00:00:00Z",
        "cpuCount": 8,
        "cpuUtilization": 0,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }

    sandbox = lazy_sandbox(sync_client, "parent", toolbox_url="http://10.0.0.7:2280")
    sandbox._impl._update(sandbox_class="linux")
    forked = sandbox.fork(wait_seconds=0)

    assert forked.metrics().cpu_count == 8
    assert server.calls == [
        ("POST", "/sandboxes/parent/fork"),
        ("POST", "/sandboxes/forked/access-token"),
        ("GET", "/sandboxes/forked/system/metrics"),
    ]


def test_sync_list_without_toolbox_proxy_url_fetches_detail_once_before_building_the_handle(
    server: _Server, sync_client: Daytona
) -> None:
    server.routes[("GET", "/sandboxes")] = _sandboxes(_sandbox("sb-1", toolbox_proxy_url=None))
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    server.routes[("POST", "/sandboxes/sb-1/access-token")] = {
        "access_token": "jwt",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00Z",
        "operations": [],
    }
    server.routes[("GET", "/sandboxes/sb-1/system/metrics")] = {
        "timestamp": "2026-10-08T00:00:00Z",
        "cpuCount": 6,
        "cpuUtilization": 0,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }

    sandbox = sync_client.list().items[0]

    assert sandbox.metrics().cpu_count == 6
    assert server.calls == [
        ("GET", "/sandboxes"),
        ("GET", "/sandboxes/sb-1"),
        ("POST", "/sandboxes/sb-1/access-token"),
        ("GET", "/sandboxes/sb-1/system/metrics"),
    ]


def test_sync_lazy_sandbox_toolbox_calls_raise_the_guidance_message(sync_client: Daytona) -> None:
    with pytest.raises(
        Exception,
        match=r"sandbox handle has no toolbox URL; fetch the sandbox with get\(id\) \(or pass toolbox_url\)",
    ):
        lazy_sandbox(sync_client, "lazy").metrics()


def test_sync_daytona_toolbox_url_env_has_no_effect(monkeypatch: pytest.MonkeyPatch, server: _Server) -> None:
    monkeypatch.setenv("DAYTONA_TOOLBOX_URL", "http://ignored.invalid/sandboxes")
    client = Daytona(DaytonaConfig(api_key="k"))
    mock = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    impl = client._impl
    originals = (impl._api, impl._toolbox)
    impl._api = AsyncTransport("http://api.invalid", "k", client=mock)
    impl._toolbox = AsyncTransport(None, None, client=mock)
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    server.routes[("POST", "/sandboxes/sb-1/access-token")] = {
        "access_token": "jwt",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00Z",
        "operations": [],
    }
    server.routes[("GET", "/sandboxes/sb-1/system/metrics")] = {
        "timestamp": "2026-10-08T00:00:00Z",
        "cpuCount": 5,
        "cpuUtilization": 0,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }

    try:
        assert client.get("sb-1").metrics().cpu_count == 5
        with pytest.raises(
            Exception,
            match=r"sandbox handle has no toolbox URL; fetch the sandbox with get\(id\) \(or pass toolbox_url\)",
        ):
            lazy_sandbox(client, "lazy").metrics()
    finally:
        if not client._bridge.closed:

            async def release() -> None:
                await mock.aclose()
                for transport in originals:
                    await transport.close()

            client._bridge.call(release)
        client.close()


def test_rehydration_shape_round_trips_across_surfaces(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    server.routes[("POST", "/sandboxes/sb-1/processes")] = _process("p-1")
    serialized = sync_client.get("sb-1").process.start("true").to_json()

    async_handle = AsyncProcessHandle.from_json(serialized)
    assert async_handle.process_id == "p-1"

    rehydrated = sync_client.get("sb-1").process.connect(serialized)
    assert type(rehydrated).__name__ == "ProcessHandle"
    assert rehydrated.to_json() == serialized


def test_argument_shape_unwraps_sync_handles_passed_back_in(server: _Server, sync_client: Daytona) -> None:
    """The inverse direction: a Sync handle given to the SDK must reach the
    async implementation as the async object it wraps."""
    states = iter(["running", "deleting", "deleted"])
    server.routes[("GET", "/sandboxes/sb-1")] = lambda request: httpx.Response(
        200, json=_sandbox("sb-1", next(states))
    )
    server.routes[("DELETE", "/sandboxes/sb-1")] = {}
    sandbox = sync_client.get("sb-1")
    assert type(sandbox).__name__ == "Sandbox"

    sync_client.delete(sandbox)  # would TypeError on an un-unwrapped wrapper

    assert ("DELETE", "/sandboxes/sb-1") in server.calls
    assert sandbox.state == "destroyed"


def _leaves(value: object) -> list[object]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, (list, tuple, set)):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


def test_unwrap_reaches_handles_nested_in_every_container_shape(
    server: _Server, sync_client: Daytona
) -> None:
    """No method takes a dict/set of handles today; when one does, the erased
    unwrap must already reach into it rather than pass wrappers through."""
    from daytona.v2._sync._runtime import unwrap as _unwrap
    from daytona.v2.sandbox import AsyncSandbox

    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    sandbox = sync_client.get("sb-1")

    for container in ([sandbox], (sandbox,), {sandbox}, {"k": sandbox}, [[sandbox]], {"k": (sandbox,)}):
        leaves = _leaves(_unwrap(container))
        assert leaves and all(type(leaf) is AsyncSandbox for leaf in leaves), (
            f"wrapper leaked through {type(container)}"
        )


def test_rehydrated_sync_handles_need_no_bridge_until_connected(
    server: _Server, sync_client: Daytona
) -> None:
    """`ProcessHandle.from_json(...)` mirrors the async classmethod exactly:
    identity round-trips with no client; I/O before connecting raises the
    SDK's own not-bound error; connecting through a client binds it."""
    from daytona.v2 import ProcessHandle
    from daytona.v2.errors import BadRequestError

    serialized = {"sandboxId": "sb-1", "processId": "p-1", "kind": "exec"}
    unbound = ProcessHandle.from_json(serialized)
    assert unbound.to_json() == serialized
    assert unbound.ref().process_id == "p-1"
    with pytest.raises(BadRequestError, match="not bound"):
        unbound.wait(timeout=1)

    server.routes[("GET", "/sandboxes/sb-1/processes/p-1")] = {
        "id": "p-1",
        "kind": "exec",
        "state": "terminal",
        "createdAt": "2026-01-01T00:00:00Z",
        "cwd": "/",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "truncatedHead": False,
    }
    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")
    bound = sync_client.get("sb-1").process.connect(unbound.to_json())
    assert bound.get().id == "p-1"


def test_close_from_a_loop_thread_callback_still_releases_http_pools() -> None:
    """A sync callback the loop is running may call client.close(). It must not
    raise, must close the SDK's HTTP pools BEFORE the loop stops, and the loop
    thread must end."""
    client = Daytona(DaytonaConfig(api_key="k"))
    closed: list[str] = []
    impl = client._impl
    original_api, original_toolbox = impl._api.close, impl._toolbox.close

    async def spy_api() -> None:
        closed.append("api")
        await original_api()

    async def spy_toolbox() -> None:
        closed.append("toolbox")
        await original_toolbox()

    impl._api.close = spy_api  # type: ignore[method-assign]
    impl._toolbox.close = spy_toolbox  # type: ignore[method-assign]

    async def callback_on_loop() -> str:
        client.close()
        return "returned"

    assert client._bridge.call(callback_on_loop) == "returned"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (
        sorted(closed) != ["api", "toolbox"] or any("portal" in t.name for t in threading.enumerate())
    ):
        time.sleep(0.02)
    assert sorted(closed) == ["api", "toolbox"], f"HTTP pools not released on in-loop close: {closed}"
    assert not any("portal" in t.name for t in threading.enumerate()), "loop thread left alive"
    assert client._bridge.closed


def test_close_is_idempotent_including_inside_a_with_block(server: _Server) -> None:
    client = Daytona(DaytonaConfig(api_key="k"))
    client.close()
    client.close()  # second close must be a no-op, not BridgeError

    with Daytona(DaytonaConfig(api_key="k")) as inner:
        inner.close()  # explicit close, then __exit__ closes again


def test_context_manager_shape_closes_on_exit(server: _Server) -> None:
    with Daytona(DaytonaConfig(api_key="k")) as client:
        assert client._bridge is not None
    with pytest.raises(BridgeError, match="closed"):
        client._bridge.call(lambda: client._impl.list())


def test_typed_errors_cross_the_bridge_unchanged(server: _Server, sync_client: Daytona) -> None:
    from daytona.v2.errors import NotFoundError

    with pytest.raises(NotFoundError) as caught:
        sync_client.get("missing")
    assert caught.value.status_code == 404


def test_sync_client_refuses_to_run_inside_an_event_loop(server: _Server, sync_client: Daytona) -> None:
    import asyncio

    server.routes[("GET", "/sandboxes/sb-1")] = _sandbox("sb-1")

    async def misuse() -> None:
        sync_client.get("sb-1")

    with pytest.raises(BridgeError, match="running event loop"):
        asyncio.run(misuse())


def test_sync_handles_are_cheap_and_share_one_bridge(server: _Server, sync_client: Daytona) -> None:
    server.routes[("GET", "/sandboxes")] = _sandboxes(*(_sandbox(f"sb-{i}") for i in range(200)))
    before = threading.active_count()
    page = sync_client.list()
    assert len(page.items) == 200
    assert all(item._bridge is sync_client._bridge for item in page.items)
    assert all(item.process._bridge is sync_client._bridge for item in page.items)
    assert threading.active_count() - before <= 1, "sandbox handles must not spawn threads or loops"


def test_async_and_sync_surfaces_are_the_same_implementation() -> None:
    assert sync_module.Daytona._async_type is AsyncDaytona
    assert sync_module.ProcessService._async_type is AsyncProcessService
    assert isinstance(Daytona(_config_at("http://x.invalid", api_key="k"))._bridge, SyncBridge)
