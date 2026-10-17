# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typing_extensions import assert_type

import daytona
import daytona.v1 as daytona_v1
from daytona._async.daytona import AsyncDaytona as AsyncDaytonaImpl
from daytona._sync.daytona import Daytona as DaytonaImpl
from daytona._sync.sandbox import Sandbox
from daytona._versioning import _reset_deprecation_warnings_for_tests
from daytona.common.daytona import V1_API_URL, CodeLanguage, DaytonaConfig
from daytona.v1 import SandboxV1
from daytona_api_client import Configuration as SyncConfiguration
from daytona_api_client_async import Configuration as AsyncConfiguration

SHARED_ERROR_EXPORTS = (
    "DaytonaError",
    "DaytonaBadRequestError",
    "DaytonaAuthenticationError",
    "DaytonaForbiddenError",
    "DaytonaNotFoundError",
    "DaytonaTimeoutError",
    "DaytonaConflictError",
    "DaytonaGoneError",
    "DaytonaUnprocessableEntityError",
    "DaytonaRateLimitError",
    "DaytonaInternalServerError",
    "DaytonaServiceUnavailableError",
    "DaytonaConnectionError",
    "DaytonaValidationError",
    "DaytonaAuthorizationError",
)


@pytest.fixture(autouse=True)
def reset_deprecation_state(monkeypatch: pytest.MonkeyPatch):
    # Warning counts must not depend on the developer's shell: these variables emit their own
    # DeprecationWarnings. Tests that need one set it again with monkeypatch.
    for name in ("DAYTONA_TARGET", "DAYTONA_USE_DEPRECATED_POLLING", "DAYTONA_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    _reset_deprecation_warnings_for_tests()
    yield
    _reset_deprecation_warnings_for_tests()


def _make_root_daytona(config: DaytonaConfig | None = None) -> daytona.Daytona:
    with (
        patch("daytona._sync.daytona.ApiClient") as mock_api_cls,
        patch("daytona._sync.daytona.ToolboxApiClient") as mock_toolbox_cls,
        patch("daytona._sync.daytona.SyncEventDispatcher"),
    ):
        mock_api_instance = MagicMock()
        mock_api_instance.configuration = SyncConfiguration(host="https://test.daytona.io/api")
        mock_api_instance.default_headers = {}
        mock_api_instance.user_agent = ""
        mock_api_cls.return_value = mock_api_instance

        mock_toolbox_instance = MagicMock()
        mock_toolbox_instance.default_headers = {}
        mock_toolbox_cls.return_value = mock_toolbox_instance

        return daytona.Daytona(config)


def _make_root_async_daytona(config: DaytonaConfig | None = None) -> daytona.AsyncDaytona:
    with (
        patch("daytona._async.daytona.ApiClient") as mock_api_cls,
        patch("daytona._async.daytona.ToolboxApiClient") as mock_toolbox_cls,
        patch("daytona._async.daytona.AsyncEventDispatcher"),
    ):
        mock_api_instance = MagicMock()
        mock_api_instance.configuration = AsyncConfiguration(host="https://test.daytona.io/api")
        mock_api_instance.default_headers = {}
        mock_api_instance.user_agent = ""
        mock_api_instance.close = AsyncMock()
        mock_api_cls.return_value = mock_api_instance

        mock_toolbox_instance = MagicMock()
        mock_toolbox_instance.default_headers = {}
        mock_toolbox_instance.close = AsyncMock()
        mock_toolbox_cls.return_value = mock_toolbox_instance

        return daytona.AsyncDaytona(config)


def _public_v1_members(client: daytona_v1.Daytona | daytona_v1.AsyncDaytona) -> list[str]:
    instance_members = {name for name in vars(client) if not name.startswith("_")}
    class_members = {name for name in dir(type(client)) if not name.startswith("_")}
    return sorted(instance_members | class_members)


class TestRootDaytonaWrapper:
    def test_v1_property_returns_same_sync_instance(self):
        v1_client = MagicMock()

        with patch("daytona._root_sync._DaytonaV1", return_value=v1_client):
            client = daytona.Daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        assert client.v1 is v1_client
        assert client.v1 is client.v1

    @pytest.mark.asyncio
    async def test_v1_property_returns_same_async_instance(self):
        v1_client = MagicMock()
        v1_client.close = AsyncMock()

        with patch("daytona._root_async._AsyncDaytonaV1", return_value=v1_client):
            client = daytona.AsyncDaytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        assert client.v1 is v1_client
        assert client.v1 is client.v1
        await client.close()
        v1_client.close.assert_awaited_once()

    def test_root_methods_and_services_forward_to_v1(self):
        v1_client = MagicMock()
        sandbox = MagicMock()
        sandboxes = iter([sandbox])
        volume_service = MagicMock()
        snapshot_service = MagicMock()
        secret_service = MagicMock()
        warm_pool_service = MagicMock()
        v1_client.create.return_value = sandbox
        v1_client.get.return_value = sandbox
        v1_client.list.return_value = sandboxes
        v1_client.volume = volume_service
        v1_client.snapshot = snapshot_service
        v1_client.secret = secret_service
        v1_client.warm_pool = warm_pool_service

        with patch("daytona._root_sync._DaytonaV1", return_value=v1_client):
            client = daytona.Daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert client.create(timeout=5) is sandbox
            assert client.get("sandbox-id") is sandbox
            assert client.list() is sandboxes
            client.start(sandbox, timeout=7)
            client.stop(sandbox, timeout=8)
            client.delete(sandbox, timeout=9, wait=True)
            assert client.volume is volume_service
            assert client.snapshot is snapshot_service
            assert client.secret is secret_service
            assert client.warm_pool is warm_pool_service

        deprecations = [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecations) == 1
        assert "daytona.v1.create" in str(deprecations[0].message)
        v1_client.create.assert_called_once_with(None, timeout=5, on_snapshot_create_logs=None)
        v1_client.get.assert_called_once_with("sandbox-id", request_timeout=None)
        v1_client.list.assert_called_once_with(None, request_timeout=None)
        v1_client.start.assert_called_once_with(sandbox, 7)
        v1_client.stop.assert_called_once_with(sandbox, 8)
        v1_client.delete.assert_called_once_with(sandbox, 9, wait=True)

    def test_root_default_language_setter_forwards_to_v1(self):
        v1_client = MagicMock()

        with patch("daytona._root_sync._DaytonaV1", return_value=v1_client):
            client = daytona.Daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client.default_language = CodeLanguage.TYPESCRIPT

        assert v1_client.default_language == CodeLanguage.TYPESCRIPT

    def test_root_warning_is_attributed_to_the_caller(self):
        v1_client = MagicMock()

        with patch("daytona._root_sync._DaytonaV1", return_value=v1_client):
            client = daytona.Daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.create()

        assert [warning.filename for warning in caught] == [__file__]

    def test_v1_usage_does_not_warn(self):
        v1_client = MagicMock()

        with patch("daytona._root_sync._DaytonaV1", return_value=v1_client):
            client = daytona.Daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.v1.create()

        assert caught == []
        v1_client.create.assert_called_once_with()

    def test_real_v1_client_members_do_not_warn(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        client = _make_root_daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            v1 = client.v1
            v1.default_language = CodeLanguage.TYPESCRIPT
            assert v1.default_language == CodeLanguage.TYPESCRIPT
            _ = (v1.volume, v1.snapshot, v1.secret, v1.warm_pool)

        assert [warning for warning in caught if issubclass(warning.category, DeprecationWarning)] == []

    def test_root_close_and_context_manager_close_the_real_v1_client(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        config = DaytonaConfig(api_key="test-key", api_url="https://api.test.io")

        with _make_root_daytona(config) as client:
            http_client = client.v1._http_client  # pylint: disable=protected-access
            assert not http_client.is_closed
        assert http_client.is_closed

        client = _make_root_daytona(config)
        client.close()
        client.close()
        assert client.v1._http_client.is_closed  # pylint: disable=protected-access

    def test_root_deprecation_warning_from_existing_env_vars_is_attributed_to_the_caller(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.setenv("DAYTONA_SERVER_URL", "https://api.test.io")
        monkeypatch.setenv("DAYTONA_USE_DEPRECATED_POLLING", "true")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = daytona.Daytona(DaytonaConfig(api_key="test-key"))

        relevant = [w for w in caught if "DAYTONA_SERVER_URL" in str(w.message) or "Polling-only" in str(w.message)]
        assert len(relevant) == 2
        assert [w.filename for w in relevant] == [__file__, __file__]

    def test_root_wrapper_has_v1_parity_with_sync_client(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)

        root_client = _make_root_daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            missing_members = [name for name in _public_v1_members(root_client.v1) if not hasattr(root_client, name)]

        assert missing_members == []


class TestRootAsyncDaytonaWrapper:
    @pytest.mark.asyncio
    async def test_root_methods_forward_to_async_v1(self):
        v1_client = MagicMock()
        sandbox = MagicMock()
        sandboxes = MagicMock(spec=Iterator)
        v1_client.create = AsyncMock(return_value=sandbox)
        v1_client.get = AsyncMock(return_value=sandbox)
        v1_client.delete = AsyncMock()
        v1_client.start = AsyncMock()
        v1_client.stop = AsyncMock()
        v1_client.list.return_value = sandboxes
        v1_client.close = AsyncMock()

        with patch("daytona._root_async._AsyncDaytonaV1", return_value=v1_client):
            client = daytona.AsyncDaytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert await client.create(timeout=5) is sandbox
            assert await client.get("sandbox-id") is sandbox
            assert client.list() is sandboxes
            await client.start(sandbox, timeout=7)
            await client.stop(sandbox, timeout=8)
            await client.delete(sandbox, timeout=9, wait=True)

        deprecations = [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecations) == 1
        assert "daytona.v1.create" in str(deprecations[0].message)
        v1_client.create.assert_awaited_once_with(None, timeout=5, on_snapshot_create_logs=None)
        v1_client.get.assert_awaited_once_with("sandbox-id", request_timeout=None)
        v1_client.start.assert_awaited_once_with(sandbox, 7)
        v1_client.stop.assert_awaited_once_with(sandbox, 8)
        v1_client.delete.assert_awaited_once_with(sandbox, 9, wait=True)

    def test_root_wrapper_has_v1_parity_with_async_client(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)

        root_client = _make_root_async_daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            missing_members = [name for name in _public_v1_members(root_client.v1) if not hasattr(root_client, name)]

        assert missing_members == []


@pytest.mark.parametrize(
    ("root_cls", "v1_patch_module", "v1_patch_target"),
    [(daytona.Daytona, "_root_sync", "_DaytonaV1"), (daytona.AsyncDaytona, "_root_async", "_AsyncDaytonaV1")],
)
def test_root_service_setters_forward_to_v1(root_cls: type, v1_patch_module: str, v1_patch_target: str):
    v1_client = MagicMock()

    with patch(f"daytona.{v1_patch_module}.{v1_patch_target}", return_value=v1_client):
        client = root_cls(DaytonaConfig(api_key="test-key", api_url="https://api.test.io"))

    replacements = {name: MagicMock() for name in ("volume", "snapshot", "secret", "warm_pool")}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for name, value in replacements.items():
            setattr(client, name, value)

    for name, value in replacements.items():
        assert getattr(v1_client, name) is value
    assert len([w for w in caught if issubclass(w.category, DeprecationWarning)]) == 1


@pytest.mark.parametrize(
    ("root_cls", "impl_cls"), [(daytona.Daytona, DaytonaImpl), (daytona.AsyncDaytona, AsyncDaytonaImpl)]
)
def test_root_forwarders_match_v1_method_signatures(root_cls: type, impl_cls: type):
    def shape(func: object) -> list[tuple[str, object, object]]:
        signature = inspect.signature(func)  # pyright: ignore[reportArgumentType]
        return [(param.name, param.kind, param.default) for param in signature.parameters.values()]

    mismatched = [
        name
        for name, member in inspect.getmembers(impl_cls, inspect.isfunction)
        if not name.startswith("_") and shape(member) != shape(getattr(root_cls, name))
    ]
    assert mismatched == []


class TestTargetDeprecation:
    def test_target_config_warns_once_per_process(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="us")
            DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="eu")

        deprecations = [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecations) == 1
        assert "DaytonaConfig.target" in str(deprecations[0].message)
        assert "DAYTONA_TARGET" in str(deprecations[0].message)

    def test_target_assignment_after_construction_warns_once(self):
        config = DaytonaConfig(api_key="test-key", api_url="https://api.test.io")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config.target = "us"
            config.target = "eu"
            assert config.target == "eu"

        assert [warning.filename for warning in caught if "DaytonaConfig.target" in str(warning.message)] == [__file__]

    def test_warnings_raise_on_every_call_when_turned_into_errors(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            for _ in range(2):
                with pytest.raises(DeprecationWarning):
                    DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="us")

    def test_target_warning_is_attributed_to_the_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="us")
            assert config.target == "us"
            assert config.target == "us"

        assert [warning.filename for warning in caught] == [__file__]

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_target_env_warning_is_attributed_to_the_caller(self, _mock_dotenv, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "test-api-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://api.test.io")
        monkeypatch.setenv("DAYTONA_TARGET", "us")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _make_root_daytona()

        target_warnings = [warning for warning in caught if "DaytonaConfig.target" in str(warning.message)]
        assert [warning.filename for warning in target_warnings] == [__file__]

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_empty_config_target_falls_back_to_env_with_warning(self, _mock_dotenv, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "test-api-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://api.test.io")
        monkeypatch.setenv("DAYTONA_TARGET", "us")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = _make_root_daytona(DaytonaConfig(target=""))

        assert client.v1._target == "us"  # pylint: disable=protected-access
        assert len([warning for warning in caught if "DaytonaConfig.target" in str(warning.message)]) == 1

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_target_env_warns_once_per_process(self, _mock_dotenv, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "test-api-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://api.test.io")
        monkeypatch.setenv("DAYTONA_TARGET", "us")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _make_root_daytona()
            _make_root_daytona()

        deprecations = [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecations) == 1
        assert "DaytonaConfig.target" in str(deprecations[0].message)


class TestV1NamespaceExports:
    def test_v1_typing_exports_impl_classes(self):
        assert_type(daytona_v1.Daytona, type[DaytonaImpl])
        assert_type(daytona_v1.AsyncDaytona, type[AsyncDaytonaImpl])
        assert_type(SandboxV1, type[Sandbox])

    def test_v1_aliases_preserve_identity(self):
        alias_pairs = {
            "AsyncDaytonaV1": daytona_v1.AsyncDaytona,
            "AsyncSandboxV1": daytona_v1.AsyncSandbox,
            "DaytonaV1": daytona_v1.Daytona,
            "DaytonaAuthenticationErrorV1": daytona_v1.DaytonaAuthenticationError,
            "DaytonaAuthorizationErrorV1": daytona_v1.DaytonaAuthorizationError,
            "DaytonaConfigV1": daytona_v1.DaytonaConfig,
            "DaytonaConflictErrorV1": daytona_v1.DaytonaConflictError,
            "DaytonaConnectionErrorV1": daytona_v1.DaytonaConnectionError,
            "DaytonaErrorV1": daytona_v1.DaytonaError,
            "DaytonaNotFoundErrorV1": daytona_v1.DaytonaNotFoundError,
            "DaytonaRateLimitErrorV1": daytona_v1.DaytonaRateLimitError,
            "DaytonaTimeoutErrorV1": daytona_v1.DaytonaTimeoutError,
            "DaytonaValidationErrorV1": daytona_v1.DaytonaValidationError,
            "ImageV1": daytona_v1.Image,
            "ListSandboxesQueryV1": daytona_v1.ListSandboxesQuery,
            "ResourcesV1": daytona_v1.Resources,
            "SandboxV1": daytona_v1.Sandbox,
            "VolumeMountV1": daytona_v1.VolumeMount,
        }

        for alias_name, target in alias_pairs.items():
            assert getattr(daytona_v1, alias_name) is target

    def test_v1_all_is_root_all_plus_aliases(self):
        aliases = [name for name in daytona_v1.__all__ if name not in daytona.__all__]
        assert daytona_v1.__all__ == [*daytona.__all__, *aliases]
        assert all(name.endswith("V1") for name in aliases)
        for name in daytona_v1.__all__:
            assert getattr(daytona_v1, name) is not None

    def test_v1_namespace_type_checks_as_an_installed_library(self, tmp_path):
        # Run the type checker on a consumer file with the package on the search path rather than
        # as local source, which is how installed users see daytona.v1.
        consumer = tmp_path / "consumer.py"
        consumer.write_text(
            "import daytona\n"
            "from daytona.v1 import AsyncDaytona, Daytona, SandboxV1\n"
            "from daytona._sync.daytona import Daytona as Impl\n"
            "def use(client: Daytona) -> None:\n    print(client)\n"
            "use(daytona.Daytona().v1)\n"
            "impl: type[Impl] = daytona.v1.Daytona\n"
            "async_impl = AsyncDaytona\n"
            "sandbox_type = SandboxV1\n"
        )
        src_dir = Path(daytona.__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "basedpyright", "--pythonpath", sys.executable, str(consumer)],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(src_dir)},
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_sync_root_import_does_not_load_the_async_client(self):
        code = (
            "import sys; from daytona import Daytona; "
            "print(any(name.startswith('daytona._async') for name in sys.modules))"
        )
        src_dir = Path(daytona.__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONPATH": str(src_dir)},
        )
        assert result.stdout.strip() == "False"

    def test_v1_api_url_is_exported(self):
        assert V1_API_URL == "https://app.daytona.io/api"
        assert daytona.V1_API_URL == V1_API_URL
        assert daytona_v1.V1_API_URL == V1_API_URL

    def test_root_and_v1_export_shared_errors(self):
        for module in (daytona, daytona_v1):
            exported_names = set(module.__all__)
            for name in SHARED_ERROR_EXPORTS:
                assert name in exported_names
                assert getattr(module, name) is not None

        assert daytona.DaytonaValidationError is daytona.DaytonaBadRequestError
        assert daytona.DaytonaAuthorizationError is daytona.DaytonaForbiddenError
        assert daytona_v1.DaytonaValidationError is daytona_v1.DaytonaBadRequestError
        assert daytona_v1.DaytonaAuthorizationError is daytona_v1.DaytonaForbiddenError
