# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The package root: clients, config, errors; a namespace per API version.

Version namespaces: `Daytona().v0` / `.v2` are lazy and cached, the
root has no resource methods, old root imports fail with directions, and a
v2-only program never imports v0 code.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
import types
import warnings
from pathlib import Path

import pytest

import daytona
from daytona import (
    DEFAULT_V2_API_URL,
    V0_API_URL,
    AsyncDaytona,
    Daytona,
    DaytonaConfig,
    DaytonaConflictError,
    DaytonaError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
    _errors,
)
from daytona import v2 as daytona_v2
from daytona._client import _V0_EXTRA_HINT
from daytona.v2 import _sync as daytona_v2_sync
from daytona.v2 import daytona as daytona_v2_async
from daytona.v2 import types as daytona_v2_types

ROOT = Path(__file__).resolve().parents[1]


def _repo_scripts() -> Path:
    """The repository's ``scripts/``, which the release-script tests drive.

    An sdist has only the SDK, so those tests skip there.
    """
    scripts = ROOT.parent / "scripts"
    if not scripts.is_dir():
        pytest.skip("the repository's scripts/ is not in this tree (an sdist)")
    return scripts


def test_root_exports_only_clients_config_constants_and_errors() -> None:
    non_errors = {
        name
        for name in daytona.__all__
        if not (
            isinstance(getattr(daytona, name), type) and issubclass(getattr(daytona, name), BaseException)
        )
    }
    assert non_errors == {
        "AsyncDaytona",
        "AsyncDaytonaV2Client",
        "Daytona",
        "DaytonaConfig",
        "DaytonaV2Client",
        "DEFAULT_V2_API_URL",
        "V0_API_URL",
    }
    # The root errors are the shared hierarchy, not v2's classes.
    errors = set(daytona.__all__) - non_errors
    assert errors == set(_errors.SHARED_ERRORS)
    assert DaytonaError is not daytona_v2.DaytonaError


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("DaytonaValidationError", "DaytonaBadRequestError"),
        ("DaytonaAuthorizationError", "DaytonaForbiddenError"),
    ],
)
def test_removed_root_error_synonyms_name_the_surviving_category_and_daytona_v0(
    name: str, category: str
) -> None:
    # Both were 0.x root names: the guidance names the root category to catch
    # (as the migration tool's note does) and the 0.x class in daytona.v0.
    with pytest.raises(ImportError) as caught:
        getattr(daytona, name)
    message = str(caught.value)
    assert f"use the root `{category}`" in message, message
    assert f"from daytona.v0 import {name}" in message, message
    assert category in daytona.__all__
    # The pairing matches the migration tool's table, when this checkout has it
    # (the sdist does not).
    if _UPGRADE_MAPPING.is_file():
        mapping = json.loads(_UPGRADE_MAPPING.read_text(encoding="utf-8"))
        assert mapping["rootAliasCategories"][name] == category


@pytest.mark.parametrize(
    "name",
    [
        "DaytonaAuthenticationError",
        "DaytonaAuthorizationError",
        "DaytonaConflictError",
        "DaytonaConnectionError",
        "DaytonaNotFoundError",
        "DaytonaNotSupportedError",
        "DaytonaRateLimitError",
        "DaytonaTimeoutError",
        "DaytonaValidationError",
        "UnauthorizedError",
    ],
)
def test_v2_exports_no_legacy_error_synonyms(name: str) -> None:
    assert name not in daytona_v2.__all__
    assert not hasattr(daytona_v2, name)
    assert not hasattr(daytona_v2, f"{name}V2")


def test_v2_errors_are_caught_by_the_shared_root_classes() -> None:
    error = daytona_v2.NotFoundError("missing", status_code=404, code="NOT_FOUND")
    with pytest.raises(DaytonaNotFoundError):
        raise error
    with pytest.raises(DaytonaError):
        raise daytona_v2.ProcessNotFoundError("gone")
    assert not isinstance(error, DaytonaConflictError)
    assert error.code == "NOT_FOUND"  # the v2 class keeps its own attributes
    for timeout in (daytona_v2.RequestTimeoutError("t"), daytona_v2.ClientTimeoutError("t")):
        assert isinstance(timeout, DaytonaTimeoutError)
    assert issubclass(daytona_v2.ForbiddenError, daytona.DaytonaForbiddenError)
    assert issubclass(daytona_v2.AuthenticationError, daytona.DaytonaAuthenticationError)
    assert issubclass(daytona_v2.ConnectionError, daytona.DaytonaConnectionError)
    # v2-only categories are not shared: NotSupportedError is v2's InternalServerError.
    assert issubclass(daytona_v2.NotSupportedError, daytona.DaytonaInternalServerError)


def test_v0_errors_rewritten_by_the_bundle_are_caught_by_the_shared_root_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the bundle's errors rewrite on a v0-shaped module and catch its errors at the root."""
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    common = tmp_path / "common"
    common.mkdir()
    source = "class DaytonaError(Exception):\n    pass\n\n\n" + "".join(
        f"class {name}(DaytonaError):\n    pass\n\n\n"
        for name in bundle_v0_python.V0_SHARED_ERRORS.values()
        if name != "DaytonaError"
    )
    (common / "errors.py").write_text(source)
    bundle_v0_python._share_errors(tmp_path)
    # Import it the way the bundle ships it, one level under `daytona` (the
    # rewrite's `from ... import _errors` resolves to daytona._errors), under
    # a probe name so the real daytona.v0 bundle is never shadowed.
    probe = "daytona._v0_rewrite_probe"
    (tmp_path / "__init__.py").write_text("")
    (common / "__init__.py").write_text("")
    spec = importlib.util.spec_from_file_location(probe, tmp_path / "__init__.py")
    assert spec is not None
    package = importlib.util.module_from_spec(spec)
    package.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, probe, package)
    try:
        errors = importlib.import_module(f"{probe}.common.errors")
    finally:
        for name in [n for n in sys.modules if n.startswith(f"{probe}.")]:
            del sys.modules[name]
    with pytest.raises(DaytonaNotFoundError):
        raise errors.DaytonaNotFoundError("v0 missing")
    with pytest.raises(DaytonaError):
        raise errors.DaytonaNotFoundError("v0 missing")
    assert issubclass(errors.DaytonaForbiddenError, daytona.DaytonaForbiddenError)
    assert issubclass(errors.DaytonaError, daytona.DaytonaError)


def test_the_bundle_errors_rewrite_refuses_a_class_already_inheriting_a_marker(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    (tmp_path / "common").mkdir()
    source = "class DaytonaError(Exception):\n    pass\n\n\n" + "".join(
        f"class {name}(DaytonaError):\n    pass\n\n\n"
        for name in bundle_v0_python.V0_SHARED_ERRORS.values()
        if name != "DaytonaError"
    )
    source = source.replace(
        "class DaytonaNotFoundError(DaytonaError):",
        "class DaytonaNotFoundError(DaytonaError, _shared_errors.DaytonaNotFoundError):",
    )
    (tmp_path / "common" / "errors.py").write_text(source)
    with pytest.raises(SystemExit, match="already inherits a root marker"):
        bundle_v0_python._share_errors(tmp_path)


def test_the_bundle_errors_self_check_rejects_a_duplicate_base() -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    lines = [
        "from ... import _errors as _shared_errors",
        "class DaytonaError(_shared_errors.DaytonaError): pass",
    ]
    for marker, name in bundle_v0_python.V0_SHARED_ERRORS.items():
        if name != "DaytonaError":
            lines.append(
                f"class {name}(DaytonaError, _shared_errors.{marker}, _shared_errors.{marker}): pass"
            )
    with pytest.raises(SystemExit, match="inconsistent"):
        bundle_v0_python._check_shared_errors("\n".join(lines) + "\n")


def test_the_bundle_errors_rewrite_fails_on_a_missing_class(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "errors.py").write_text("class DaytonaError(Exception):\n    pass\n")
    with pytest.raises(SystemExit, match="DaytonaNotFoundError"):
        bundle_v0_python._share_errors(tmp_path)


def _bundler() -> types.ModuleType:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    return bundle_v0_python


def test_bundler_fails_when_the_sdk_version_lookup_is_missing(tmp_path: Path) -> None:
    bundle_v0_python = _bundler()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("import json\n")
    with pytest.raises(SystemExit, match="SDK-version lookup was not found"):
        bundle_v0_python._pin_sdk_version(tmp_path, "0.224.0")


def test_bundler_pins_the_sdk_version_lookup(tmp_path: Path) -> None:
    bundle_v0_python = _bundler()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(bundle_v0_python._METADATA_IMPORT)
    assert bundle_v0_python._pin_sdk_version(tmp_path, "0.224.0") == ["pkg/mod.py"]
    assert "from .._bundle import bundled_version as version" in (tmp_path / "pkg" / "mod.py").read_text()


_V0_INIT_SHAPE = """from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._async.daytona import AsyncDaytona
    from ._sync.daytona import Daytona
    from .common.daytona import (
        CodeLanguage,
        DaytonaConfig,
    )

__all__ = [
    "Daytona",
    "DaytonaConfig",
    "CodeLanguage",
    "AsyncDaytona",
]

_DYNAMIC_IMPORTS: dict[str, str] = {
    "Daytona": "_sync.daytona",
    "AsyncDaytona": "_async.daytona",
    "CodeLanguage": "common.daytona",
    "DaytonaConfig": "common.daytona",
}
"""


def test_bundler_hides_the_v0_client_classes(tmp_path: Path) -> None:
    """The v0.224.0 shape of daytona/__init__.py: each client class leaves
    __all__ and the lazy table and is imported under its private name."""
    bundle_v0_python = _bundler()
    (tmp_path / "__init__.py").write_text(_V0_INIT_SHAPE)
    hidden = bundle_v0_python._hide_clients(tmp_path)
    assert hidden == {
        "_AsyncDaytona": ("_async.daytona", "AsyncDaytona"),
        "_Daytona": ("_sync.daytona", "Daytona"),
        "_DaytonaConfig": ("common.daytona", "DaytonaConfig"),
    }
    text = (tmp_path / "__init__.py").read_text()
    assert "from ._sync.daytona import Daytona as _Daytona\n" in text
    assert "from ._async.daytona import AsyncDaytona as _AsyncDaytona\n" in text
    assert "        DaytonaConfig as _DaytonaConfig,\n" in text
    assert bundle_v0_python._public_names(tmp_path / "__init__.py") == {"CodeLanguage"}
    assert '"CodeLanguage": "common.daytona"' in text
    assert '"Daytona":' not in text and '"DaytonaConfig":' not in text


def test_bundler_fails_when_a_v0_client_class_cannot_be_hidden(tmp_path: Path) -> None:
    bundle_v0_python = _bundler()
    (tmp_path / "__init__.py").write_text(
        _V0_INIT_SHAPE.replace('    "AsyncDaytona": "_async.daytona",\n', "")
    )
    with pytest.raises(SystemExit, match=r"_DYNAMIC_IMPORTS entry 'AsyncDaytona' \(0 matches\)"):
        bundle_v0_python._hide_clients(tmp_path)
    assert (tmp_path / "__init__.py").read_text() == _V0_INIT_SHAPE.replace(
        '    "AsyncDaytona": "_async.daytona",\n', ""
    )


def test_root_clients_have_no_resource_methods() -> None:
    for cls in (Daytona, AsyncDaytona):
        public = {name for name in vars(cls) if not name.startswith("_")}
        assert public == {"v0", "v2", "close"}, cls
    for name in ("create", "list", "get", "delete", "images"):
        assert not hasattr(Daytona(DaytonaConfig(api_key="k")), name)


_UPGRADE_MAPPING = Path(__file__).resolve().parents[2] / "upgrade" / "mapping" / "mapping.json"


@pytest.mark.parametrize("cls", [Daytona, AsyncDaytona])
def test_moved_client_members_name_the_v0_form(cls: type) -> None:
    client = cls(DaytonaConfig(api_key="k"))
    for name in (
        "create",
        "list",
        "get",
        "start",
        "stop",
        "delete",
        "snapshot",
        "secret",
        "warm_pool",
        "default_language",
    ):
        with pytest.raises(AttributeError) as caught:
            getattr(client, name)
        message = str(caught.value)
        assert f"{cls.__name__}.v0.{name}" in message, message
        assert caught.value.name == name


@pytest.mark.parametrize(
    ("installed", "tool"),
    [("2.0.0a8", "uvx daytona-upgrade@2.0.0a8."), ("unknown", "uvx daytona-upgrade.")],
)
def test_moved_member_guidance_names_the_matching_migration_tool(
    monkeypatch: pytest.MonkeyPatch, installed: str, tool: str
) -> None:
    import daytona._client as root_module

    monkeypatch.setattr(root_module, "_SDK_VERSION", installed)
    with pytest.raises(AttributeError) as caught:
        Daytona(DaytonaConfig(api_key="k")).create  # type: ignore[attr-defined]  # noqa: B018
    assert str(caught.value).endswith(f"To migrate 0.x code, run {tool}")


@pytest.mark.parametrize("cls", [Daytona, AsyncDaytona])
def test_assigning_a_moved_client_setting_on_the_root_names_the_v0_form(cls: type) -> None:
    client = cls(DaytonaConfig(api_key="k"))
    with pytest.raises(AttributeError) as caught:
        client.default_language = "typescript"
    assert f"{cls.__name__}.v0.default_language" in str(caught.value)


def test_an_attribute_error_inside_a_namespace_property_surfaces_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import daytona._client as root_module

    def broken(_config: object) -> object:
        raise AttributeError("inner bug while building the v2 client")

    calls: list[object] = []

    def counting(config: object) -> object:
        calls.append(config)
        return broken(config)

    monkeypatch.setattr(root_module, "_DaytonaV2", counting)
    with pytest.raises(AttributeError, match="inner bug while building the v2 client"):
        _ = Daytona(DaytonaConfig(api_key="k")).v2
    assert len(calls) == 1, "the failing getter ran more than once"


def test_other_missing_root_attributes_keep_the_plain_message() -> None:
    client = Daytona(DaytonaConfig(api_key="k"))
    with pytest.raises(AttributeError) as caught:
        client.images  # type: ignore[attr-defined]  # noqa: B018
    assert str(caught.value) == "'Daytona' object has no attribute 'images'"
    assert "SDK 2.x moved" not in str(caught.value)


@pytest.mark.skipif(
    not _UPGRADE_MAPPING.is_file(), reason="the upgrade tool's mapping is not in this checkout"
)
def test_moved_client_members_match_the_upgrade_tools_mapping() -> None:
    import json

    from daytona._client import _MOVED_TO_V0

    mapping = json.loads(_UPGRADE_MAPPING.read_text(encoding="utf-8"))
    assert set(mapping["movedClientMembers"]["py"]) == set(_MOVED_TO_V0)


@pytest.mark.parametrize(
    ("name", "in_v0", "in_v2"),
    [
        ("Sandbox", True, True),
        ("AsyncSandbox", True, True),
        ("Image", True, True),
        ("CreateSandboxFromImageParams", True, False),  # 0.x only
        ("BarChart", True, False),  # 0.x only
        ("ImageBuilder", False, True),  # 2.0-alpha only
        ("ProcessHandle", False, True),  # 2.0-alpha only
    ],
)
def test_old_root_imports_point_only_at_the_namespaces_that_have_the_name(
    name: str, in_v0: bool, in_v2: bool
) -> None:
    with pytest.raises(ImportError) as excinfo:
        getattr(daytona, name)  # what `from daytona import <name>` runs
    message = str(excinfo.value)
    assert (f"from daytona.v0 import {name}`" in message) is in_v0
    assert (f"from daytona.v2 import {name}`" in message) is in_v2
    assert ('pip install "daytona[v0-sdk]"' in message) is in_v0


def test_the_from_import_statement_surfaces_the_guidance() -> None:
    with pytest.raises(ImportError, match=r"from daytona\.v0 import Sandbox"):
        from daytona import Sandbox  # noqa: F401


def test_removed_root_names_raise_import_error_for_probes_too() -> None:
    # Pinned trade-off (see daytona.__getattr__): the from-import keeps the
    # guidance text, so a plain attribute probe raises instead of False.
    with pytest.raises(ImportError, match=r"from daytona\.v2 import Sandbox"):
        hasattr(daytona, "Sandbox")
    with pytest.raises(ImportError, match=r"from daytona\.v2 import Sandbox"):
        from daytona import Sandbox  # noqa: F401
    assert not issubclass(ImportError, AttributeError)


def test_private_lookups_stay_attribute_errors() -> None:
    assert not hasattr(daytona, "_no_such_thing")


@pytest.mark.parametrize("name", ["Sandbx", "NoSuchThing", "Synchronize", "v3"])
def test_unknown_root_names_are_plain_attribute_errors(name: str) -> None:
    # Only names a pre-2.0 root exported get the migration guidance; typos and
    # probes behave like any module attribute.
    assert not hasattr(daytona, name)
    assert getattr(daytona, name, "default") == "default"


def test_a_typo_in_a_from_import_gets_no_migration_advice() -> None:
    with pytest.raises(ImportError) as excinfo:
        from daytona import Sandbx  # type: ignore[attr-defined]  # noqa: F401
    assert "daytona.v0" not in str(excinfo.value)


def test_every_pre_2_0_root_name_gets_the_guidance() -> None:
    from daytona._v0_root_names import V0_ROOT_NAMES
    from daytona.v2._exports import ROOT_EXPORTS

    shared = set(daytona.__all__)
    for name in sorted((V0_ROOT_NAMES | set(ROOT_EXPORTS)) - shared):
        with pytest.raises(ImportError) as excinfo:
            getattr(daytona, name)
        message = str(excinfo.value)
        assert (f"from daytona.v0 import {name}`" in message) is (name in V0_ROOT_NAMES), name
        assert (f"from daytona.v2 import {name}`" in message) is (name in ROOT_EXPORTS), name


def test_from_daytona_import_v0_without_the_bundle_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "daytona.v0", raising=False)
    if "v0" in vars(daytona):  # hasattr() would run the __getattr__ under test
        monkeypatch.delitem(vars(daytona), "v0")
    monkeypatch.setattr(daytona, "__path__", [])  # no bundle, even in the bundle CI job
    with pytest.raises(ImportError) as excinfo:
        from daytona import v0  # noqa: F401
    assert str(excinfo.value) == _V0_EXTRA_HINT  # the same wording as d.v0's
    assert isinstance(excinfo.value.__cause__, ModuleNotFoundError)


def test_from_daytona_import_v0_raises_the_extra_hint_when_the_bundle_lacks_its_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wheel that carries the bundle but not the v0-sdk extra: the bundle's lazy
    __getattr__ raises the hint on first export access, and the root reads one
    export at import so `from daytona import v0` itself fails, not the next line."""
    fake = types.ModuleType("daytona.v0")

    def missing(_name: str) -> object:
        raise ImportError(_V0_EXTRA_HINT)

    fake.__getattr__ = missing  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "daytona.v0", fake)
    if "v0" in vars(daytona):
        monkeypatch.delitem(vars(daytona), "v0")
    with pytest.raises(ImportError, match="v0-sdk"):
        from daytona import v0
    # The failed probe rolled back the import's binding...
    assert "daytona.v0" not in sys.modules
    # ...so a retry raises again rather than
    # returning the half-bound module.
    monkeypatch.setitem(sys.modules, "daytona.v0", fake)
    with pytest.raises(ImportError, match="v0-sdk"):
        from daytona import v0  # noqa: F401
    assert "v0" not in vars(daytona)


def test_from_daytona_import_v0_imports_the_bundled_submodule(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("daytona.v0")
    monkeypatch.setitem(sys.modules, "daytona.v0", fake)
    if "v0" in vars(daytona):
        monkeypatch.delitem(vars(daytona), "v0")
    from daytona import v0

    assert v0 is fake


def test_v2_is_built_lazily_once_from_the_shared_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_V2_API_URL", raising=False)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    client = Daytona(DaytonaConfig(api_key="dtn_key", request_timeout=7.5, organization_id="org-for-v0-only"))
    assert client._v2 is None
    v2 = client.v2
    assert isinstance(v2, daytona_v2_sync.Daytona)
    assert client.v2 is v2
    impl = v2._impl
    assert impl._api_url == DEFAULT_V2_API_URL
    assert impl._api_key == "dtn_key"
    assert impl._request_timeout == 7.5
    client.close()
    client.close()  # idempotent
    assert client._v2 is None


def test_v2_api_url_is_the_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_V2_API_URL", "http://test-env.invalid/")
    with Daytona(DaytonaConfig(api_key="k")) as client:
        assert client.v2._impl._api_url == "http://test-env.invalid"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_or_whitespace_api_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("DAYTONA_V2_API_URL", blank)
    with Daytona(DaytonaConfig(api_key="k")) as client:
        assert client.v2._impl._api_url == DEFAULT_V2_API_URL


def test_v2_jwt_scope_is_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    client = Daytona(DaytonaConfig(jwt_token="jwt", project_id="proj"))
    assert client.v2._impl._api_key is None
    client.close()


@pytest.mark.anyio
async def test_async_root_builds_the_async_v2_client() -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="k")) as client:
        v2 = client.v2
        assert isinstance(v2, daytona_v2_async.AsyncDaytona)
        assert client.v2 is v2
    assert client._v2 is None


def test_config_target_is_deprecated_in_favour_of_the_per_call_forms() -> None:
    # The pinned 0.x SDK takes target per sandbox create and region_id per
    # snapshot create, so config `target` is deprecated in 2.x (only here:
    # SDK 0.x keeps it deprecation-free).
    with pytest.warns(DeprecationWarning) as record:
        DaytonaConfig(api_key="k", target="us")
    message = str(record[0].message)
    assert "applies only to the v0 namespace" in message
    assert "CreateSandboxFromSnapshotParams(target=...)" in message
    assert "region_id" in message
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        DaytonaConfig(api_key="k")


def test_v0_without_the_bundle_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    # `None` in sys.modules makes the import fail, exactly like a missing extra.
    monkeypatch.setitem(sys.modules, "daytona.v0", None)
    client = Daytona(DaytonaConfig(api_key="k"))
    with pytest.raises(ImportError, match=r'pip install "daytona\[v0-sdk\]"') as excinfo:
        _ = client.v0
    assert isinstance(excinfo.value.__cause__, ImportError)
    with pytest.raises(ImportError, match="v0"):
        _ = AsyncDaytona(DaytonaConfig(api_key="k")).v0


def _lazy_v0_missing_dependency(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """The real shape of a wheel installed without the extra: `daytona.v0`
    imports fine (its exports are lazy), then reading a client class fails on
    a dependency the extra would have installed."""
    module = types.ModuleType("daytona.v0")

    def lazy(name: str) -> object:
        raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)

    module.__getattr__ = lazy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "daytona.v0", module)


@pytest.mark.parametrize("client", [Daytona, AsyncDaytona])
def test_v0_without_the_extra_names_the_extra_when_the_lazy_export_fails(
    monkeypatch: pytest.MonkeyPatch, client: type[Daytona | AsyncDaytona]
) -> None:
    _lazy_v0_missing_dependency(monkeypatch, "opentelemetry")
    with pytest.raises(ImportError, match=r'pip install "daytona\[v0-sdk\]"') as excinfo:
        _ = client(DaytonaConfig(api_key="k")).v0
    assert "failed on 'opentelemetry'" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ModuleNotFoundError)


def test_v0_reports_a_non_missing_module_import_error_without_the_extra_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("daytona.v0")

    def lazy(name: str) -> object:
        raise ImportError("cannot import name 'Thing' from partially initialized module")

    module.__getattr__ = lazy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "daytona.v0", module)
    with pytest.raises(ImportError) as excinfo:
        _ = Daytona(DaytonaConfig(api_key="k")).v0
    message = str(excinfo.value)
    assert message.startswith("importing the v0 namespace failed: cannot import name 'Thing'")
    assert "pip install" not in message
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_v0_names_the_dependency_that_failed_to_import(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenFinder:
        def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
            if fullname == "daytona.v0":
                raise ModuleNotFoundError("No module named 'daytona_api_client'", name="daytona_api_client")

    monkeypatch.delitem(sys.modules, "daytona.v0", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BrokenFinder(), *sys.meta_path])
    with pytest.raises(ImportError, match=r"failed on 'daytona_api_client'") as excinfo:
        _ = Daytona(DaytonaConfig(api_key="k")).v0
    assert 'pip install "daytona[v0-sdk]"' in str(excinfo.value)


class _FakeV0Config:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeV0Client:
    def __init__(self, config: _FakeV0Config) -> None:
        self.config = config
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeAsyncV0Client(_FakeV0Client):
    async def close(self) -> None:  # type: ignore[override]
        self.closed += 1


def _fake_v0(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("daytona.v0")
    # The private names the bundle gives v0's client classes.
    module._DaytonaConfig = _FakeV0Config  # type: ignore[attr-defined]
    module._Daytona = _FakeV0Client  # type: ignore[attr-defined]
    module._AsyncDaytona = _FakeAsyncV0Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "daytona.v0", module)
    return module


def test_v0_gets_the_shared_credentials_and_its_own_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_v0(monkeypatch)
    monkeypatch.setenv("DAYTONA_V2_API_URL", "http://v2-test-env.invalid")
    with pytest.warns(DeprecationWarning):
        config = DaytonaConfig(
            api_key="dtn_key", organization_id="org", target="us", project_id="v2-only", otel_enabled=True
        )
    client = Daytona(config)
    v0 = client.v0
    assert isinstance(v0, _FakeV0Client)
    assert client.v0 is v0
    assert v0.config.kwargs == {
        "api_key": "dtn_key",
        "jwt_token": None,
        "organization_id": "org",
        # DAYTONA_V2_API_URL addresses v2 test environments; v0 never sees it.
        "api_url": V0_API_URL,
        "target": "us",
        "otel_enabled": True,
    }
    client.close()
    assert v0.closed == 1
    assert client._v0 is None


@pytest.mark.anyio
async def test_async_v0_is_the_async_client_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_v0(monkeypatch)
    client = AsyncDaytona(DaytonaConfig(api_key="k", connection_pool_maxsize=8))
    v0 = client.v0
    assert isinstance(v0, _FakeAsyncV0Client)
    assert v0.config.kwargs["connection_pool_maxsize"] == 8
    assert "otel_enabled" not in v0.config.kwargs  # unset: v0 keeps its own default
    await client.close()
    assert v0.closed == 1


def test_v2_suffixed_aliases_name_the_same_classes() -> None:
    from daytona.v2 import AsyncSandboxV2, DaytonaErrorV2, SandboxV2

    assert SandboxV2 is daytona_v2.Sandbox
    assert AsyncSandboxV2 is daytona_v2.AsyncSandbox
    assert DaytonaErrorV2 is daytona_v2.DaytonaError


def test_a_v2_only_program_never_imports_v0() -> None:
    """A real v2 call in a fresh interpreter, then `daytona.v0` must be absent."""
    script = textwrap.dedent(
        """
        import sys

        import httpx
        import respx

        from daytona import Daytona, DaytonaConfig

        with respx.mock(assert_all_called=True) as router:
            router.get("http://api.invalid/sandboxes").mock(
                return_value=httpx.Response(200, json={"sandboxes": [], "next_cursor": None})
            )
            with Daytona(DaytonaConfig(api_key="k")) as client:
                page = client.v2.list()
        assert list(page.items) == [], page
        loaded = sorted(name for name in sys.modules if name == "daytona.v0" or name.startswith("daytona.v0."))
        assert not loaded, loaded
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"DAYTONA_V2_API_URL": "http://api.invalid", "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


def _resolve_v0_tag(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        timeout=30,
        check=False,
    )


def test_the_v0_namespace_is_the_pinned_0x_tag() -> None:
    """scripts/v0-sdk-version is the single source of the vendored v0 tag."""
    pin = (_repo_scripts() / "v0-sdk-version").read_text().splitlines()[0].strip()
    assert pin.startswith("v0."), "the v0 namespace vendors the 0.x SDK at a pinned tag"
    result = _resolve_v0_tag({})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == pin


def test_the_pin_records_the_tag_commit() -> None:
    lines = (_repo_scripts() / "v0-sdk-version").read_text().splitlines()
    assert len(lines) == 2
    assert re.fullmatch(r"[0-9a-f]{40}", lines[1]), lines
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh"), "--sha"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        check=False,
    )
    assert result.stdout.strip() == lines[1]


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        ("v0.224.0\n" + "a" * 40 + "\n", True),
        ("v0.224.0\n" + "a" * 40, True),
        ("v0.224.0\n" + "a" * 40 + "\n\n", True),
        ("v0.224.0\n" + "a" * 40 + "\nv0.225.0\n", False),
        ("v0.224.0\n" + "a" * 40 + "\n# note\n", False),
    ],
)
def test_the_pin_file_holds_exactly_two_non_blank_lines(tmp_path: Path, body: str, ok: bool) -> None:
    pin = tmp_path / "pin"
    pin.write_text(body)
    result = _resolve_v0_tag({"DAYTONA_V0_PIN_FILE": str(pin)})
    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert "exactly two lines" in result.stderr


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        # Whitespace is trimmed at the ends (CRLF, padding), never inside a line.
        (" v0.224.0 \r\n" + "a" * 40 + "\r\n", True),
        ("v0.224 .0\n" + "a" * 40 + "\n", False),
        ("v0.224.0\n" + "a" * 20 + " " + "a" * 20 + "\n", False),
    ],
)
def test_pin_whitespace_is_trimmed_only_at_the_ends(tmp_path: Path, body: str, ok: bool) -> None:
    pin = tmp_path / "pin"
    pin.write_text(body)
    result = _resolve_v0_tag({"DAYTONA_V0_PIN_FILE": str(pin)})
    assert (result.returncode == 0) is ok, result.stderr


def test_a_pin_without_its_commit_is_rejected(tmp_path: Path) -> None:
    pin = tmp_path / "pin"
    pin.write_text("v0.224.0\n")
    result = _resolve_v0_tag({"DAYTONA_V0_PIN_FILE": str(pin)})
    assert result.returncode != 0
    assert "40-character commit sha" in result.stderr


def test_the_v0_tag_override_is_shape_checked() -> None:
    assert _resolve_v0_tag({"DAYTONA_V0_TAG": "v0.300.0"}).stdout.strip() == "v0.300.0"
    for bad in ("main", "v1.2", "v0.1.0-rc.1", "sdk-go/v0.1.0"):
        result = _resolve_v0_tag({"DAYTONA_V0_TAG": bad})
        assert result.returncode != 0, bad
        assert "is not a vX.Y.Z release tag" in result.stderr


def test_root_config_routing_covers_every_field_and_matches_the_v0_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    from daytona._client import ROOT_CONFIG_ROUTING

    fields = {f.name for f in dataclasses.fields(DaytonaConfig)}
    assert set(ROOT_CONFIG_ROUTING) == fields
    _fake_v0(monkeypatch)
    values: dict[str, object] = {
        "api_key": "k",
        "organization_id": "org",
        "target": "eu",
        "project_id": "p",
        "request_timeout": 5.0,
        "connection_pool_maxsize": 4,
        "otel_enabled": True,
    }
    with pytest.warns(DeprecationWarning):
        config = DaytonaConfig(**values)  # type: ignore[arg-type]
    forwarded = set(Daytona(config).v0.config.kwargs) - {"api_url", "jwt_token"}
    expected = {name for name in values if ROOT_CONFIG_ROUTING[name] != "v2"}
    assert forwarded == expected


def _publish_mode(tree: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_repo_scripts() / "v0-publish-mode.sh"), str(tree)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
        timeout=30,
        check=False,
    )


def _tree(tmp_path: Path, *files: str) -> Path:
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return tmp_path


def test_publish_skips_v0_only_for_a_tree_from_before_the_namespaces(tmp_path: Path) -> None:
    result = _publish_mode(_tree(tmp_path, "sdk-python/src/daytona/__init__.py"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"
    assert "::notice::" in result.stderr


def test_publish_bundles_v0_when_the_namespace_code_and_the_pin_exist(tmp_path: Path) -> None:
    result = _publish_mode(
        _tree(
            tmp_path,
            "sdk-typescript/src/v0-unbundled.ts",
            "sdk-python/src/daytona/_client.py",
            "scripts/v0-sdk-version",
        )
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bundle"


@pytest.mark.parametrize("code", ["sdk-typescript/src/v0-unbundled.ts", "sdk-python/src/daytona/_client.py"])
def test_publish_refuses_the_namespace_code_in_only_one_sdk(tmp_path: Path, code: str) -> None:
    result = _publish_mode(_tree(tmp_path, code, "scripts/v0-sdk-version"))
    assert result.returncode != 0
    assert "only one SDK" in result.stderr


def test_publish_refuses_namespace_code_without_a_pin(tmp_path: Path) -> None:
    result = _publish_mode(
        _tree(tmp_path, "sdk-python/src/daytona/_client.py", "sdk-typescript/src/v0-unbundled.ts")
    )
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert "no scripts/v0-sdk-version pin" in result.stderr
    # A testing override counts as a pin.
    assert _publish_mode(tmp_path, {"DAYTONA_V0_TAG": "v0.224.0"}).stdout.strip() == "bundle"


def test_publish_refuses_a_pin_without_the_namespace_code(tmp_path: Path) -> None:
    result = _publish_mode(_tree(tmp_path, "scripts/v0-sdk-version"))
    assert result.returncode != 0
    assert "no v0 namespace code" in result.stderr


def test_this_tree_bundles_v0() -> None:
    assert _publish_mode(_repo_scripts().parent).stdout.strip() == "bundle"


def test_an_override_equal_to_the_pinned_tag_keeps_the_commit_check(tmp_path: Path) -> None:
    """The moved-tag reproduction: overriding with the pinned tag must still
    verify the pinned commit (resolve-v0-tag.sh --sha keeps it)."""
    pin = tmp_path / "pin"
    pin.write_text("v0.224.0\n" + "a" * 40 + "\n")
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh"), "--sha"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "DAYTONA_V0_PIN_FILE": str(pin), "DAYTONA_V0_TAG": "v0.224.0"},
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "a" * 40
    other = subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh"), "--sha"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "DAYTONA_V0_PIN_FILE": str(pin), "DAYTONA_V0_TAG": "v0.225.0"},
        timeout=30,
        check=False,
    )
    assert other.stdout.strip() == ""


def test_an_override_equal_to_the_pinned_tag_refuses_a_moved_tag(tmp_path: Path) -> None:
    """End to end against a local repository whose tag was force-moved."""
    repo = tmp_path / "clients.git"
    work = tmp_path / "work"
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run([*git, "init", "-q", str(work)], check=True, env=env)
    (work / "f").write_text("one")
    subprocess.run([*git, "-C", str(work), "add", "f"], check=True, env=env)
    subprocess.run([*git, "-C", str(work), "commit", "-qm", "one"], check=True, env=env)
    pinned = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (work / "f").write_text("two")
    subprocess.run([*git, "-C", str(work), "commit", "-qam", "two"], check=True, env=env)
    subprocess.run(
        [*git, "-C", str(work), "tag", "v0.224.0"], check=True, env=env
    )  # the tag sits on the moved commit
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(repo)], check=True, env=env)
    pin = tmp_path / "pin"
    pin.write_text(f"v0.224.0\n{pinned}\n")
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh"), "--verify"],
        capture_output=True,
        text=True,
        env={
            **env,
            "DAYTONA_V0_PIN_FILE": str(pin),
            "DAYTONA_V0_TAG": "v0.224.0",
            "DAYTONA_V0_REPO": str(repo),
        },
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "was the tag moved?" in result.stderr
    # A DAYTONA_V0_SHA matching the moved tag must not replace the pinned commit.
    moved = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    spoofed = subprocess.run(
        ["bash", str(_repo_scripts() / "resolve-v0-tag.sh"), "--verify"],
        capture_output=True,
        text=True,
        env={
            **env,
            "DAYTONA_V0_PIN_FILE": str(pin),
            "DAYTONA_V0_TAG": "v0.224.0",
            "DAYTONA_V0_SHA": moved,
            "DAYTONA_V0_REPO": str(repo),
        },
        timeout=60,
        check=False,
    )
    assert spoofed.returncode != 0
    assert "conflicts with the commit pinned" in spoofed.stderr


@pytest.mark.parametrize(
    ("env", "ok"),
    [
        ({}, True),
        ({"DAYTONA_V0_TAG": "v0.225.0", "PUBLISH_PYPI": "false", "PUBLISH_NPM": "false"}, True),
        ({"DAYTONA_V0_TAG": "v0.225.0", "PUBLISH_PYPI": "true", "PUBLISH_NPM": "false"}, False),
        # The pinned tag itself is not an override (its commit is still checked).
        ({"DAYTONA_V0_TAG": "v0.230.0", "PUBLISH_PYPI": "false", "PUBLISH_NPM": "true"}, True),
        ({"DAYTONA_V0_TAG": "v0.223.1", "PUBLISH_PYPI": "false", "PUBLISH_NPM": "true"}, False),
        ({"DAYTONA_V0_TAG": "v0.225.0", "PUBLISH_NPM": "true", "ALLOW_UNPINNED_V0": "true"}, True),
    ],
)
def test_publishing_with_a_v0_tag_override_needs_allow_unpinned(env: dict[str, str], ok: bool) -> None:
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "v0-publish-mode.sh"), "--check-override"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        timeout=30,
        check=False,
    )
    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert "allow_unpinned_v0" in result.stderr


@pytest.mark.parametrize(
    ("tag", "ok"),
    [
        # The source's own pinned tag is not an override...
        ("v0.223.1", True),
        # ...while the pin beside the script (main's) is one for this source.
        ("v0.230.0", False),
    ],
)
def test_check_override_compares_against_the_pin_file_being_published(
    tmp_path: Path, tag: str, ok: bool
) -> None:
    pin = tmp_path / "pin"
    pin.write_text("v0.223.1\r\n" + "c" * 40 + "\n")
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "v0-publish-mode.sh"), "--check-override"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "DAYTONA_V0_PIN_FILE": str(pin),
            "DAYTONA_V0_TAG": tag,
            "PUBLISH_PYPI": "true",
        },
        timeout=30,
        check=False,
    )
    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert "allow_unpinned_v0" in result.stderr


@pytest.mark.parametrize("bundled", [False, True])
def test_check_no_bundled_v0_catches_a_large_bundled_lockfile(tmp_path: Path, bundled: bool) -> None:
    """A bundled package-lock.json is larger than a pipe buffer; the check must
    still fail on it (git show | grep -q under pipefail used to pass it)."""
    clients = tmp_path
    (clients / "scripts").mkdir(parents=True)
    (clients / "sdk-python").mkdir()
    (clients / "sdk-typescript").mkdir()
    script = clients / "scripts" / "check-no-bundled-v0.sh"
    script.write_text((_repo_scripts() / "check-no-bundled-v0.sh").read_text())
    (clients / "sdk-python" / "pyproject.toml").write_text("[project.optional-dependencies]\nv0-sdk = []\n")
    (clients / "sdk-typescript" / "package.json").write_text('{"name": "@daytona/sdk"}\n')
    filler = "".join(f'    "node_modules/pkg{i}": {{"version": "1.0.0"}},\n' for i in range(8000))
    client = '    "node_modules/@daytona/api-client": {"version": "0.224.0"},\n' if bundled else ""
    (clients / "sdk-typescript" / "package-lock.json").write_text(
        '{"packages": {\n' + client + filler + "}}\n"
    )
    assert (clients / "sdk-typescript" / "package-lock.json").stat().st_size > 256 * 1024
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert (result.returncode != 0) is bundled, result.stdout + result.stderr
    if bundled:
        assert "package-lock.json carries v0's API clients" in result.stdout


@pytest.mark.parametrize("name", ["api_url", "toolbox_url"])
def test_root_config_rejects_url_fields_saying_to_stay_on_0x(name: str) -> None:
    with pytest.raises(TypeError) as excinfo:
        DaytonaConfig(**{name: "https://daytona.corp/api"})  # type: ignore[arg-type]
    assert str(excinfo.value) == (
        f"DaytonaConfig does not take '{name}': "
        "SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x."
    )
    # Other unknown keywords keep Python's own error.
    with pytest.raises(TypeError, match="unexpected keyword argument 'nope'"):
        DaytonaConfig(nope=1)  # type: ignore[call-arg]


def test_the_python_bundler_refuses_symlinks(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    source = tmp_path / "sdk-python" / "src" / "daytona"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (source / "leaked.py").symlink_to(outside)
    with pytest.raises(SystemExit, match="contains symlinks.*leaked.py"):
        bundle_v0_python.bundle(tmp_path / "sdk-python", "0.224.0", "v0.224.0")


def test_a_multi_line_v0_tag_is_refused() -> None:
    result = _resolve_v0_tag({"DAYTONA_V0_TAG": "v0.224.0\nx=y"})
    assert result.returncode != 0
    assert "single line" in result.stderr


def test_the_target_deprecation_points_at_the_caller() -> None:
    """Attributed to user code, so Python's default filters show it in scripts."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        DaytonaConfig(target="eu")
    assert [w.filename for w in caught if issubclass(w.category, DeprecationWarning)] == [__file__]


def test_dir_lists_v0_only_when_the_bundle_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    # An earlier test (test_v0_bundle.py) may have imported daytona.v0, which
    # sets it as a package attribute; drop it so the "no bundle" half is real.
    monkeypatch.delitem(sys.modules, "daytona.v0", raising=False)
    if "v0" in vars(daytona):  # not hasattr: the root __getattr__ would try to import it
        monkeypatch.delattr(daytona, "v0")
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "daytona.v0" else real(name, *a)
    )
    assert "v0" not in dir(daytona)
    assert "v2" in dir(daytona)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: object() if name == "daytona.v0" else real(name, *a)
    )
    assert "v0" in dir(daytona)


def test_the_python_bundler_refuses_an_unaliased_dotted_import(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    (tmp_path / "ok.py").write_text("import daytona.toolbox as tb\nfrom daytona.common import x\n")
    assert bundle_v0_python._rewrite_imports(tmp_path) == 2
    (tmp_path / "bad.py").write_text("import daytona.toolbox\n")
    with pytest.raises(SystemExit, match="unaliased"):
        bundle_v0_python._rewrite_imports(tmp_path)


def test_check_override_refuses_a_multi_line_tag() -> None:
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "v0-publish-mode.sh"), "--check-override"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "DAYTONA_V0_TAG": "v9.9.9\n::notice::forged", "PUBLISH_PYPI": "true"},
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "single line" in result.stderr
    assert "::notice::forged" not in result.stdout + result.stderr


def test_v2_all_matches_root_exports() -> None:
    from daytona.v2._exports import ROOT_EXPORTS

    assert daytona_v2.__all__ == ROOT_EXPORTS


@pytest.mark.parametrize("bundle_dir", ["sdk-python/src/daytona/v0", "sdk-typescript/src/v0"])
def test_check_no_bundled_v0_refuses_committed_bundles(tmp_path: Path, bundle_dir: str) -> None:
    """Reject committed publish-time namespace bundles."""
    clients = tmp_path
    (clients / "scripts").mkdir(parents=True)
    (clients / bundle_dir).mkdir(parents=True)
    (clients / "sdk-python").mkdir(exist_ok=True)
    (clients / "sdk-typescript").mkdir(exist_ok=True)
    script = clients / "scripts" / "check-no-bundled-v0.sh"
    script.write_text((_repo_scripts() / "check-no-bundled-v0.sh").read_text())
    (clients / "sdk-python" / "pyproject.toml").write_text("v0-sdk = []\n")
    (clients / bundle_dir / "index.py").write_text("")
    (clients / "sdk-typescript" / "package.json").write_text("{}\n")
    (clients / "sdk-typescript" / "package-lock.json").write_text("{}\n")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert result.returncode != 0
    assert f"{bundle_dir}/index.py" in result.stdout


def test_check_no_bundled_v0_is_case_insensitive(tmp_path: Path) -> None:
    clients = tmp_path
    (clients / "scripts").mkdir(parents=True)
    (clients / "sdk-python" / "src" / "daytona" / "V0").mkdir(parents=True)
    (clients / "sdk-typescript").mkdir()
    script = clients / "scripts" / "check-no-bundled-v0.sh"
    script.write_text((_repo_scripts() / "check-no-bundled-v0.sh").read_text())
    (clients / "sdk-python" / "pyproject.toml").write_text("v0-sdk = []\n")
    (clients / "sdk-python" / "src" / "daytona" / "V0" / "x.py").write_text("")
    (clients / "sdk-typescript" / "package.json").write_text("{}\n")
    (clients / "sdk-typescript" / "package-lock.json").write_text("{}\n")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert result.returncode != 0
    assert "daytona/V0/x.py" in result.stdout


def test_the_python_bundler_refuses_an_unrewritten_sdk_version_lookup(tmp_path: Path) -> None:
    sys.path.insert(0, str(_repo_scripts()))
    try:
        import bundle_v0_python
    finally:
        sys.path.pop(0)
    (tmp_path / "_bundle.py").write_text("")
    (tmp_path / "a.py").write_text(bundle_v0_python._METADATA_IMPORT)
    (tmp_path / "b.py").write_text("import importlib.metadata as metadata\nV = metadata.version('daytona')\n")
    with pytest.raises(SystemExit, match="left unrewritten in.*b.py"):
        bundle_v0_python._pin_sdk_version(tmp_path, "0.224.0")


def test_check_no_bundled_v0_catches_an_entry_at_the_exact_path(tmp_path: Path) -> None:
    clients = tmp_path
    (clients / "scripts").mkdir(parents=True)
    (clients / "sdk-python" / "src" / "daytona").mkdir(parents=True)
    (clients / "sdk-typescript").mkdir()
    script = clients / "scripts" / "check-no-bundled-v0.sh"
    script.write_text((_repo_scripts() / "check-no-bundled-v0.sh").read_text())
    (clients / "sdk-python" / "pyproject.toml").write_text("v0-sdk = []\n")
    (clients / "sdk-python" / "src" / "daytona" / "v0").symlink_to("/nonexistent")
    (clients / "sdk-typescript" / "package.json").write_text("{}\n")
    (clients / "sdk-typescript" / "package-lock.json").write_text("{}\n")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert result.returncode != 0
    assert "sdk-python/src/daytona/v0" in result.stdout


@pytest.mark.parametrize(
    ("manifest", "bad"),
    [
        ('{"dependencies": {"axios": "^1.7.0", "ws": "^8"}}', False),
        # A bundled manifest with the three API clients removed by hand.
        ('{"dependencies": {"axios": "^1.20.0", "socket.io-client": "^4.8.4"}}', True),
    ],
)
def test_check_no_bundled_v0_catches_v0_only_dependencies(tmp_path: Path, manifest: str, bad: bool) -> None:
    clients = tmp_path
    (clients / "scripts").mkdir(parents=True)
    (clients / "sdk-python").mkdir()
    (clients / "sdk-typescript").mkdir()
    script = clients / "scripts" / "check-no-bundled-v0.sh"
    script.write_text((_repo_scripts() / "check-no-bundled-v0.sh").read_text())
    (clients / "sdk-python" / "pyproject.toml").write_text("v0-sdk = []\n")
    (clients / "sdk-typescript" / "package.json").write_text(manifest + "\n")
    (clients / "sdk-typescript" / "package-lock.json").write_text(
        '{"packages": {"": {"dependencies": {"axios": "^1"}}}}\n'
    )
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert (result.returncode != 0) is bad, result.stdout + result.stderr
    if bad:
        assert "declares v0-only dependencies (socket.io-client)" in result.stdout


@pytest.mark.parametrize("bundled", [False, True])
def test_check_dist_artifacts_reads_member_lists(tmp_path: Path, bundled: bool) -> None:
    """The publish artifact check matches exact member paths (no piped grep)."""
    import io
    import tarfile
    import zipfile

    wheel_members = ["daytona/py.typed"] + (["daytona/v0/_bundle.py"] if bundled else [])
    with zipfile.ZipFile(tmp_path / "daytona-2.0.0-py3-none-any.whl", "w") as wheel:
        for name in wheel_members:
            wheel.writestr(name, "")
    with tarfile.open(tmp_path / "daytona-2.0.0.tar.gz", "w:gz") as sdist:
        for name in ["src/daytona/py.typed"] + (["src/daytona/v0/_bundle.py"] if bundled else []):
            info = tarfile.TarInfo(f"daytona-2.0.0/{name}")
            sdist.addfile(info, io.BytesIO(b""))
    script = str(_repo_scripts() / "check_dist_artifacts.py")
    plain = subprocess.run(
        [sys.executable, script, "python", str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert plain.returncode == 0, plain.stdout
    with_v0 = subprocess.run(
        [sys.executable, script, "python", str(tmp_path), "--v0"], capture_output=True, text=True, check=False
    )
    assert (with_v0.returncode == 0) is bundled, with_v0.stdout


def test_close_closes_v0_even_when_v2_close_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_v0(monkeypatch)
    client = Daytona(DaytonaConfig(api_key="k"))
    v0 = client.v0
    v2 = client.v2

    def boom() -> None:
        raise RuntimeError("v2 close failed")

    monkeypatch.setattr(v2, "close", boom)
    with pytest.raises(RuntimeError, match="v2 close failed"):
        client.close()
    assert v0.closed == 1


class _Release:
    def __init__(self, log: list[str], name: str, fail: bool = False) -> None:
        self._log, self._name, self._fail = log, name, fail

    def shutdown(self) -> None:
        self._log.append(self._name)
        if self._fail:
            raise RuntimeError(f"{self._name} failed")

    disconnect = shutdown


def test_close_releases_a_sync_v0_client_without_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bundled 0.x sync Daytona has no close() but holds a socket.io
    connection, a subscription worker and (with otel) a tracer provider: the
    root releases all three, in the order v0's async close() does, even when
    one step raises."""
    log: list[str] = []

    class SyncV0WithoutClose:
        def __init__(self, config: _FakeV0Config) -> None:
            self._tracer_provider = _Release(log, "tracer", fail=True)
            self._subscription_manager = _Release(log, "subscriptions")
            self._event_dispatcher = _Release(log, "events")

    module = _fake_v0(monkeypatch)
    module._Daytona = SyncV0WithoutClose  # type: ignore[attr-defined]
    client = Daytona(DaytonaConfig(api_key="k"))
    assert isinstance(client.v0, SyncV0WithoutClose)
    with pytest.raises(RuntimeError, match="tracer failed"):
        client.close()
    assert log == ["tracer", "subscriptions", "events"]
    client.close()  # idempotent: the dropped client is not released twice
    assert log == ["tracer", "subscriptions", "events"]


@pytest.mark.anyio
async def test_async_close_closes_v0_even_when_v2_close_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_v0(monkeypatch)
    client = AsyncDaytona(DaytonaConfig(api_key="k"))
    v0 = client.v0
    v2 = client.v2

    async def boom() -> None:
        raise RuntimeError("v2 close failed")

    monkeypatch.setattr(v2, "close", boom)
    with pytest.raises(RuntimeError, match="v2 close failed"):
        await client.close()
    assert v0.closed == 1


@pytest.mark.anyio
async def test_async_close_propagates_a_cancellation_from_v0_after_a_v2_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    _fake_v0(monkeypatch)
    client = AsyncDaytona(DaytonaConfig(api_key="k"))
    v0 = client.v0
    v2 = client.v2

    async def boom() -> None:
        raise RuntimeError("v2 close failed")

    async def cancelled() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(v2, "close", boom)
    monkeypatch.setattr(v0, "close", cancelled)
    with pytest.raises(asyncio.CancelledError) as excinfo:
        await client.close()
    assert isinstance(excinfo.value.__context__, RuntimeError)


def _v2_list_host(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> str:
    """The host a real v2 list request is sent to under `env`."""
    import httpx
    import respx

    for name in ("DAYTONA_V2_API_URL", "DAYTONA_API_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/sandboxes.*").mock(
            return_value=httpx.Response(
                200, json={"sandboxes": [], "next_cursor": None, "total": 0, "total_relation": "eq"}
            )
        )
        with Daytona(DaytonaConfig(api_key="k")) as client:
            client.v2.list()
        assert route.called
        return str(route.calls.last.request.url.host)


@pytest.mark.parametrize(
    ("env", "host"),
    [
        (
            {"DAYTONA_V2_API_URL": "http://v2.invalid", "DAYTONA_API_URL": "http://v0-sdk.invalid"},
            "v2.invalid",
        ),
        # DAYTONA_API_URL belongs to the 0.x SDK: 2.x never falls back to it.
        ({"DAYTONA_V2_API_URL": "   ", "DAYTONA_API_URL": "http://v0-sdk.invalid"}, "api.daytona.com"),
        ({"DAYTONA_API_URL": "http://v0-sdk.invalid"}, "api.daytona.com"),
        ({"DAYTONA_V2_API_URL": " \t"}, "api.daytona.com"),
        ({"DAYTONA_V2_API_URL": " http://v2.invalid// "}, "v2.invalid"),
        ({"DAYTONA_V2_API_URL": "//", "DAYTONA_API_URL": "http://v0-sdk.invalid"}, "api.daytona.com"),
    ],
)
def test_v2_api_url_resolution_order(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], host: str) -> None:
    assert _v2_list_host(monkeypatch, env) == host


@pytest.mark.parametrize(
    ("env", "url"),
    [
        (
            {"DAYTONA_V0_API_URL": "http://v0.invalid/api", "DAYTONA_API_URL": "http://v2.invalid"},
            "http://v0.invalid/api",
        ),
        ({"DAYTONA_V0_API_URL": "  ", "DAYTONA_API_URL": "http://v2.invalid"}, V0_API_URL),
        ({"DAYTONA_API_URL": "http://v2.invalid"}, V0_API_URL),
        ({"DAYTONA_V0_API_URL": " http://v0.invalid/api// "}, "http://v0.invalid/api"),
        ({"DAYTONA_V0_API_URL": "/"}, V0_API_URL),
    ],
)
def test_v0_api_url_honours_its_own_var_and_ignores_daytona_api_url(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], url: str
) -> None:
    _fake_v0(monkeypatch)
    monkeypatch.delenv("DAYTONA_V0_API_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert Daytona(DaytonaConfig(api_key="k")).v0.config.kwargs["api_url"] == url


def test_the_publish_mode_markers_exist_in_this_tree() -> None:
    result = subprocess.run(
        ["bash", str(_repo_scripts() / "v0-publish-mode.sh"), "--check-markers"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("config_class", ["root", "v2"])
def test_config_type_hints_resolve_at_runtime(config_class: str) -> None:
    import typing

    cls = daytona.DaytonaConfig if config_class == "root" else daytona_v2_types.DaytonaConfig
    hints = typing.get_type_hints(cls)
    assert "api_key" in hints


def test_dir_lists_the_namespaces() -> None:
    # v0 is listed only when the bundle exists (test_dir_lists_v0_only_when_the_bundle_exists).
    names = dir(daytona)
    assert "v2" in names and "Daytona" in names


@pytest.mark.parametrize("namespace", ["v0", "v2"])
@pytest.mark.parametrize(
    ("name", "root_form"),
    [
        ("Daytona", "`from daytona import Daytona`, then `Daytona().{ns}`"),
        ("AsyncDaytona", "`from daytona import AsyncDaytona`, then `AsyncDaytona().{ns}`"),
        (
            "DaytonaConfig",
            "`from daytona import Daytona, DaytonaConfig`, then `Daytona(DaytonaConfig(...)).{ns}`",
        ),
        ("DaytonaV2", "`from daytona import Daytona`, then `Daytona().{ns}`"),
        (
            "DaytonaConfigV0",
            "`from daytona import Daytona, DaytonaConfig`, then `Daytona(DaytonaConfig(...)).{ns}`",
        ),
    ],
)
def test_the_hidden_client_message_names_the_root_form(namespace: str, name: str, root_form: str) -> None:
    from daytona._client import _client_not_exported

    message = _client_not_exported(namespace, name)
    assert message.startswith(f"`daytona.{namespace}.{name}` is not exported: ")
    assert f"Use {root_form.format(ns=namespace)}." in message
    assert message.endswith("For a custom API URL, stay on SDK 0.x.") is (namespace == "v0")


@pytest.mark.parametrize(
    "name", ["Daytona", "AsyncDaytona", "DaytonaConfig", "DaytonaV2", "AsyncDaytonaV2", "DaytonaConfigV2"]
)
def test_the_v2_client_classes_are_not_exported(name: str) -> None:
    """SDK 2.x builds a v2 client only through the root: the old import
    raises an ImportError naming the root form, in a fresh interpreter."""
    script = f"try:\n    from daytona.v2 import {name}\nexcept ImportError as error:\n    print(error)\n"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    assert out.startswith(f"`daytona.v2.{name}` is not exported"), out
    assert out.rstrip().endswith(".v2`."), out
    with pytest.raises(ImportError, match="is not exported"):
        getattr(daytona_v2, name)
    assert name not in daytona_v2.__all__
    assert name not in dir(daytona_v2)


def test_other_unknown_v2_names_keep_attribute_error() -> None:
    assert not hasattr(daytona_v2, "NoSuchThing")
    assert hasattr(daytona_v2, "Sandbox")


def test_neither_namespace_shares_the_client_classes() -> None:
    from daytona.v2._exports import ROOT_EXPORTS, SHARED_WITH_V0

    client_names = {"AsyncDaytona", "Daytona", "DaytonaConfig"}
    assert not client_names & set(SHARED_WITH_V0)
    assert not client_names & set(ROOT_EXPORTS)
    assert not {f"{name}V2" for name in client_names} & set(ROOT_EXPORTS)


def test_the_root_exports_the_v2_client_types_for_annotations() -> None:
    from daytona import AsyncDaytonaV2Client, DaytonaV2Client

    root = Daytona(DaytonaConfig(api_key="k"))
    assert isinstance(root.v2, DaytonaV2Client)
    assert not isinstance(root.v2, AsyncDaytonaV2Client)
    assert isinstance(AsyncDaytona(DaytonaConfig(api_key="k")).v2, AsyncDaytonaV2Client)
    assert issubclass(daytona_v2_sync.Daytona, DaytonaV2Client)


@pytest.mark.parametrize(
    ("name", "root_form"),
    [
        ("DaytonaV2Client", "`from daytona import Daytona`, then `Daytona().v2`"),
        ("AsyncDaytonaV2Client", "`from daytona import AsyncDaytona`, then `AsyncDaytona().v2`"),
    ],
)
def test_the_client_types_cannot_build_a_client(name: str, root_form: str) -> None:
    """Annotation-only: a second way to build a client is what 2.0 removed."""
    cls = getattr(daytona, name)
    with pytest.raises(TypeError) as excinfo:
        cls(DaytonaConfig(api_key="k"))
    assert str(excinfo.value) == (
        f"`daytona.{name}` is a type for annotations, not a constructor: "
        f"the client is built only through the root client. Use {root_form}."
    )


@pytest.mark.parametrize(
    ("name", "private"), [("DaytonaV0Client", "_Daytona"), ("AsyncDaytonaV0Client", "_AsyncDaytona")]
)
def test_the_v0_client_types_resolve_lazily_from_the_bundle(
    monkeypatch: pytest.MonkeyPatch, name: str, private: str
) -> None:
    module = types.ModuleType("daytona.v0")
    marker = type(private, (), {})
    setattr(module, private, marker)
    module._DaytonaConfig = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "daytona.v0", module)
    cls = getattr(daytona, name)
    assert isinstance(marker(), cls)
    assert not isinstance(object(), cls)
    with pytest.raises(TypeError, match=r"not a constructor.*\.v0`"):
        cls()
    assert name not in daytona.__all__


def test_the_v0_client_types_name_the_extra_without_the_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "daytona.v0", None)
    with pytest.raises(ImportError, match="v0-sdk"):
        from daytona import DaytonaV0Client  # noqa: F401


def test_the_client_types_are_subclasses_of_themselves() -> None:
    from daytona import AsyncDaytonaV2Client, DaytonaV2Client

    assert issubclass(DaytonaV2Client, DaytonaV2Client)
    assert issubclass(AsyncDaytonaV2Client, AsyncDaytonaV2Client)
    assert not issubclass(DaytonaV2Client, AsyncDaytonaV2Client)


def test_the_client_types_cannot_be_subclassed() -> None:
    from daytona import DaytonaV2Client

    message = (
        "`daytona.DaytonaV2Client` is a type for annotations and cannot be subclassed: the client is "
        "built only through the root client. Use `from daytona import Daytona`, then `Daytona().v2`."
    )
    with pytest.raises(TypeError) as excinfo:

        class Mine(DaytonaV2Client):  # type: ignore[misc]
            pass

    assert str(excinfo.value) == message

    class Mixin:
        pass

    with pytest.raises(TypeError) as excinfo:

        class MixedIn(Mixin, DaytonaV2Client):  # type: ignore[misc]
            pass

    assert str(excinfo.value) == message


@pytest.mark.parametrize("name", ["api_url", "server_url", "toolbox_url"])
def test_setting_a_url_field_on_a_config_after_construction_is_refused(name: str) -> None:
    config = DaytonaConfig(api_key="k")
    with pytest.raises(TypeError, match="stay on SDK 0.x"):
        setattr(config, name, "https://self.example/api")


@pytest.mark.parametrize("cls", [Daytona, AsyncDaytona])
def test_a_url_field_smuggled_into_the_config_dict_stops_the_client_build(cls: type) -> None:
    config = DaytonaConfig(api_key="k")
    vars(config)["api_url"] = "https://self.example/api"
    with pytest.raises(TypeError, match="stay on SDK 0.x"):
        cls(config)


def _mutate_after_build(config: DaytonaConfig) -> None:
    config.api_key = "second"
    config.project_id = "p-2"
    config.request_timeout = 9
    vars(config)["api_url"] = "https://self.example/api"  # too late to matter


def test_the_sync_client_snapshots_the_config_when_it_is_built() -> None:
    config = DaytonaConfig(api_key="first", project_id="p-1", request_timeout=5)
    with Daytona(config) as root:
        _mutate_after_build(config)
        impl = root.v2._impl
        assert (impl._api_key, impl._request_timeout) == ("first", 5)
        assert root._config is not config


@pytest.mark.anyio
async def test_the_async_client_snapshots_the_config_when_it_is_built() -> None:
    config = DaytonaConfig(api_key="first", project_id="p-1", request_timeout=5)
    async with AsyncDaytona(config) as root:
        _mutate_after_build(config)
        impl = root.v2
        assert (impl._api_key, impl._request_timeout) == ("first", 5)
        assert root._config is not config


def test_server_url_in_the_constructor_names_the_field() -> None:
    with pytest.raises(TypeError, match="does not take 'server_url'"):
        DaytonaConfig(api_key="k", server_url="https://self.example/api")  # type: ignore[call-arg]


def test_a_subclass_may_define_and_assign_a_moved_name() -> None:
    class Custom(Daytona):
        default_language = "python"

    client = Custom(DaytonaConfig(api_key="k"))
    client.default_language = "typescript"
    assert client.default_language == "typescript"


@pytest.mark.parametrize("cls", [Daytona, AsyncDaytona])
def test_a_subclass_instance_may_assign_a_moved_name_it_does_not_define(cls: type) -> None:
    class Custom(cls):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__(DaytonaConfig(api_key="k"))
            self.create = lambda: "own"

    assert Custom().create() == "own"
