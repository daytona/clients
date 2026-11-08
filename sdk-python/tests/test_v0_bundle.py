# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The bundled v0 namespace (`daytona.v0`) at the pinned 0.x tag.

Skipped in a source checkout, where nothing is bundled; sdk-ci's
`python-v0-bundle` job and the publish job run it after
`scripts/bundle-v0.sh` and `pip install ".[v0-sdk]"`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _repo import _repo_file

if TYPE_CHECKING:
    from daytona.v0 import _Daytona  # type: ignore[import-not-found]

v0 = pytest.importorskip("daytona.v0", reason="the v0 namespace is bundled at publish time")


HEADERS: dict[str, dict[str, str]] = {}


@pytest.fixture
def v0_api() -> Iterator[tuple[str, list[tuple[str, str, dict[str, object]]]]]:
    """A local stand-in for the v0 API that records requests and refuses them."""
    seen: list[tuple[str, str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            seen.append((self.command, self.path, json.loads(raw) if raw else {}))
            HEADERS[self.path] = {k.lower(): v for k, v in self.headers.items()}
            body = json.dumps({"statusCode": 400, "message": "recorded", "error": "Bad Request"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _record

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def _client(api_url: str, monkeypatch: pytest.MonkeyPatch) -> _Daytona:
    """The root's v0 client (the only way to build one in SDK 2.x), pointed at
    the local stand-in through the non-production DAYTONA_V0_API_URL."""
    from daytona import Daytona, DaytonaConfig

    monkeypatch.setenv("DAYTONA_V0_API_URL", api_url)
    return Daytona(DaytonaConfig(api_key="dtn_test")).v0


def test_a_per_call_target_is_sent_in_the_create_body(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    api_url, seen = v0_api
    client = _client(api_url, monkeypatch)
    with pytest.raises(v0.DaytonaError):
        client.create(v0.CreateSandboxFromSnapshotParams(target="eu"), timeout=5)
    creates = [body for method, path, body in seen if method == "POST" and path.startswith("/sandbox")]
    assert creates, seen
    assert creates[-1].get("target") == "eu"


def test_v0_reports_the_bundled_release_as_its_sdk_version(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parity with the TS bundle: X-Daytona-SDK-Version is the bundled v0
    release (not the 2.x distribution's version), and User-Agent carries it."""
    from daytona.v0._bundle import BUNDLED_VERSION  # type: ignore[import-not-found]

    api_url, seen = v0_api
    HEADERS.clear()
    with pytest.raises(v0.DaytonaError):
        _client(api_url, monkeypatch).create(v0.CreateSandboxFromSnapshotParams(), timeout=5)
    path = next(path for method, path, _ in seen if method == "POST" and path.startswith("/sandbox"))
    headers = HEADERS[path]
    assert headers["x-daytona-sdk-version"] == BUNDLED_VERSION
    assert headers.get("user-agent") == f"sdk-python/{BUNDLED_VERSION}"


def test_a_per_call_region_id_is_sent_in_the_snapshot_create_body(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    api_url, seen = v0_api
    client = _client(api_url, monkeypatch)
    with pytest.raises(v0.DaytonaError):
        client.snapshot.create(
            v0.CreateSnapshotParams(name="snap", image="python:3.12", region_id="eu-region"), timeout=5
        )
    creates = [body for method, path, body in seen if method == "POST" and path.startswith("/snapshots")]
    assert creates, seen
    assert creates[-1].get("regionId") == "eu-region"


def test_the_bundle_is_the_pinned_release() -> None:
    import os

    from daytona.v0._bundle import BUNDLED_FROM  # type: ignore[import-not-found]

    # The publish job passes the tag its gate approved (the pin, or an
    # allow_unpinned_v0 override); otherwise the bundle must be the pin.
    expected = os.environ.get("DAYTONA_V0_EXPECTED_TAG") or (
        (_repo_file("scripts/v0-sdk-version")).read_text().splitlines()[0].strip()
    )
    assert expected == BUNDLED_FROM.rsplit("@", 1)[1]


def test_root_v0_sends_to_daytona_v0_api_url_not_daytona_api_url(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daytona().v0 builds the real bundled client against DAYTONA_V0_API_URL;
    DAYTONA_API_URL (the 0.x SDK's variable) points somewhere that would fail if used."""
    from daytona import Daytona, DaytonaConfig

    api_url, seen = v0_api
    monkeypatch.setenv("DAYTONA_V0_API_URL", api_url)
    monkeypatch.setenv("DAYTONA_API_URL", "http://v2-only.invalid")
    with pytest.raises(v0.DaytonaError):
        Daytona(DaytonaConfig(api_key="dtn_test")).v0.create(v0.CreateSandboxFromSnapshotParams(), timeout=5)
    assert any(method == "POST" and path.startswith("/sandbox") for method, path, _ in seen), seen


def test_the_root_migration_list_matches_the_bundled_v0_root() -> None:
    # daytona.__getattr__ guides `from daytona import X` for exactly these names;
    # a pin bump that changes the 0.x root must update _v0_root_names.py.
    from daytona._v0_root_names import V0_ROOT_NAMES

    client_names = {"AsyncDaytona", "Daytona", "DaytonaConfig"}
    bundled = set(v0.__all__)
    assert V0_ROOT_NAMES - client_names <= bundled
    assert not client_names & bundled
    assert all(name.endswith("V0") for name in bundled - V0_ROOT_NAMES)


@pytest.mark.parametrize(
    ("name", "root_form"),
    [
        ("Daytona", "Daytona().v0"),
        ("AsyncDaytona", "AsyncDaytona().v0"),
        ("DaytonaConfig", "Daytona(DaytonaConfig(...)).v0"),
        ("DaytonaV0", "Daytona().v0"),
        ("AsyncDaytonaV0", "AsyncDaytona().v0"),
    ],
)
def test_the_v0_client_classes_are_not_exported(name: str, root_form: str) -> None:
    """SDK 2.x builds a v0 client only through the root: the old
    import raises an ImportError naming the root form, in a fresh interpreter
    so no earlier access has cached anything."""
    import subprocess
    import sys

    script = f"try:\n    from daytona.v0 import {name}\nexcept ImportError as error:\n    print(error)\n"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    assert out.startswith(f"`daytona.v0.{name}` is not exported"), out
    assert f"`{root_form}`" in out
    assert "stay on SDK 0.x" in out
    with pytest.raises(ImportError, match="is not exported"):
        getattr(v0, name)
    assert name not in dir(v0)


def test_the_root_builds_v0_from_the_private_client_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona import AsyncDaytona, Daytona, DaytonaConfig

    monkeypatch.delenv("DAYTONA_V0_API_URL", raising=False)
    with Daytona(DaytonaConfig(api_key="dtn_test")) as root:
        assert type(root.v0) is v0._Daytona
        assert type(root.v0).__name__ == "Daytona"
    assert type(AsyncDaytona(DaytonaConfig(api_key="dtn_test")).v0) is v0._AsyncDaytona
    from daytona import AsyncDaytonaV0Client, DaytonaV0Client

    with Daytona(DaytonaConfig(api_key="dtn_test")) as root:
        assert isinstance(root.v0, DaytonaV0Client)
    assert isinstance(AsyncDaytona(DaytonaConfig(api_key="dtn_test")).v0, AsyncDaytonaV0Client)


def test_the_v0_client_types_cannot_build_a_client_with_a_custom_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A directly built v0 client would read DAYTONA_API_URL (the path
    rejected); the annotation types refuse to build one."""
    from daytona import AsyncDaytonaV0Client, DaytonaV0Client

    monkeypatch.setenv("DAYTONA_API_URL", "https://selfhosted.example.com/api")
    for cls in (DaytonaV0Client, AsyncDaytonaV0Client):
        with pytest.raises(TypeError, match="not a constructor"):
            cls()
    # Everything else stays importable from the namespace.
    from daytona.v0 import CreateSandboxFromSnapshotParams, DaytonaNotFoundError, Sandbox, SandboxV0

    assert SandboxV0 is Sandbox
    assert issubclass(DaytonaNotFoundError, v0.DaytonaError)
    assert CreateSandboxFromSnapshotParams is not None


def test_from_daytona_import_v0_returns_the_bundle() -> None:
    from daytona import v0 as loaded

    assert loaded is v0


def test_volume_mount_is_shared_with_v0_under_version_aliases() -> None:
    # Given VolumeMount in both namespaces, each has a version-suffixed alias.
    from daytona import v2

    assert v0.VolumeMount is not None
    assert "VolumeMount" in v0.__all__
    assert "VolumeMountV0" in v0.__all__
    assert v0.VolumeMountV0 is v0.VolumeMount
    assert v2.VolumeMountV2 is v2.VolumeMount


def test_a_v0_type_import_without_the_extra_names_the_extra() -> None:
    # `from daytona.v0 import Sandbox` is what the migration text tells users to
    # write; without the extra it must name it, like d.v0 does. Run in a fresh
    # interpreter with one of the extra's dependencies made unimportable.
    import subprocess
    import sys

    script = (
        "import sys\n"
        "sys.modules['deprecated'] = None\n"
        "try:\n"
        "    from daytona.v0 import Sandbox\n"
        "except ImportError as error:\n"
        "    print(type(error).__name__, error)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    assert 'pip install "daytona[v0-sdk]"' in out
    assert "'deprecated'" in out


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
def test_d_v0_without_a_dependency_of_the_extra_prints_the_hint_once(client: str) -> None:
    # Real bundle, one of the extra's dependencies unimportable: the message is
    # the approved hint, not double-prefixed by _load_v0.
    import subprocess
    import sys

    script = (
        "import sys\n"
        "sys.modules['opentelemetry'] = None\n"
        f"from daytona import {client}, DaytonaConfig\n"
        "try:\n"
        f"    {client}(DaytonaConfig(api_key='k')).v0\n"
        "except ImportError as error:\n"
        "    print(error)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    assert out.startswith(
        'The v0 namespace needs the "v0-sdk" extra: pip install "daytona[v0-sdk]" (importing the v0 namespace failed on '
    )
    assert "'opentelemetry'" in out
    assert out.count("importing the v0 namespace failed") == 1


def test_the_bundle_carries_v0s_license_and_notice() -> None:
    from pathlib import Path

    root = Path(v0.__file__).parent
    for name in ("LICENSE", "NOTICE"):
        assert (root / name).is_file(), f"daytona/v0/{name} missing from the bundle"


def test_closing_the_root_releases_the_sync_v0_event_socket(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bundled 0.x sync Daytona has no close(); the root's close() must still
    release its socket.io dispatcher and subscription worker."""
    from daytona import Daytona, DaytonaConfig

    api_url, _ = v0_api
    monkeypatch.setenv("DAYTONA_V0_API_URL", api_url)
    with Daytona(DaytonaConfig(api_key="dtn_test")) as root:
        client = root.v0
        assert not callable(getattr(client, "close", None))
        dispatcher = client._event_dispatcher
        manager = client._subscription_manager
    assert dispatcher._closed is True
    assert dispatcher._sio is None
    assert manager._closed is True


def test_async_v0_reports_the_bundled_release_as_its_sdk_version(
    v0_api: tuple[str, list[tuple[str, str, dict[str, object]]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async 0.x client's version lookup is rewritten too (both _sync and
    _async import importlib.metadata.version)."""
    import asyncio

    from daytona.v0._bundle import BUNDLED_VERSION  # type: ignore[import-not-found]

    api_url, seen = v0_api
    HEADERS.clear()

    from daytona import AsyncDaytona, DaytonaConfig

    monkeypatch.setenv("DAYTONA_V0_API_URL", api_url)

    async def main() -> None:
        async with AsyncDaytona(DaytonaConfig(api_key="dtn_test")) as root:
            with pytest.raises(v0.DaytonaError):
                await root.v0.create(v0.CreateSandboxFromSnapshotParams(), timeout=5)

    asyncio.run(main())
    path = next(path for method, path, _ in seen if method == "POST" and path.startswith("/sandbox"))
    assert HEADERS[path]["x-daytona-sdk-version"] == BUNDLED_VERSION


def test_the_dotenv_warning_names_remedies_that_work_under_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0's dotenv warning must not tell a root user to pass api_url (the root
    rejects it) or set DAYTONA_API_URL (SDK 2.x, .v0 included, never reads it)."""
    import warnings

    from daytona import Daytona, DaytonaConfig

    (tmp_path / ".env").write_text("DAYTONA_API_URL=http://dotenv.invalid\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAYTONA_API_URL", raising=False)
    monkeypatch.delenv("DAYTONA_V0_API_URL", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with Daytona(DaytonaConfig(api_key="dtn_test")) as root:
            assert root.v0 is not None
    messages = [str(w.message) for w in caught if "ignored" in str(w.message)]
    assert messages, [str(w.message) for w in caught]
    assert "For a custom API URL, stay on SDK 0.x." in messages[0]
    assert "daytona.v0.Daytona" not in messages[0]
    assert "DAYTONA_V0_API_URL" in messages[0]
    assert "pass `api_url` to `DaytonaConfig`" not in messages[0]
