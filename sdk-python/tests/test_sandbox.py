# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time
import warnings
from unittest.mock import MagicMock

import pytest

from daytona.common.errors import (
    DaytonaError,
    DaytonaQueueTimeoutError,
    DaytonaSpotEvictedError,
    DaytonaTimeoutError,
    DaytonaValidationError,
)
from daytona.internal import wait_poll
from daytona_api_client import SandboxState, UpdateSandboxSecrets

from .conftest import FakeEventDispatcher, make_pydantic_validation_error, make_sandbox_dto


def _polling_only_manager() -> MagicMock:
    """A manager without event streaming: waits take no subscription and poll at the fast cadence."""
    manager = MagicMock()
    manager.subscribe.return_value = ""
    manager.dispatcher = None
    return manager


def make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
    from daytona._sync.sandbox import Sandbox

    return Sandbox(
        sandbox_dto,
        mock_toolbox_api_client,
        mock_sandbox_api,
        "python",
        subscription_manager=_polling_only_manager(),
        http_client=MagicMock(),
    )


class TestSandboxInit:
    def test_sandbox_properties(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.id == "test-sandbox-id"
        assert sandbox.name == "test-sandbox"
        assert sandbox.state == SandboxState.STARTED
        assert sandbox.cpu == 4
        assert sandbox.memory == 8
        assert sandbox.disk == 30
        assert sandbox.user == "daytona"
        assert sandbox.public is False
        assert sandbox.sandbox_class == "container"
        assert sandbox.warm_pool_id == "wp-123"
        assert sandbox.daemon_version == "0.52.0"
        assert sandbox.gpu_type is None
        assert sandbox.desired_state is None
        assert sandbox.otel_endpoint_override == "https://otel.example.com"

    def test_sandbox_has_subsystems(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.fs is not None
        assert sandbox.git is not None
        assert sandbox.process is not None
        assert sandbox.computer_use is not None
        assert sandbox.code_interpreter is not None

    def test_process_dto_preserves_proxy_url_when_missing(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(toolbox_proxy_url="http://hydrated:2280")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        update_dto = make_sandbox_dto(toolbox_proxy_url="")
        sandbox._Sandbox__process_sandbox_dto(update_dto)
        assert sandbox.toolbox_proxy_url == "http://hydrated:2280"

    def test_kvm_hydrated_from_dto(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(kvm=True)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.kvm is True

    def test_kvm_default_false_from_dto(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto()
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.kvm is False


class TestSandboxLifecycleSettings:
    def test_negative_autostop_interval_raises(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-stop interval must be a non-negative"):
            sandbox.set_autostop_interval(-1)

    def test_valid_autostop_interval(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_autostop_interval(30)
        assert sandbox.auto_stop_interval == 30
        mock_sandbox_api.set_autostop_interval.assert_called_once_with(sandbox.id, 30, _request_timeout=None)

    @pytest.mark.parametrize("public", [True, False])
    def test_set_public(self, public, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_public(public)
        assert sandbox.public is public
        mock_sandbox_api.update_public_status.assert_called_once_with(sandbox.id, public, _request_timeout=None)

    def test_negative_autopause_interval_raises(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-pause interval must be a non-negative"):
            sandbox.set_auto_pause_interval(-1)

    def test_valid_autopause_interval(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_auto_pause_interval(30)
        assert sandbox.auto_pause_interval == 30
        mock_sandbox_api.set_auto_pause_interval.assert_called_once_with(sandbox.id, 30)

    def test_negative_auto_archive_interval_raises(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-archive interval must be a non-negative"):
            sandbox.set_auto_archive_interval(-1)

    def test_valid_auto_archive_interval(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_auto_archive_interval(60)
        assert sandbox.auto_archive_interval == 60

    def test_auto_delete_interval(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_auto_delete_interval(120)
        assert sandbox.auto_delete_interval == 120
        sandbox.set_auto_delete_interval(-1)
        assert sandbox.auto_delete_interval == -1

    def test_negative_ttl_raises(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="TTL must be a non-negative"):
            sandbox.set_ttl(-1)

    def test_valid_ttl_calls_api(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.set_ttl(60)
        mock_sandbox_api.set_ttl.assert_called_once_with(sandbox.id, 60, _request_timeout=None)


class TestSandboxOperations:
    def test_set_labels(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        new_labels = {"project": "test", "env": "dev"}
        mock_response = MagicMock(labels=new_labels)
        mock_sandbox_api.replace_labels.return_value = mock_response
        result = sandbox.set_labels(new_labels)
        assert result == new_labels
        assert sandbox.labels == new_labels

    def test_create_lsp_server(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        from daytona._sync.lsp_server import LspServer

        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        lsp = sandbox.create_lsp_server("python", "/workspace/project")
        assert isinstance(lsp, LspServer)

    def test_refresh_data(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED, cpu=8)
        sandbox.refresh_data()
        assert sandbox.state == SandboxState.STOPPED
        assert sandbox.cpu == 8

    def test_get_user_home_dir(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox._info_api = MagicMock(get_user_home_dir=MagicMock(return_value=MagicMock(dir="/home/daytona")))
        assert sandbox.get_user_home_dir() == "/home/daytona"

    def test_get_work_dir(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox._info_api = MagicMock(get_work_dir=MagicMock(return_value=MagicMock(dir="/workspace")))
        assert sandbox.get_work_dir() == "/workspace"

    def test_get_user_root_dir_delegates_to_home_dir(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox._info_api = MagicMock(get_user_home_dir=MagicMock(return_value=MagicMock(dir="/home/daytona")))

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert sandbox.get_user_root_dir() == "/home/daytona"

    def test_preview_and_ssh_operations_delegate(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_port_preview_url.return_value = MagicMock(url="https://preview")
        mock_sandbox_api.create_ssh_access.return_value = MagicMock(token="ssh-token")
        mock_sandbox_api.validate_ssh_access.return_value = MagicMock(valid=True)

        assert sandbox.get_preview_link(3000).url == "https://preview"
        assert sandbox.create_ssh_access(10).token == "ssh-token"
        assert sandbox.validate_ssh_access("token").valid is True
        sandbox.revoke_ssh_access("token")
        sandbox.refresh_activity()

        mock_sandbox_api.get_port_preview_url.assert_called_once_with(sandbox.id, 3000, _request_timeout=None)
        mock_sandbox_api.revoke_ssh_access.assert_called_once_with(sandbox.id, "token", _request_timeout=None)
        mock_sandbox_api.update_last_activity.assert_called_once_with(sandbox.id, _request_timeout=None)

    def test_update_secrets(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.update_sandbox_secrets.return_value = make_sandbox_dto(env={"FOO": "placeholder"})

        sandbox.update_secrets({"FOO": "foo-secret", "BAR": "bar"})

        mock_sandbox_api.update_sandbox_secrets.assert_called_once()
        sandbox_id, body = mock_sandbox_api.update_sandbox_secrets.call_args.args
        assert sandbox_id == sandbox.id
        assert isinstance(body, UpdateSandboxSecrets)
        assert body.secrets == [{"FOO": "foo-secret"}, {"BAR": "bar"}]
        assert sandbox.env == {"FOO": "placeholder"}

    def test_update_secrets_empty_dict_detaches_all(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.update_sandbox_secrets.return_value = make_sandbox_dto(env={})

        sandbox.update_secrets({})

        mock_sandbox_api.update_sandbox_secrets.assert_called_once()
        _, body = mock_sandbox_api.update_sandbox_secrets.call_args.args
        assert body.secrets == []

    def test_update_env(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)
        # The daemon responds with a status message, which update_env discards.
        sandbox._server_api = MagicMock(
            update_env=MagicMock(return_value={"message": "Environment updated successfully"})
        )

        result = sandbox.update_env({"A": "1"}, unset=["B"])

        assert result is None
        sandbox._server_api.update_env.assert_called_once()
        request = sandbox._server_api.update_env.call_args.kwargs["request"]
        assert request.set == {"A": "1"}
        assert request.unset == ["B"]
        assert request.unset_value_prefix is None


class TestSandboxWaitForStart:
    def test_error_state_raises(self, mock_toolbox_api_client, mock_sandbox_api):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="build failed")
        sandbox = make_sandbox(error_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.return_value = error_dto
        with pytest.raises(DaytonaError, match="Failure during waiting for sandbox to start"):
            sandbox.wait_for_sandbox_start(timeout=0)


class TestSandboxQueueTimeout:
    def test_queue_timeout_fields_hydrated_from_dto(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(queue_timeout=5, queue_timed_out_at="2026-08-13T12:00:00.000Z")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.queue_timeout == 5
        assert sandbox.queue_timed_out_at == "2026-08-13T12:00:00.000Z"

    def test_queue_timeout_fields_default_none(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api)
        assert sandbox.queue_timeout is None
        assert sandbox.queue_timed_out_at is None

    def test_wait_for_start_raises_queue_timeout_error(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(
            make_sandbox_dto(state=SandboxState.PENDING_BUILD), mock_toolbox_api_client, mock_sandbox_api
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(
            state=SandboxState.DESTROYED, queue_timeout=1, queue_timed_out_at="2026-08-13T12:00:00.000Z"
        )
        with pytest.raises(DaytonaQueueTimeoutError, match="waiting 1 minute for a runner") as exc_info:
            sandbox.wait_for_sandbox_start(timeout=0)
        assert isinstance(exc_info.value, DaytonaTimeoutError)

    def test_queue_timeout_message_omits_duration_when_unknown(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.DESTROYED, queue_timed_out_at="2026-08-13T12:00:00Z")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(
            DaytonaQueueTimeoutError, match=r"was destroyed after waiting for a runner \(queue timed out at"
        ):
            sandbox.wait_for_sandbox_start(timeout=0)

    def test_wait_for_start_raises_spot_evicted_error(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(
            state=SandboxState.DESTROYED, spot_evicted_at="2026-08-13T12:00:00.000Z"
        )
        with pytest.raises(DaytonaSpotEvictedError, match="evicted by spot preemption"):
            sandbox.wait_for_sandbox_start(timeout=0)

    def test_cached_queue_timed_out_state_raises_immediately(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.DESTROYED, queue_timeout=2, queue_timed_out_at="2026-08-13T12:00:00Z")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        with pytest.raises(DaytonaQueueTimeoutError):
            sandbox.wait_for_sandbox_start(timeout=0)
        mock_sandbox_api.get_sandbox.assert_not_called()

    def test_delete_wait_succeeds_for_queue_timed_out_sandbox(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(
            make_sandbox_dto(state=SandboxState.PENDING_BUILD), mock_toolbox_api_client, mock_sandbox_api
        )
        mock_sandbox_api.delete_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYING)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(
            state=SandboxState.DESTROYED, queue_timeout=1, queue_timed_out_at="2026-08-13T12:00:00.000Z"
        )
        sandbox.delete(timeout=0, wait=True)
        assert sandbox.state == SandboxState.DESTROYED


class TestSandboxPollingSemantics:
    def test_cached_error_recovers_after_refresh(self, mock_toolbox_api_client, mock_sandbox_api):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="transient")
        sandbox = make_sandbox(error_dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox.wait_for_sandbox_start(timeout=0)
        assert sandbox.state == SandboxState.STARTED

    def test_validation_error_tolerated_during_stop_wait(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [
            make_pydantic_validation_error(),
            make_sandbox_dto(state=SandboxState.STOPPED),
        ]
        sandbox.wait_for_sandbox_stop(timeout=0)
        assert sandbox.state == SandboxState.STOPPED

    def test_non_validation_error_aborts_stop_wait(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = Exception("401 Unauthorized")
        with pytest.raises(Exception, match="401 Unauthorized"):
            sandbox.wait_for_sandbox_stop(timeout=0)

    def test_error_only_mentioning_validation_in_message_aborts_stop_wait(
        self, mock_toolbox_api_client, mock_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = Exception("500 upstream failure: validation error in body")
        with pytest.raises(Exception, match="500 upstream failure"):
            sandbox.wait_for_sandbox_stop(timeout=0)

    def test_delete_none_response_does_not_raise(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.delete_sandbox.return_value = None
        sandbox.delete(timeout=0)
        assert sandbox.state == SandboxState.STARTED

    def test_delete_default_does_not_wait(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.delete_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYING)
        sandbox.delete(timeout=0)
        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.DESTROYING

    def test_delete_wait_true_blocks_until_destroyed(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.delete_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYING)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYED)
        sandbox.delete(timeout=0, wait=True)
        mock_sandbox_api.get_sandbox.assert_called()
        assert sandbox.state == SandboxState.DESTROYED

    def test_pause_resolves_on_stopped(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.pause_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.PAUSING),
            make_sandbox_dto(state=SandboxState.STOPPED),
        ]
        sandbox.pause(timeout=0)
        assert sandbox.state == SandboxState.STOPPED

    def test_fork(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED, toolbox_proxy_url="http://original:2280")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        fork_dto = make_sandbox_dto(
            sandbox_id="forked-id",
            state=SandboxState.STARTED,
            toolbox_proxy_url="http://forked:2280",
        )
        mock_sandbox_api.fork_sandbox = MagicMock(return_value=fork_dto)
        mock_sandbox_api.get_sandbox = MagicMock(
            return_value=make_sandbox_dto(
                sandbox_id="forked-id", state=SandboxState.STARTED, toolbox_proxy_url="http://forked:2280"
            )
        )

        forked = sandbox.fork(name="my-fork", timeout=0)
        assert forked.id == "forked-id"

    def test_experimental_fork_delegates_and_warns(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED, toolbox_proxy_url="http://original:2280")
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        fork_dto = make_sandbox_dto(
            sandbox_id="forked-id",
            state=SandboxState.STARTED,
            toolbox_proxy_url="http://forked:2280",
        )
        mock_sandbox_api.fork_sandbox = MagicMock(return_value=fork_dto)
        mock_sandbox_api.get_sandbox = MagicMock(
            return_value=make_sandbox_dto(
                sandbox_id="forked-id", state=SandboxState.STARTED, toolbox_proxy_url="http://forked:2280"
            )
        )

        with pytest.warns(DeprecationWarning, match="Use `fork` instead"):
            forked = sandbox._experimental_fork(name="my-fork", timeout=0)
        assert forked.id == "forked-id"

    def test_experimental_create_snapshot_delegates_and_warns(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        snapshot_dto = make_sandbox_dto(state=SandboxState.STARTED)
        mock_sandbox_api.create_sandbox_snapshot = MagicMock(return_value=snapshot_dto)
        mock_sandbox_api.get_sandbox = MagicMock(return_value=make_sandbox_dto(state=SandboxState.STARTED))

        with pytest.warns(DeprecationWarning, match="Use `create_snapshot` instead"):
            sandbox._experimental_create_snapshot("my-snapshot", timeout=0)


@pytest.mark.parametrize("public", [True, False])
def test_public_status_path_uses_json_booleans(public):
    from daytona_api_client.api_client import ApiClient
    from daytona_api_client_async.api_client import ApiClient as AsyncApiClient

    for client in (ApiClient(), AsyncApiClient()):
        _, url, *_ = client.param_serialize(
            method="POST",
            resource_path="/sandbox/{sandboxIdOrName}/public/{isPublic}",
            path_params={"sandboxIdOrName": "sb-1", "isPublic": public},
        )
        assert url.endswith(f"/sandbox/sb-1/public/{str(public).lower()}")


def _subscription_manager(connected: bool = True) -> MagicMock:
    manager = MagicMock()
    manager.subscribe.side_effect = [f"sub-{i}" for i in range(1, 10)]
    manager.refresh.return_value = True
    manager.dispatcher.is_connected = connected
    return manager


def _make_with_manager(dto, toolbox_api, sandbox_api, manager):
    from daytona._sync.sandbox import Sandbox

    return Sandbox(dto, toolbox_api, sandbox_api, "python", subscription_manager=manager, http_client=MagicMock())


class TestSandboxWaitScopedSubscriptions:
    def test_construction_and_method_calls_do_not_subscribe(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto()
        mock_sandbox_api.replace_labels.return_value = MagicMock(labels={"a": "b"})

        sandbox.refresh_data()
        _ = sandbox.set_labels({"a": "b"})

        manager.subscribe.assert_not_called()
        manager.refresh.assert_not_called()

    def test_cached_target_state_returns_without_subscribing(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api, manager
        )

        sandbox._wait_for_state([SandboxState.STARTED], [SandboxState.ERROR])

        manager.subscribe.assert_not_called()

    def test_wait_subscribes_to_state_events_and_unsubscribes_on_success(
        self, mock_toolbox_api_client, mock_sandbox_api
    ):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTED)

        sandbox.wait_for_sandbox_start(timeout=0)

        manager.subscribe.assert_called_once()
        assert manager.subscribe.call_args.args[0] == "test-sandbox-id"
        assert manager.subscribe.call_args.kwargs["events"] == ["sandbox.state.updated"]
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_wait_unsubscribes_on_error_state(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR)

        with pytest.raises(DaytonaError, match="entered error state"):
            sandbox.wait_for_sandbox_start(timeout=0)

        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_wait_unsubscribes_on_refresh_failure(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.side_effect = Exception("401 Unauthorized")

        with pytest.raises(Exception, match="401 Unauthorized"):
            sandbox.wait_for_sandbox_start(timeout=0)

        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_wait_unsubscribes_on_timeout(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTING)

        with pytest.raises(DaytonaTimeoutError):
            sandbox.wait_for_sandbox_start(timeout=0.2)

        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_state_event_resolves_wait(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTING)
        refreshes: list[int] = []

        def deliver_event() -> None:
            assert _wait_until(lambda: manager.subscribe.called and mock_sandbox_api.get_sandbox.called)
            refreshes.append(mock_sandbox_api.get_sandbox.call_count)
            handler = manager.subscribe.call_args.args[1]
            handler(
                "sandbox.state.updated",
                {
                    "sandbox": {"id": "test-sandbox-id", "state": "started"},
                    "oldState": "starting",
                    "newState": "started",
                },
            )

        sender = threading.Thread(target=deliver_event)
        sender.start()
        sandbox.wait_for_sandbox_start(timeout=5)
        sender.join()

        assert sandbox.state == SandboxState.STARTED
        assert mock_sandbox_api.get_sandbox.call_count == refreshes[0]
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_slim_state_event_only_applies_the_state(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_toolbox_api_client,
            mock_sandbox_api,
            _subscription_manager(),
        )

        sandbox._handle_event(
            "sandbox.state.updated",
            {
                "sandbox": {"id": "test-sandbox-id", "state": "error", "errorReason": "boom", "recoverable": True},
                "oldState": "starting",
                "newState": "error",
            },
        )

        assert sandbox.state == SandboxState.ERROR
        assert sandbox.error_reason is None
        assert sandbox.cpu == 4

    def test_concurrent_waits_hold_independent_subscriptions(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        monkeypatch.setattr(wait_poll, "EVENT_BACKED_POLL_INTERVAL", 0.01)
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTING)
        errors: list[BaseException] = []

        def wait() -> None:
            try:
                sandbox.wait_for_sandbox_start(timeout=5)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)

        waiters = [threading.Thread(target=wait) for _ in range(2)]
        for waiter in waiters:
            waiter.start()
        assert _wait_until(lambda: manager.subscribe.call_count == 2)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTED)
        for waiter in waiters:
            waiter.join(timeout=5)

        assert not errors
        assert sorted(c.args[0] for c in manager.unsubscribe.call_args_list) == ["sub-1", "sub-2"]

    def test_expired_subscription_is_renewed_during_a_long_wait(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        monkeypatch.setattr(wait_poll, "EVENT_BACKED_POLL_INTERVAL", 0.01)
        manager = _subscription_manager()
        manager.refresh.side_effect = lambda sub_id: sub_id != "sub-1"
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.side_effect = [make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [
            make_sandbox_dto()
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        assert manager.subscribe.call_count == 2
        assert [c.args[0] for c in manager.refresh.call_args_list] == ["sub-1", "sub-2", "sub-2"]
        manager.unsubscribe.assert_called_once_with("sub-2")

    def test_poll_cadence_follows_the_event_connection(self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)
        manager = _subscription_manager(connected=False)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        refreshes = 0

        def get_sandbox(*_args, **_kwargs):
            nonlocal refreshes
            refreshes += 1
            if refreshes == 3:
                manager.dispatcher.is_connected = True  # the connection comes up mid-wait
            if refreshes == 5:
                manager.dispatcher.is_connected = False  # and drops again
            return make_sandbox_dto(state=SandboxState.STARTED if refreshes == 6 else SandboxState.STARTING)

        mock_sandbox_api.get_sandbox.side_effect = get_sandbox

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == pytest.approx([1.0, 1.0, 5.0, 5.0, 1.0])

    @pytest.mark.parametrize(("random_value", "expected_interval"), [(0.0, 4.0), (1.0, 6.0)])
    def test_event_backed_wait_polls_again_only_after_four_to_six_seconds(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, random_value, expected_interval
    ):
        poll_intervals = _record_poll_intervals(monkeypatch, mock_sandbox_api)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: random_value)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_toolbox_api_client,
            mock_sandbox_api,
            _subscription_manager(connected=True),
        )
        mock_sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.STARTING),
            make_sandbox_dto(state=SandboxState.STARTED),
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        # One immediate poll, then nothing until the jittered 5 s interval elapses.
        assert poll_intervals == [(pytest.approx(expected_interval), 1)]
        assert mock_sandbox_api.get_sandbox.call_count == 2

    @pytest.mark.parametrize(("random_value", "expected_interval"), [(0.0, 0.8), (1.0, 1.2)])
    def test_subscribed_wait_without_connection_polls_about_every_second(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, random_value, expected_interval
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: random_value)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_toolbox_api_client,
            mock_sandbox_api,
            _subscription_manager(connected=False),
        )
        mock_sandbox_api.get_sandbox.side_effect = [make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [
            make_sandbox_dto()
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == pytest.approx([expected_interval] * 3)

    def test_unsubscribed_wait_keeps_the_fast_cadence(self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 1.0)
        manager = _subscription_manager(connected=True)
        manager.subscribe.side_effect = None
        manager.subscribe.return_value = ""
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        mock_sandbox_api.get_sandbox.side_effect = [make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [
            make_sandbox_dto()
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == [wait_poll.FAST_POLL_INTERVAL] * 3


def _record_poll_intervals(monkeypatch, sandbox_api: MagicMock | None = None) -> list[tuple[float, int]]:
    """Makes the wait loop record (poll interval, refreshes so far) instead of sleeping."""
    from daytona._sync import sandbox as sandbox_module

    poll_intervals: list[tuple[float, int]] = []

    class RecordingEvent(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            if timeout is not None:
                refreshes = sandbox_api.get_sandbox.call_count if sandbox_api is not None else -1
                poll_intervals.append((timeout, refreshes))
            return super().wait(0)

    class RecordingThreading:
        """Proxy for the sandbox module's ``threading`` that records poll intervals instead of sleeping."""

        Event = RecordingEvent

        def __getattr__(self, name: str):
            return getattr(threading, name)

    monkeypatch.setattr(sandbox_module, "threading", RecordingThreading())
    return poll_intervals


class TestSandboxOperationResponses:
    def test_stop_applies_the_returned_sandbox_without_a_get(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=0)

        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STOPPED

    def test_stop_without_a_returned_sandbox_refreshes_once(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.stop_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=0)

        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.STOPPED

    def test_stop_without_a_returned_sandbox_keeps_the_safe_refresh(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.stop_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.side_effect = [
            make_pydantic_validation_error(),
            make_sandbox_dto(state=SandboxState.STOPPED),
        ]

        sandbox.stop(timeout=0)

        assert mock_sandbox_api.get_sandbox.call_count == 2
        assert sandbox.state == SandboxState.STOPPED

    def test_pause_applies_the_returned_sandbox_without_a_get(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.pause_sandbox.return_value = make_sandbox_dto(state=SandboxState.PAUSED)

        sandbox.pause(timeout=0)

        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.PAUSED

    def test_pause_without_a_returned_sandbox_refreshes_once(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.pause_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.PAUSED)

        sandbox.pause(timeout=0)

        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.PAUSED

    def test_archive_applies_the_returned_sandbox_without_a_get(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STOPPED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.archive_sandbox.return_value = make_sandbox_dto(state=SandboxState.ARCHIVING)

        sandbox.archive()

        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.ARCHIVING

    def test_archive_without_a_returned_sandbox_refreshes_once(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STOPPED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.archive_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.ARCHIVING)

        sandbox.archive(request_timeout=30)

        mock_sandbox_api.get_sandbox.assert_called_once_with("test-sandbox-id", _request_timeout=30.0)
        assert sandbox.state == SandboxState.ARCHIVING


def _wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _state_event(state: str) -> dict[str, object]:
    return {"sandbox": {"id": "test-sandbox-id", "state": state}, "newState": state}


def _deliver_once_waiting(manager: MagicMock, state: str) -> threading.Thread:
    """Sends a state event from another thread, as the dispatcher does, once the wait polls."""

    def deliver() -> None:
        if _wait_until(lambda: manager.refresh.called):
            manager.subscribe.call_args.args[1]("sandbox.state.updated", _state_event(state))

    sender = threading.Thread(target=deliver)
    sender.start()
    return sender


class TestSandboxSubscribeBeforeRequest:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    def _sandbox(self, toolbox_api, sandbox_api, manager, state=SandboxState.STARTED):
        sandbox = _make_with_manager(make_sandbox_dto(state=state), toolbox_api, sandbox_api, manager)
        sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=state)
        return sandbox

    @staticmethod
    def _respond(manager: MagicMock, state: SandboxState, subscribed_before: list[bool]):
        def respond(*_args, **_kwargs):
            subscribed_before.append(manager.subscribe.called)
            return make_sandbox_dto(state=state)

        return respond

    def test_stop_with_live_events_makes_no_get(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.stop_sandbox.side_effect = self._respond(manager, SandboxState.STOPPING, subscribed_before)

        sender = _deliver_once_waiting(manager, "stopped")
        sandbox.stop(timeout=5)
        sender.join(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STOPPED
        manager.subscribe.assert_called_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_stop_with_events_down_reads_once(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=False)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.stop_sandbox.side_effect = self._respond(manager, SandboxState.STOPPING, subscribed_before)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_called_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_state_event_during_the_request_forces_the_first_read(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)

        def stop_sandbox(*_args, **_kwargs):
            # The event overtakes the response (on the dispatcher's thread), which then carries the older state.
            sender = threading.Thread(
                target=manager.subscribe.call_args.args[1], args=("sandbox.state.updated", _state_event("stopped"))
            )
            sender.start()
            sender.join()
            return make_sandbox_dto(state=SandboxState.STOPPING)

        mock_sandbox_api.stop_sandbox.side_effect = stop_sandbox
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=5)

        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.STOPPED

    def test_failed_stop_request_releases_the_subscription(self, mock_toolbox_api_client, mock_sandbox_api):
        from daytona.internal.event_subscription_manager import SyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        manager = SyncEventSubscriptionManager(dispatcher)  # pyright: ignore[reportArgumentType]
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        listeners_during_request: list[int] = []

        def stop_sandbox(*_args, **_kwargs):
            listeners_during_request.append(len(dispatcher.listeners.get("test-sandbox-id", [])))
            raise RuntimeError("stop rejected")

        mock_sandbox_api.stop_sandbox.side_effect = stop_sandbox

        try:
            with pytest.raises(DaytonaError, match="stop rejected") as exc_info:
                sandbox.stop(timeout=5)
        finally:
            manager.shutdown()

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert listeners_during_request == [1]
        assert dispatcher.sent == [("subscribe", "test-sandbox-id"), ("unsubscribe", "test-sandbox-id")]
        mock_sandbox_api.get_sandbox.assert_not_called()

    def test_failed_stop_request_keeps_other_listeners(self, mock_toolbox_api_client, mock_sandbox_api):
        from daytona.internal.event_subscription_manager import SyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        manager = SyncEventSubscriptionManager(dispatcher)  # pyright: ignore[reportArgumentType]
        _ = manager.subscribe("test-sandbox-id", lambda *_: None, events=["sandbox.state.updated"])
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.side_effect = RuntimeError("stop rejected")

        try:
            with pytest.raises(DaytonaError, match="stop rejected"):
                sandbox.stop(timeout=5)
            assert len(dispatcher.listeners["test-sandbox-id"]) == 1
            assert dispatcher.sent == [("subscribe", "test-sandbox-id")]
        finally:
            manager.shutdown()

    def test_stop_response_in_the_target_state_releases_the_subscription(
        self, mock_toolbox_api_client, mock_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.stop_sandbox.side_effect = self._respond(manager, SandboxState.STOPPED, subscribed_before)

        sandbox.stop(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_not_called()
        manager.refresh.assert_not_called()
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_delete_and_wait_with_live_events_makes_no_get(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.delete_sandbox.side_effect = self._respond(manager, SandboxState.DESTROYING, subscribed_before)

        sender = _deliver_once_waiting(manager, "destroyed")
        sandbox.delete(timeout=5, wait=True)
        sender.join(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.DESTROYED
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_delete_without_wait_takes_no_subscription(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.delete_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYING)

        sandbox.delete(timeout=5)

        manager.subscribe.assert_not_called()
        mock_sandbox_api.get_sandbox.assert_not_called()

    def test_pause_with_live_events_makes_no_get(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.pause_sandbox.side_effect = self._respond(manager, SandboxState.PAUSING, subscribed_before)

        sender = _deliver_once_waiting(manager, "paused")
        sandbox.pause(timeout=5)
        sender.join(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.PAUSED
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_resize_with_live_events_makes_no_get(self, mock_toolbox_api_client, mock_sandbox_api):
        from daytona.common.sandbox import Resources

        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_sandbox_api.resize_sandbox.side_effect = self._respond(manager, SandboxState.RESIZING, subscribed_before)

        sender = _deliver_once_waiting(manager, "started")
        sandbox.resize(Resources(cpu=2), timeout=5)
        sender.join(timeout=5)

        assert subscribed_before == [True]
        mock_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STARTED
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_stop_error_response_reads_first_and_fails_at_once(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR)

        with pytest.raises(DaytonaError, match="entered error state"):
            sandbox.stop(timeout=5)

        mock_sandbox_api.get_sandbox.assert_called_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @staticmethod
    def _fork_dto(state: SandboxState):
        return make_sandbox_dto(sandbox_id="forked-id", state=state, toolbox_proxy_url="http://forked:2280")

    def test_live_fork_makes_no_get(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.fork_sandbox.return_value = self._fork_dto(SandboxState.FORKING)

        sender = _deliver_once_waiting(manager, "started")
        forked = sandbox.fork(timeout=5)
        sender.join(timeout=5)

        assert forked.state == SandboxState.STARTED
        assert manager.subscribe.call_args.args[0] == "forked-id"
        mock_sandbox_api.get_sandbox.assert_not_called()
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_fork_without_live_events_reads_once(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=False)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.fork_sandbox.return_value = self._fork_dto(SandboxState.FORKING)
        mock_sandbox_api.get_sandbox.return_value = self._fork_dto(SandboxState.STARTED)

        forked = sandbox.fork(timeout=5)

        assert forked.state == SandboxState.STARTED
        mock_sandbox_api.get_sandbox.assert_called_once_with("forked-id", _request_timeout=None)
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_started_fork_takes_no_wait(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.fork_sandbox.return_value = self._fork_dto(SandboxState.STARTED)

        forked = sandbox.fork(timeout=5)

        assert forked.state == SandboxState.STARTED
        manager.subscribe.assert_not_called()
        mock_sandbox_api.get_sandbox.assert_not_called()

    def test_public_wait_keeps_its_first_read(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager, SandboxState.STOPPING)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.wait_for_sandbox_stop(timeout=5)

        mock_sandbox_api.get_sandbox.assert_called_once()

    def test_state_events_from_many_threads_are_all_counted(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, _subscription_manager())
        senders = [
            threading.Thread(
                target=lambda: [
                    sandbox._handle_event("sandbox.state.updated", _state_event("started")) for _ in range(500)
                ]
            )
            for _ in range(8)
        ]
        for sender in senders:
            sender.start()
        for sender in senders:
            sender.join()

        assert sandbox._read_state_event_count() == 4000
