# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import warnings
from unittest.mock import MagicMock, patch

import pytest

from daytona.common.daytona import CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams, DaytonaConfig
from daytona.common.errors import DaytonaAuthenticationError, DaytonaValidationError
from daytona.common.sandbox import Resources

SYNC_MODULE = "daytona._sync.daytona"


def _make_daytona(config=None):
    from daytona._sync.daytona import Daytona
    from daytona_api_client import Configuration

    with (
        patch(f"{SYNC_MODULE}.ApiClient") as mock_api_cls,
        patch(f"{SYNC_MODULE}.ToolboxApiClient") as mock_toolbox_cls,
    ):
        mock_api_instance = MagicMock()
        mock_api_instance.configuration = Configuration(host="https://test.daytona.io/api")
        mock_api_instance.default_headers = {}
        mock_api_instance.user_agent = ""
        mock_api_cls.return_value = mock_api_instance

        mock_toolbox_instance = MagicMock()
        mock_toolbox_instance.default_headers = {}
        mock_toolbox_cls.return_value = mock_toolbox_instance

        return Daytona(config)


class TestDaytonaInit:
    def test_init_with_config(self):
        daytona = _make_daytona(DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="us"))
        assert daytona._api_key == "test-key"
        assert daytona._api_url == "https://api.test.io"
        assert daytona._target == "us"

    def test_init_with_env_vars(self, env_with_api_key):
        daytona = _make_daytona()
        assert daytona._api_key == "test-api-key-123"
        assert daytona._api_url == "https://test.daytona.io/api"
        assert daytona._target == "us"

    def test_init_with_jwt(self, env_with_jwt):
        daytona = _make_daytona()
        assert daytona._jwt_token == "test-jwt-token-123"
        assert daytona._organization_id == "test-org-id"

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_init_without_credentials_raises(self, _mock_dotenv, monkeypatch):
        for key in [
            "DAYTONA_API_KEY",
            "DAYTONA_JWT_TOKEN",
            "DAYTONA_API_URL",
            "DAYTONA_TARGET",
            "DAYTONA_SERVER_URL",
            "DAYTONA_ORGANIZATION_ID",
        ]:
            monkeypatch.delenv(key, raising=False)

        from daytona._sync.daytona import Daytona

        with pytest.raises(
            DaytonaAuthenticationError, match="Authentication credentials not found. Set DAYTONA_API_KEY"
        ):
            Daytona()

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_default_api_url(self, _mock_dotenv, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "key")
        monkeypatch.setenv("DAYTONA_TARGET", "us")
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.delenv("DAYTONA_SERVER_URL", raising=False)
        daytona = _make_daytona()
        assert daytona._api_url == "https://app.daytona.io/api"

    @patch("daytona._utils.env.dotenv_values", return_value={})
    def test_env_server_url_warns_when_api_url_missing(self, _mock_dotenv, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "key")
        monkeypatch.setenv("DAYTONA_TARGET", "us")
        monkeypatch.setenv("DAYTONA_SERVER_URL", "https://server.daytona.io/api")
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)

        with pytest.warns(DeprecationWarning, match="DAYTONA_SERVER_URL"):
            daytona = _make_daytona()

        assert daytona._api_url == "https://server.daytona.io/api"

    def test_jwt_without_organization_id_raises(self):
        with pytest.raises(DaytonaAuthenticationError, match="DAYTONA_ORGANIZATION_ID is required"):
            _make_daytona(DaytonaConfig(jwt_token="jwt", api_url="https://api.test.io", target="us"))

    @patch(f"{SYNC_MODULE}.SyncEventDispatcher")
    def test_init_default_creates_event_dispatcher(self, mock_dispatcher, env_with_api_key):
        dispatcher = MagicMock()
        mock_dispatcher.return_value = dispatcher

        daytona = _make_daytona()

        assert daytona._event_dispatcher is dispatcher
        dispatcher.ensure_connected.assert_called_once_with()

    @patch(f"{SYNC_MODULE}.SyncEventDispatcher")
    def test_deprecated_polling_config_disables_dispatcher(self, mock_dispatcher, env_with_api_key):
        with pytest.warns(DeprecationWarning, match="Polling-only mode"):
            daytona = _make_daytona(
                DaytonaConfig(
                    api_key="test-key", api_url="https://api.test.io", target="us", use_deprecated_polling=True
                )
            )

        assert daytona._event_dispatcher is None
        mock_dispatcher.assert_not_called()

    @patch(f"{SYNC_MODULE}.SyncEventDispatcher")
    def test_deprecated_polling_env_disables_dispatcher(self, mock_dispatcher, env_with_api_key, monkeypatch):
        monkeypatch.setenv("DAYTONA_USE_DEPRECATED_POLLING", "true")

        with pytest.warns(DeprecationWarning, match="Polling-only mode"):
            daytona = _make_daytona()

        assert daytona._event_dispatcher is None
        mock_dispatcher.assert_not_called()

    @patch(f"{SYNC_MODULE}.SyncEventDispatcher")
    def test_explicit_false_beats_env_var(self, mock_dispatcher, env_with_api_key, monkeypatch):
        dispatcher = MagicMock()
        mock_dispatcher.return_value = dispatcher
        monkeypatch.setenv("DAYTONA_USE_DEPRECATED_POLLING", "true")

        daytona = _make_daytona(
            DaytonaConfig(api_key="test-key", api_url="https://api.test.io", target="us", use_deprecated_polling=False)
        )

        assert daytona._event_dispatcher is dispatcher
        dispatcher.ensure_connected.assert_called_once_with()


class TestDaytonaCreateValidation:
    def test_negative_timeout_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="Timeout must be a non-negative number"):
            daytona._create(CreateSandboxFromSnapshotParams(language="python"), timeout=-1)

    def test_negative_auto_stop_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="auto_stop_interval must be a non-negative"):
            daytona._create(CreateSandboxFromSnapshotParams(language="python", auto_stop_interval=-1), timeout=60)

    def test_negative_auto_pause_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="auto_pause_interval must be a non-negative"):
            daytona._create(CreateSandboxFromSnapshotParams(language="python", auto_pause_interval=-1), timeout=60)

    def test_auto_stop_and_auto_pause_mutual_exclusivity(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="mutually exclusive"):
            daytona._create(
                CreateSandboxFromSnapshotParams(language="python", auto_stop_interval=10, auto_pause_interval=10),
                timeout=60,
            )

    def test_ephemeral_with_auto_pause_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="Ephemeral sandboxes cannot have auto-pause enabled"):
            daytona._create(
                CreateSandboxFromSnapshotParams(language="python", ephemeral=True, auto_pause_interval=60),
                timeout=60,
            )

    def test_negative_ttl_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="ttl_minutes must be a non-negative"):
            daytona._create(CreateSandboxFromSnapshotParams(language="python", ttl_minutes=-1), timeout=60)

    def test_negative_auto_archive_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="auto_archive_interval must be a non-negative"):
            daytona._create(CreateSandboxFromSnapshotParams(language="python", auto_archive_interval=-1), timeout=60)

    def test_create_defaults_language_and_sets_label(self, env_with_api_key, sandbox_dto):
        from daytona.common.daytona import CODE_TOOLBOX_LANGUAGE_LABEL

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto
        sandbox = daytona.create()

        request = daytona._sandbox_api.create_sandbox.call_args.kwargs["_request_timeout"]
        assert request == 60
        create_request = daytona._sandbox_api.create_sandbox.call_args.args[0]
        assert create_request.labels[CODE_TOOLBOX_LANGUAGE_LABEL] == "python"
        assert sandbox.id == sandbox_dto.id

    def test_create_from_image_sets_resources(self, env_with_api_key, sandbox_dto):
        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto
        params = CreateSandboxFromImageParams(image="python:3.12", resources=Resources(cpu=2, memory=4, disk=8, gpu=1))
        daytona.create(params)
        create_request = daytona._sandbox_api.create_sandbox.call_args.args[0]
        assert create_request.cpu == 2
        assert create_request.memory == 4
        assert create_request.disk == 8
        assert create_request.gpu == 1

    def test_create_from_snapshot_sets_snapshot_and_volume_mounts(self, env_with_api_key, sandbox_dto):
        from daytona.common.volume import VolumeMount

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto
        params = CreateSandboxFromSnapshotParams(
            snapshot="snap-1",
            volumes=[VolumeMount(volume_id="vol-1", mount_path="/data", subpath="logs")],
        )

        daytona.create(params)

        create_request = daytona._sandbox_api.create_sandbox.call_args.args[0]
        assert create_request.snapshot == "snap-1"
        assert create_request.volumes[0].volume_id == "vol-1"
        assert create_request.volumes[0].subpath == "logs"


class TestDaytonaGetAndList:
    def test_get_empty_id_raises(self, env_with_api_key):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="sandbox_id_or_name is required"):
            daytona.get("")

    def test_get_returns_sandbox(self, env_with_api_key, sandbox_dto):
        from daytona._sync.sandbox import Sandbox

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.get_sandbox.return_value = sandbox_dto
        sandbox = daytona.get("test-sandbox-id")
        assert isinstance(sandbox, Sandbox)
        assert sandbox.id == "test-sandbox-id"

    def test_list_returns_iterator(self, env_with_api_key):
        import inspect

        from daytona._sync.daytona import Daytona

        # ``list`` is a generator function — calling it returns an iterator
        # without performing the API request.
        assert inspect.isgeneratorfunction(Daytona.list)

    def test_list_serializes_labels(self, env_with_api_key, sandbox_dto):
        from daytona import ListSandboxesQuery

        response = MagicMock(items=[sandbox_dto], next_cursor=None)
        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = response

        sandboxes = list(daytona.list(ListSandboxesQuery(labels={"project": "test"}, limit=10)))

        assert len(sandboxes) == 1
        kwargs = daytona._sandbox_api.list_sandboxes.call_args.kwargs
        assert json.loads(kwargs["labels"]) == {"project": "test"}
        assert kwargs["limit"] == 10
        # cursor is internal; first page fetch passes None
        assert kwargs["cursor"] is None

    def test_list_paginates_via_cursor(self, env_with_api_key, sandbox_dto):
        page1 = MagicMock(items=[sandbox_dto, sandbox_dto], next_cursor="cursor-2")
        page2 = MagicMock(items=[sandbox_dto], next_cursor=None)

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.side_effect = [page1, page2]

        sandboxes = list(daytona.list())

        assert len(sandboxes) == 3
        assert daytona._sandbox_api.list_sandboxes.call_count == 2
        # Second call must carry the cursor returned by page 1.
        second_call_kwargs = daytona._sandbox_api.list_sandboxes.call_args_list[1].kwargs
        assert second_call_kwargs["cursor"] == "cursor-2"

    def test_list_early_termination_stops_fetching(self, env_with_api_key, sandbox_dto):
        page1 = MagicMock(items=[sandbox_dto, sandbox_dto], next_cursor="cursor-2")
        # If the iterator advanced past page 1, the mock would yield page2;
        # we assert that does NOT happen.
        page2 = MagicMock(items=[sandbox_dto], next_cursor=None)

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.side_effect = [page1, page2]

        first = next(iter(daytona.list()))
        assert first is not None
        # Only page 1 was fetched.
        assert daytona._sandbox_api.list_sandboxes.call_count == 1


class TestDaytonaValidateLanguageLabel:
    def test_none_returns_python(self, env_with_api_key):
        from daytona.common.daytona import CodeLanguage

        daytona = _make_daytona()
        assert daytona._validate_language_label(None) == CodeLanguage.PYTHON

    @pytest.mark.parametrize("value", ["python", "typescript", "javascript"])
    def test_valid_language(self, env_with_api_key, value):
        daytona = _make_daytona()
        assert str(daytona._validate_language_label(value)) == value

    def test_invalid_language_raises(self, env_with_api_key):
        from daytona.common.daytona import CODE_TOOLBOX_LANGUAGE_LABEL

        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match=f"Invalid {CODE_TOOLBOX_LANGUAGE_LABEL}"):
            daytona._validate_language_label("ruby")


DEFAULT_API_URL = "https://app.daytona.io/api"
_CREDENTIAL_ENV_KEYS = (
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_SERVER_URL",
    "DAYTONA_TARGET",
    "DAYTONA_JWT_TOKEN",
    "DAYTONA_ORGANIZATION_ID",
)


class TestCwdDotenvCannotRedirectTheEndpoint:
    """A `.env` in the working directory must not decide where the credential is sent.

    The working directory is frequently a cloned repository, so its files are authored by a
    third party. The credential comes from the caller; the endpoint must never come from
    a file the caller did not write.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        for key in _CREDENTIAL_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @staticmethod
    def _construct(config=None):
        """Construct a client, returning it alongside every warning it emitted.

        The endpoint assertion has to be the primary signal, so construction must not
        happen inside `pytest.warns` — a missing warning would fail the test before the
        resolved URL is ever checked.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            daytona = _make_daytona(config)
        return daytona, [str(w.message) for w in caught]

    @staticmethod
    def _ignored_endpoint_warnings(messages, name="DAYTONA_API_URL"):
        return [m for m in messages if name in m and "was ignored" in m]

    def test_dotenv_api_url_is_ignored(self, _isolated_cwd):
        (_isolated_cwd / ".env").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(DaytonaConfig(api_key="victim-key"))

        assert daytona._api_url == DEFAULT_API_URL
        assert daytona._api_key == "victim-key"
        assert self._ignored_endpoint_warnings(messages)

    def test_dotenv_local_api_url_is_ignored(self, _isolated_cwd):
        (_isolated_cwd / ".env.local").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(DaytonaConfig(api_key="victim-key"))

        assert daytona._api_url == DEFAULT_API_URL
        assert self._ignored_endpoint_warnings(messages)

    def test_dotenv_server_url_is_ignored(self, _isolated_cwd):
        (_isolated_cwd / ".env").write_text("DAYTONA_SERVER_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(DaytonaConfig(api_key="victim-key"))

        assert daytona._api_url == DEFAULT_API_URL
        assert self._ignored_endpoint_warnings(messages, "DAYTONA_SERVER_URL")

    def test_dotenv_cannot_redirect_a_credential_taken_from_the_process_environment(self, _isolated_cwd, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "victim-key")
        (_isolated_cwd / ".env").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct()

        assert daytona._api_url == DEFAULT_API_URL
        assert daytona._api_key == "victim-key"
        assert self._ignored_endpoint_warnings(messages)

    def test_dotenv_cannot_redirect_even_when_it_also_supplies_the_credential(self, _isolated_cwd):
        """A file that supplies both must still not move the endpoint.

        Same-source pairing would allow this; resolving the endpoint from the process
        environment only does not.
        """
        (_isolated_cwd / ".env").write_text(
            "DAYTONA_API_KEY=attacker-key\nDAYTONA_API_URL=http://attacker.example/api\n"
        )

        daytona, messages = self._construct()

        assert daytona._api_url == DEFAULT_API_URL
        assert self._ignored_endpoint_warnings(messages)

    def test_process_environment_api_url_still_wins(self, _isolated_cwd, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_URL", "https://chosen-by-env.example/api")
        (_isolated_cwd / ".env").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(DaytonaConfig(api_key="victim-key"))

        assert daytona._api_url == "https://chosen-by-env.example/api"
        assert self._ignored_endpoint_warnings(messages)

    def test_explicit_api_url_still_wins(self, _isolated_cwd):
        (_isolated_cwd / ".env").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(DaytonaConfig(api_key="victim-key", api_url="https://chosen.example/api"))

        assert daytona._api_url == "https://chosen.example/api"
        assert self._ignored_endpoint_warnings(messages)

    def test_fully_configured_client_is_still_told_about_a_hostile_dotenv(self, _isolated_cwd):
        """Reported even when explicit config already made the caller immune.

        The value of the report is that the working directory is hostile, which the caller
        wants to know regardless of how this particular client was configured.
        """
        (_isolated_cwd / ".env").write_text("DAYTONA_API_URL=http://attacker.example/api\n")

        daytona, messages = self._construct(
            DaytonaConfig(api_key="victim-key", api_url="https://chosen.example/api", target="us")
        )

        assert daytona._api_url == "https://chosen.example/api"
        assert self._ignored_endpoint_warnings(messages)

    def test_documented_dotenv_layout_still_works_and_stays_quiet(self, _isolated_cwd):
        """The documented `.env` sets the endpoint to its default, so nothing changes."""
        (_isolated_cwd / ".env").write_text(
            f"DAYTONA_API_KEY=victim-key\nDAYTONA_API_URL={DEFAULT_API_URL}\nDAYTONA_TARGET=us\n"
        )

        daytona, messages = self._construct()

        assert daytona._api_url == DEFAULT_API_URL
        assert daytona._api_key == "victim-key"
        assert daytona._target == "us"
        assert self._ignored_endpoint_warnings(messages) == []
