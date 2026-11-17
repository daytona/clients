# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.common.errors import (
    DaytonaError,
    DaytonaQueueTimeoutError,
    DaytonaSpotEvictedError,
    DaytonaTimeoutError,
    DaytonaValidationError,
)
from daytona.internal import wait_poll
from daytona_api_client import SandboxState
from daytona_api_client_async import Sandbox as AsyncSandboxDto
from daytona_api_client_async import UpdateSandboxSecrets

from .conftest import FakeEventDispatcher, make_pydantic_validation_error, make_sandbox_dto


def _polling_only_manager() -> MagicMock:
    """A manager without event streaming: waits take no subscription and poll at the fast cadence."""
    manager = MagicMock()
    manager.subscribe.return_value = ""
    manager.dispatcher = None
    manager.is_event_backed.return_value = False
    return manager


def make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
    from daytona._async.sandbox import AsyncSandbox

    return AsyncSandbox(
        sandbox_dto,
        mock_async_toolbox_api_client,
        mock_async_sandbox_api,
        "python",
        subscription_manager=_polling_only_manager(),
    )


class TestAsyncSandboxInit:
    def test_sandbox_properties(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.id == "test-sandbox-id"
        assert sandbox.name == "test-sandbox"
        assert sandbox.state == SandboxState.STARTED
        assert sandbox.cpu == 4

    def test_sandbox_has_subsystems(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.fs is not None
        assert sandbox.git is not None
        assert sandbox.process is not None
        assert sandbox.computer_use is not None
        assert sandbox.code_interpreter is not None

    def test_process_dto_preserves_proxy_url_when_missing(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(toolbox_proxy_url="http://hydrated:2280")
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        update_dto = make_sandbox_dto(toolbox_proxy_url="")
        sandbox._AsyncSandbox__process_sandbox_dto(update_dto)
        assert sandbox.toolbox_proxy_url == "http://hydrated:2280"

    def test_kvm_hydrated_from_dto(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = AsyncSandboxDto(**make_sandbox_dto(kvm=True).model_dump())
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.kvm is True

    def test_kvm_default_false_from_dto(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = AsyncSandboxDto(**make_sandbox_dto().model_dump())
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.kvm is False


class TestAsyncSandboxLifecycleSettings:
    @pytest.mark.asyncio
    async def test_negative_autostop_interval_raises(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-stop interval must be a non-negative"):
            await sandbox.set_autostop_interval(-1)

    @pytest.mark.asyncio
    async def test_valid_autostop_interval(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.set_autostop_interval = AsyncMock(return_value=None)
        await sandbox.set_autostop_interval(30)
        assert sandbox.auto_stop_interval == 30

    @pytest.mark.asyncio
    async def test_negative_autopause_interval_raises(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-pause interval must be a non-negative"):
            await sandbox.set_auto_pause_interval(-1)

    @pytest.mark.asyncio
    async def test_valid_autopause_interval(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.set_auto_pause_interval = AsyncMock(return_value=None)
        await sandbox.set_auto_pause_interval(30)
        assert sandbox.auto_pause_interval == 30

    @pytest.mark.asyncio
    async def test_negative_auto_archive_interval_raises(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="Auto-archive interval must be a non-negative"):
            await sandbox.set_auto_archive_interval(-1)

    @pytest.mark.asyncio
    async def test_valid_auto_archive_interval(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.set_auto_archive_interval = AsyncMock(return_value=None)
        await sandbox.set_auto_archive_interval(60)
        assert sandbox.auto_archive_interval == 60

    @pytest.mark.asyncio
    async def test_auto_delete_interval(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.set_auto_delete_interval = AsyncMock(return_value=None)
        await sandbox.set_auto_delete_interval(120)
        assert sandbox.auto_delete_interval == 120

    @pytest.mark.asyncio
    async def test_negative_ttl_raises(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        with pytest.raises(DaytonaValidationError, match="TTL must be a non-negative"):
            await sandbox.set_ttl(-1)

    @pytest.mark.asyncio
    async def test_valid_ttl_calls_api(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.set_ttl = AsyncMock(return_value=None)
        await sandbox.set_ttl(60)
        mock_async_sandbox_api.set_ttl.assert_called_once_with(sandbox.id, 60, _request_timeout=None)


class TestAsyncSandboxOperations:
    @pytest.mark.asyncio
    async def test_set_labels(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        new_labels = {"project": "test", "env": "dev"}
        mock_async_sandbox_api.replace_labels = AsyncMock(return_value=MagicMock(labels=new_labels))
        result = await sandbox.set_labels(new_labels)
        assert result == new_labels

    def test_create_lsp_server(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        from daytona._async.lsp_server import AsyncLspServer

        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        lsp = sandbox.create_lsp_server("python", "/workspace/project")
        assert isinstance(lsp, AsyncLspServer)

    @pytest.mark.asyncio
    async def test_refresh_data(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STOPPED, cpu=8))
        await sandbox.refresh_data()
        assert sandbox.state == SandboxState.STOPPED
        assert sandbox.cpu == 8

    @pytest.mark.asyncio
    async def test_get_user_home_dir(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        sandbox._info_api = AsyncMock(get_user_home_dir=AsyncMock(return_value=MagicMock(dir="/home/daytona")))
        assert await sandbox.get_user_home_dir() == "/home/daytona"

    @pytest.mark.asyncio
    async def test_get_work_dir(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        sandbox._info_api = AsyncMock(get_work_dir=AsyncMock(return_value=MagicMock(dir="/workspace")))
        assert await sandbox.get_work_dir() == "/workspace"

    @pytest.mark.asyncio
    async def test_get_user_root_dir_delegates_to_home_dir(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        sandbox._info_api = AsyncMock(get_user_home_dir=AsyncMock(return_value=MagicMock(dir="/home/daytona")))

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert await sandbox.get_user_root_dir() == "/home/daytona"

    @pytest.mark.asyncio
    async def test_preview_and_ssh_operations_delegate(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_port_preview_url = AsyncMock(return_value=MagicMock(url="https://preview"))
        mock_async_sandbox_api.create_ssh_access = AsyncMock(return_value=MagicMock(token="ssh-token"))
        mock_async_sandbox_api.validate_ssh_access = AsyncMock(return_value=MagicMock(valid=True))
        mock_async_sandbox_api.revoke_ssh_access = AsyncMock()
        mock_async_sandbox_api.update_last_activity = AsyncMock()

        assert (await sandbox.get_preview_link(3000)).url == "https://preview"
        assert (await sandbox.create_ssh_access(10)).token == "ssh-token"
        assert (await sandbox.validate_ssh_access("token")).valid is True
        await sandbox.revoke_ssh_access("token")
        await sandbox.refresh_activity()

        mock_async_sandbox_api.get_port_preview_url.assert_awaited_once_with(sandbox.id, 3000, _request_timeout=None)
        mock_async_sandbox_api.revoke_ssh_access.assert_awaited_once_with(sandbox.id, "token", _request_timeout=None)
        mock_async_sandbox_api.update_last_activity.assert_awaited_once_with(sandbox.id, _request_timeout=None)

    @pytest.mark.asyncio
    async def test_update_secrets(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        updated_dto = AsyncSandboxDto(**make_sandbox_dto(env={"FOO": "placeholder"}).model_dump())
        mock_async_sandbox_api.update_sandbox_secrets = AsyncMock(return_value=updated_dto)

        await sandbox.update_secrets({"FOO": "foo-secret", "BAR": "bar"})

        mock_async_sandbox_api.update_sandbox_secrets.assert_awaited_once()
        sandbox_id, body = mock_async_sandbox_api.update_sandbox_secrets.call_args.args
        assert sandbox_id == sandbox.id
        assert isinstance(body, UpdateSandboxSecrets)
        assert body.secrets == [{"FOO": "foo-secret"}, {"BAR": "bar"}]
        assert sandbox.env == {"FOO": "placeholder"}

    @pytest.mark.asyncio
    async def test_update_secrets_empty_dict_detaches_all(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        updated_dto = AsyncSandboxDto(**make_sandbox_dto(env={}).model_dump())
        mock_async_sandbox_api.update_sandbox_secrets = AsyncMock(return_value=updated_dto)

        await sandbox.update_secrets({})

        mock_async_sandbox_api.update_sandbox_secrets.assert_awaited_once()
        _, body = mock_async_sandbox_api.update_sandbox_secrets.call_args.args
        assert body.secrets == []

    @pytest.mark.asyncio
    async def test_update_env(self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        # The daemon responds with a status message, which update_env discards.
        sandbox._server_api = AsyncMock(
            update_env=AsyncMock(return_value={"message": "Environment updated successfully"})
        )

        result = await sandbox.update_env({"A": "1"}, unset=["B"])

        assert result is None
        sandbox._server_api.update_env.assert_awaited_once()
        request = sandbox._server_api.update_env.call_args.kwargs["request"]
        assert request.set == {"A": "1"}
        assert request.unset == ["B"]
        assert request.unset_value_prefix is None


class TestAsyncSandboxWaitForStart:
    @pytest.mark.asyncio
    async def test_error_state_raises(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="build failed")
        sandbox = make_async_sandbox(error_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=error_dto)
        with pytest.raises(DaytonaError, match="Failure during waiting for sandbox to start"):
            await sandbox.wait_for_sandbox_start(timeout=0)


class TestAsyncSandboxQueueTimeout:
    def test_queue_timeout_fields_hydrated_from_dto(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = AsyncSandboxDto(
            **make_sandbox_dto(queue_timeout=5, queue_timed_out_at="2026-08-13T12:00:00.000Z").model_dump()
        )
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.queue_timeout == 5
        assert sandbox.queue_timed_out_at == "2026-08-13T12:00:00.000Z"

    def test_queue_timeout_fields_default_none(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = AsyncSandboxDto(**make_sandbox_dto().model_dump())
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        assert sandbox.queue_timeout is None
        assert sandbox.queue_timed_out_at is None

    @pytest.mark.asyncio
    async def test_wait_for_start_raises_queue_timeout_error(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.PENDING_BUILD), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=make_sandbox_dto(
                state=SandboxState.DESTROYED, queue_timeout=1, queue_timed_out_at="2026-08-13T12:00:00.000Z"
            )
        )
        with pytest.raises(DaytonaQueueTimeoutError, match="waiting 1 minute for a runner") as exc_info:
            await sandbox.wait_for_sandbox_start(timeout=0)
        assert isinstance(exc_info.value, DaytonaTimeoutError)

    @pytest.mark.asyncio
    async def test_queue_timeout_message_omits_duration_when_unknown(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.DESTROYED, queue_timed_out_at="2026-08-13T12:00:00Z")
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        with pytest.raises(
            DaytonaQueueTimeoutError, match=r"was destroyed after waiting for a runner \(queue timed out at"
        ):
            await sandbox.wait_for_sandbox_start(timeout=0)

    @pytest.mark.asyncio
    async def test_wait_for_start_raises_spot_evicted_error(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTING), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=make_sandbox_dto(state=SandboxState.DESTROYED, spot_evicted_at="2026-08-13T12:00:00.000Z")
        )
        with pytest.raises(DaytonaSpotEvictedError, match="evicted by spot preemption"):
            await sandbox.wait_for_sandbox_start(timeout=0)

    @pytest.mark.asyncio
    async def test_cached_queue_timed_out_state_raises_immediately(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.DESTROYED, queue_timeout=2, queue_timed_out_at="2026-08-13T12:00:00Z")
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock()
        with pytest.raises(DaytonaQueueTimeoutError):
            await sandbox.wait_for_sandbox_start(timeout=0)
        mock_async_sandbox_api.get_sandbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_wait_succeeds_for_queue_timed_out_sandbox(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.PENDING_BUILD), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.DESTROYING))
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=make_sandbox_dto(
                state=SandboxState.DESTROYED, queue_timeout=1, queue_timed_out_at="2026-08-13T12:00:00.000Z"
            )
        )
        await sandbox.delete(timeout=0, wait=True)
        assert sandbox.state == SandboxState.DESTROYED


class TestAsyncSandboxPollingSemantics:
    @pytest.mark.asyncio
    async def test_cached_error_recovers_after_refresh(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="transient")
        sandbox = make_async_sandbox(error_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTED))
        await sandbox.wait_for_sandbox_start(timeout=0)
        assert sandbox.state == SandboxState.STARTED

    @pytest.mark.asyncio
    async def test_validation_error_tolerated_during_stop_wait(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[
                make_pydantic_validation_error(),
                make_sandbox_dto(state=SandboxState.STOPPED),
            ]
        )
        await sandbox.wait_for_sandbox_stop(timeout=0)
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_non_validation_error_aborts_stop_wait(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(side_effect=Exception("401 Unauthorized"))
        with pytest.raises(Exception, match="401 Unauthorized"):
            await sandbox.wait_for_sandbox_stop(timeout=0)

    @pytest.mark.asyncio
    async def test_error_only_mentioning_validation_in_message_aborts_stop_wait(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=Exception("500 upstream failure: validation error in body")
        )
        with pytest.raises(Exception, match="500 upstream failure"):
            await sandbox.wait_for_sandbox_stop(timeout=0)

    @pytest.mark.asyncio
    async def test_cancellation_propagates_during_wait(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STOPPING))
        task = asyncio.create_task(sandbox.wait_for_sandbox_stop(timeout=0))
        await asyncio.sleep(0)  # let task start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_delete_none_response_does_not_raise(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=None)
        await sandbox.delete(timeout=0)
        assert sandbox.state == SandboxState.STARTED

    @pytest.mark.asyncio
    async def test_delete_default_does_not_wait(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.DESTROYING))
        mock_async_sandbox_api.get_sandbox = AsyncMock()
        await sandbox.delete(timeout=0)
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.DESTROYING

    @pytest.mark.asyncio
    async def test_delete_wait_true_blocks_until_destroyed(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.DESTROYING))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.DESTROYED))
        await sandbox.delete(timeout=0, wait=True)
        mock_async_sandbox_api.get_sandbox.assert_called()
        assert sandbox.state == SandboxState.DESTROYED

    @pytest.mark.asyncio
    async def test_pause_resolves_on_stopped(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        mock_async_sandbox_api.pause_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[
                make_sandbox_dto(state=SandboxState.PAUSING),
                make_sandbox_dto(state=SandboxState.STOPPED),
            ]
        )
        await sandbox.pause(timeout=0)
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_fork_hydrates_proxy_url_when_missing(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED, toolbox_proxy_url="http://original:2280")
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)

        fork_dto = make_sandbox_dto(
            sandbox_id="forked-id",
            state=SandboxState.STARTED,
            toolbox_proxy_url="",
        )
        proxy_result = MagicMock()
        proxy_result.url = "http://hydrated-fork:2280"
        mock_async_sandbox_api.fork_sandbox = AsyncMock(return_value=fork_dto)
        mock_async_sandbox_api.get_toolbox_proxy_url = AsyncMock(return_value=proxy_result)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=make_sandbox_dto(
                sandbox_id="forked-id", state=SandboxState.STARTED, toolbox_proxy_url="http://hydrated-fork:2280"
            )
        )

        forked = await sandbox.fork(name="my-fork", timeout=0)
        mock_async_sandbox_api.get_toolbox_proxy_url.assert_called_once_with("forked-id")
        assert forked.toolbox_proxy_url == "http://hydrated-fork:2280"

    @pytest.mark.asyncio
    async def test_experimental_fork_delegates_and_warns(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(state=SandboxState.STARTED, toolbox_proxy_url="http://original:2280")
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)

        fork_dto = make_sandbox_dto(
            sandbox_id="forked-id",
            state=SandboxState.STARTED,
            toolbox_proxy_url="http://forked:2280",
        )
        mock_async_sandbox_api.fork_sandbox = AsyncMock(return_value=fork_dto)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=make_sandbox_dto(
                sandbox_id="forked-id", state=SandboxState.STARTED, toolbox_proxy_url="http://forked:2280"
            )
        )

        with pytest.warns(DeprecationWarning, match="Use `fork` instead"):
            forked = await sandbox._experimental_fork(name="my-fork", timeout=0)
        assert forked.id == "forked-id"

    @pytest.mark.asyncio
    async def test_experimental_create_snapshot_delegates_and_warns(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(state=SandboxState.STARTED)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)

        snapshot_dto = make_sandbox_dto(state=SandboxState.STARTED)
        mock_async_sandbox_api.create_sandbox_snapshot = AsyncMock(return_value=snapshot_dto)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTED))

        with pytest.warns(DeprecationWarning, match="Use `create_snapshot` instead"):
            await sandbox._experimental_create_snapshot("my-snapshot", timeout=0)


def _subscription_manager(connected: bool = True) -> MagicMock:
    manager = MagicMock()
    manager.subscribe.side_effect = [f"sub-{i}" for i in range(1, 10)]
    manager.refresh.return_value = True
    manager.dispatcher.is_connected = connected
    manager.connection_epoch = 1
    # Every id is served: event-backed exactly while connected.
    manager.is_event_backed.side_effect = lambda _resource_id: manager.dispatcher.is_connected
    return manager


def _make_with_manager(dto, toolbox_api, sandbox_api, manager):
    from daytona._async.sandbox import AsyncSandbox

    return AsyncSandbox(dto, toolbox_api, sandbox_api, "python", subscription_manager=manager)


class TestAsyncSandboxWaitScopedSubscriptions:
    @pytest.mark.asyncio
    async def test_construction_and_method_calls_do_not_subscribe(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager()
        sandbox = _make_with_manager(make_sandbox_dto(), mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto())
        mock_async_sandbox_api.replace_labels = AsyncMock(return_value=MagicMock(labels={"a": "b"}))

        await sandbox.refresh_data()
        _ = await sandbox.set_labels({"a": "b"})

        manager.subscribe.assert_not_called()
        manager.refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_target_state_returns_without_subscribing(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api, manager
        )

        await sandbox._wait_for_state([SandboxState.STARTED], [SandboxState.ERROR])

        manager.subscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_subscribes_to_state_events_and_unsubscribes_on_success(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTED))

        await sandbox.wait_for_sandbox_start(timeout=0)

        manager.subscribe.assert_called_once()
        assert manager.subscribe.call_args.args[0] == "test-sandbox-id"
        assert manager.subscribe.call_args.kwargs["events"] == ["sandbox.state.updated"]
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_wait_unsubscribes_on_error_state(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.ERROR))

        with pytest.raises(DaytonaError, match="entered error state"):
            await sandbox.wait_for_sandbox_start(timeout=0)

        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_wait_unsubscribes_on_refresh_failure(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(side_effect=Exception("401 Unauthorized"))

        with pytest.raises(Exception, match="401 Unauthorized"):
            await sandbox.wait_for_sandbox_start(timeout=0)

        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_wait_unsubscribes_on_timeout(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTING))

        with pytest.raises(DaytonaTimeoutError):
            await sandbox.wait_for_sandbox_start(timeout=0.2)

        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_wait_unsubscribes_on_cancellation(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTING))
        task = asyncio.create_task(sandbox.wait_for_sandbox_start(timeout=0))
        await asyncio.sleep(0.05)
        manager.subscribe.assert_called_once()

        _ = task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_state_event_resolves_wait(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=make_sandbox_dto(state=SandboxState.STARTING))
        task = asyncio.create_task(sandbox.wait_for_sandbox_start(timeout=0))
        await asyncio.sleep(0.05)
        handler = manager.subscribe.call_args.args[1]
        refreshes = mock_async_sandbox_api.get_sandbox.await_count

        handler(
            "sandbox.state.updated",
            {"sandbox": {"id": "test-sandbox-id", "state": "started"}, "oldState": "starting", "newState": "started"},
        )
        await asyncio.wait_for(task, timeout=0.5)

        assert sandbox.state == SandboxState.STARTED
        assert mock_async_sandbox_api.get_sandbox.await_count == refreshes
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_slim_state_event_only_applies_the_state(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
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

    @pytest.mark.asyncio
    async def test_concurrent_waits_hold_independent_subscriptions(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch
    ):
        monkeypatch.setattr(wait_poll, "EVENT_BACKED_POLL_INTERVAL", 0.01)
        manager = _subscription_manager()
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        states = iter([SandboxState.STARTING, SandboxState.STARTING])
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=lambda *_a, **_k: make_sandbox_dto(state=next(states, SandboxState.STARTED))
        )

        await asyncio.gather(sandbox.wait_for_sandbox_start(timeout=0), sandbox.wait_for_sandbox_start(timeout=0))

        assert manager.subscribe.call_count == 2
        assert sorted(c.args[0] for c in manager.unsubscribe.call_args_list) == ["sub-1", "sub-2"]

    @pytest.mark.asyncio
    async def test_expired_subscription_is_renewed_during_a_long_wait(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch
    ):
        monkeypatch.setattr(wait_poll, "EVENT_BACKED_POLL_INTERVAL", 0.01)
        manager = _subscription_manager()
        manager.refresh.side_effect = lambda sub_id: sub_id != "sub-1"
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [make_sandbox_dto()]
        )

        await sandbox.wait_for_sandbox_start(timeout=0)

        assert manager.subscribe.call_count == 2
        assert [c.args[0] for c in manager.refresh.call_args_list] == ["sub-1", "sub-2", "sub-2"]
        manager.unsubscribe.assert_called_once_with("sub-2")

    @pytest.mark.asyncio
    async def test_poll_cadence_follows_the_event_connection(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)
        manager = _subscription_manager(connected=False)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        refreshes = 0

        async def get_sandbox(*_args, **_kwargs):
            nonlocal refreshes
            refreshes += 1
            if refreshes == 3:
                manager.dispatcher.is_connected = True  # the connection comes up mid-wait
            if refreshes == 5:
                manager.dispatcher.is_connected = False  # and drops again
            return make_sandbox_dto(state=SandboxState.STARTED if refreshes == 6 else SandboxState.STARTING)

        mock_async_sandbox_api.get_sandbox = get_sandbox

        await sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == pytest.approx([1.0, 1.0, 5.0, 5.0, 1.0])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("random_value", "expected_interval"), [(0.0, 4.0), (1.0, 6.0)])
    async def test_event_backed_wait_polls_again_only_after_four_to_six_seconds(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch, random_value, expected_interval
    ):
        poll_intervals = _record_poll_intervals(monkeypatch, mock_async_sandbox_api)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: random_value)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            _subscription_manager(connected=True),
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_sandbox_dto(state=SandboxState.STARTING), make_sandbox_dto(state=SandboxState.STARTED)]
        )

        await sandbox.wait_for_sandbox_start(timeout=0)

        # One immediate poll, then nothing until the jittered 5 s interval elapses.
        assert poll_intervals == [(pytest.approx(expected_interval), 1)]
        assert mock_async_sandbox_api.get_sandbox.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("random_value", "expected_interval"), [(0.0, 0.8), (1.0, 1.2)])
    async def test_subscribed_wait_without_connection_polls_about_every_second(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch, random_value, expected_interval
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: random_value)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            _subscription_manager(connected=False),
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [make_sandbox_dto()]
        )

        await sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == pytest.approx([expected_interval] * 3)

    @pytest.mark.asyncio
    async def test_unsubscribed_wait_keeps_the_fast_cadence(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 1.0)
        manager = _subscription_manager(connected=True)
        manager.subscribe.side_effect = None
        manager.subscribe.return_value = ""
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_sandbox_dto(state=SandboxState.STARTING)] * 3 + [make_sandbox_dto()]
        )

        await sandbox.wait_for_sandbox_start(timeout=0)

        assert [interval for interval, _ in poll_intervals] == [wait_poll.FAST_POLL_INTERVAL] * 3


def _record_poll_intervals(monkeypatch, sandbox_api: AsyncMock | None = None) -> list[tuple[float, int]]:
    """Makes the wait loop record (poll interval, refreshes so far) instead of sleeping."""
    from daytona._async import sandbox as sandbox_module

    poll_intervals: list[tuple[float, int]] = []
    real_wait_for = asyncio.wait_for

    class RecordingAsyncio:
        """Proxy for the sandbox module's ``asyncio`` that records poll intervals instead of sleeping."""

        def __getattr__(self, name: str):
            return getattr(asyncio, name)

        @staticmethod
        async def wait_for(awaitable, timeout):
            refreshes = sandbox_api.get_sandbox.await_count if sandbox_api is not None else -1
            poll_intervals.append((timeout, refreshes))
            return await real_wait_for(awaitable, timeout=0)

    monkeypatch.setattr(sandbox_module, "asyncio", RecordingAsyncio())
    return poll_intervals


class TestAsyncSandboxOperationResponses:
    @pytest.mark.asyncio
    async def test_stop_applies_the_returned_sandbox_without_a_get(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))
        mock_async_sandbox_api.get_sandbox = AsyncMock()

        await sandbox.stop(timeout=0)

        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_without_a_returned_sandbox_refreshes_once(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))

        await sandbox.stop(timeout=0)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_without_a_returned_sandbox_keeps_the_safe_refresh(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_pydantic_validation_error(), _async_dto(state=SandboxState.STOPPED)]
        )

        await sandbox.stop(timeout=0)

        assert mock_async_sandbox_api.get_sandbox.await_count == 2
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_pause_applies_the_returned_sandbox_without_a_get(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.pause_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.PAUSED))
        mock_async_sandbox_api.get_sandbox = AsyncMock()

        await sandbox.pause(timeout=0)

        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_without_a_returned_sandbox_refreshes_once(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STARTED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.pause_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.PAUSED))

        await sandbox.pause(timeout=0)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.PAUSED

    @pytest.mark.asyncio
    async def test_archive_applies_the_returned_sandbox_without_a_get(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STOPPED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.archive_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.ARCHIVING))
        mock_async_sandbox_api.get_sandbox = AsyncMock()

        await sandbox.archive()

        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.ARCHIVING

    @pytest.mark.asyncio
    async def test_archive_without_a_returned_sandbox_refreshes_once(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STOPPED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.archive_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.ARCHIVING))

        await sandbox.archive(request_timeout=30)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once_with("test-sandbox-id", _request_timeout=30.0)
        assert sandbox.state == SandboxState.ARCHIVING


def _async_dto(**kwargs) -> AsyncSandboxDto:
    return AsyncSandboxDto(**make_sandbox_dto(**kwargs).model_dump())


def _state_event(state: str) -> dict[str, object]:
    return {"sandbox": {"id": "test-sandbox-id", "state": state}, "newState": state}


async def _deliver_once_waiting(manager: MagicMock, state: str) -> None:
    """Sends a state event once the wait has entered its poll loop."""
    for _ in range(200):
        if manager.refresh.called:
            break
        await asyncio.sleep(0.005)
    assert manager.refresh.called, "the wait never reached its poll loop"
    manager.subscribe.call_args.args[1]("sandbox.state.updated", _state_event(state))


class TestAsyncSandboxSubscribeBeforeRequest:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    def _sandbox(self, toolbox_api, sandbox_api, manager, state=SandboxState.STARTED):
        sandbox = _make_with_manager(make_sandbox_dto(state=state), toolbox_api, sandbox_api, manager)
        sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=state))
        return sandbox

    @staticmethod
    def _respond(manager: MagicMock, state: SandboxState, subscribed_before: list[bool]):
        async def respond(*_args, **_kwargs):
            subscribed_before.append(manager.subscribe.called)
            return _async_dto(state=state)

        return AsyncMock(side_effect=respond)

    @pytest.mark.asyncio
    async def test_stop_with_live_events_makes_no_get(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.stop_sandbox = self._respond(manager, SandboxState.STOPPING, subscribed_before)

        task = asyncio.create_task(sandbox.stop(timeout=0))
        await _deliver_once_waiting(manager, "stopped")
        await asyncio.wait_for(task, timeout=2)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STOPPED
        manager.subscribe.assert_called_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_stop_with_events_down_reads_once(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=False)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.stop_sandbox = self._respond(manager, SandboxState.STOPPING, subscribed_before)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))

        await sandbox.stop(timeout=0)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_state_event_during_the_request_forces_the_first_read(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)

        async def stop_sandbox(*_args, **_kwargs):
            # The event overtakes the response, which then carries the older state.
            manager.subscribe.call_args.args[1]("sandbox.state.updated", _state_event("stopped"))
            return _async_dto(state=SandboxState.STOPPING)

        mock_async_sandbox_api.stop_sandbox = AsyncMock(side_effect=stop_sandbox)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))

        await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_failed_stop_request_releases_the_subscription(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        from daytona.internal.event_subscription_manager import AsyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        sandbox = self._sandbox(
            mock_async_toolbox_api_client, mock_async_sandbox_api, AsyncEventSubscriptionManager(dispatcher)
        )
        listeners_during_request: list[int] = []

        async def stop_sandbox(*_args, **_kwargs):
            listeners_during_request.append(len(dispatcher.listeners.get("test-sandbox-id", [])))
            raise RuntimeError("stop rejected")

        mock_async_sandbox_api.stop_sandbox = AsyncMock(side_effect=stop_sandbox)

        with pytest.raises(DaytonaError, match="stop rejected") as exc_info:
            await sandbox.stop(timeout=0)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert listeners_during_request == [1]
        assert not dispatcher.listeners
        assert dispatcher.sent == [("subscribe", "test-sandbox-id"), ("unsubscribe", "test-sandbox-id")]
        mock_async_sandbox_api.get_sandbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_stop_request_keeps_other_listeners(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        from daytona.internal.event_subscription_manager import AsyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        manager = AsyncEventSubscriptionManager(dispatcher)
        other = manager.subscribe("test-sandbox-id", lambda *_: None, events=["sandbox.state.updated"])
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.stop_sandbox = AsyncMock(side_effect=RuntimeError("stop rejected"))

        with pytest.raises(DaytonaError, match="stop rejected"):
            await sandbox.stop(timeout=0)

        assert len(dispatcher.listeners["test-sandbox-id"]) == 1
        assert dispatcher.sent == [("subscribe", "test-sandbox-id")]
        manager.unsubscribe(other)

    @pytest.mark.asyncio
    async def test_stop_response_in_the_target_state_releases_the_subscription(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.stop_sandbox = self._respond(manager, SandboxState.STOPPED, subscribed_before)

        await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        manager.refresh.assert_not_called()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_delete_and_wait_with_live_events_makes_no_get(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.delete_sandbox = self._respond(manager, SandboxState.DESTROYING, subscribed_before)

        task = asyncio.create_task(sandbox.delete(timeout=0, wait=True))
        await _deliver_once_waiting(manager, "destroyed")
        await asyncio.wait_for(task, timeout=2)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.DESTROYED
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_delete_without_wait_takes_no_subscription(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.DESTROYING))

        await sandbox.delete(timeout=0)

        manager.subscribe.assert_not_called()
        mock_async_sandbox_api.get_sandbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_with_live_events_makes_no_get(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.pause_sandbox = self._respond(manager, SandboxState.PAUSING, subscribed_before)

        task = asyncio.create_task(sandbox.pause(timeout=0))
        await _deliver_once_waiting(manager, "paused")
        await asyncio.wait_for(task, timeout=2)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.PAUSED
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_resize_with_live_events_makes_no_get(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        from daytona.common.sandbox import Resources

        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        subscribed_before: list[bool] = []
        mock_async_sandbox_api.resize_sandbox = self._respond(manager, SandboxState.RESIZING, subscribed_before)

        task = asyncio.create_task(sandbox.resize(Resources(cpu=2), timeout=0))
        await _deliver_once_waiting(manager, "started")
        await asyncio.wait_for(task, timeout=2)

        assert subscribed_before == [True]
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        assert sandbox.state == SandboxState.STARTED
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_stop_error_response_reads_first_and_fails_at_once(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.ERROR))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.ERROR))

        with pytest.raises(DaytonaError, match="entered error state"):
            await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @staticmethod
    def _fork_dto(state: SandboxState) -> AsyncSandboxDto:
        return _async_dto(sandbox_id="forked-id", state=state, toolbox_proxy_url="http://forked:2280")

    @pytest.mark.asyncio
    async def test_live_fork_makes_no_get(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.fork_sandbox = AsyncMock(return_value=self._fork_dto(SandboxState.FORKING))

        task = asyncio.create_task(sandbox.fork(timeout=0))
        await _deliver_once_waiting(manager, "started")
        forked = await asyncio.wait_for(task, timeout=2)

        assert forked.state == SandboxState.STARTED
        assert manager.subscribe.call_args.args[0] == "forked-id"
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_fork_without_live_events_reads_once(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=False)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.fork_sandbox = AsyncMock(return_value=self._fork_dto(SandboxState.FORKING))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=self._fork_dto(SandboxState.STARTED))

        forked = await asyncio.wait_for(sandbox.fork(timeout=0), timeout=2)

        assert forked.state == SandboxState.STARTED
        mock_async_sandbox_api.get_sandbox.assert_awaited_once_with("forked-id", _request_timeout=None)
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_started_fork_takes_no_wait(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.fork_sandbox = AsyncMock(return_value=self._fork_dto(SandboxState.STARTED))

        forked = await sandbox.fork(timeout=0)

        assert forked.state == SandboxState.STARTED
        manager.subscribe.assert_not_called()
        mock_async_sandbox_api.get_sandbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_public_wait_keeps_its_first_read(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager, SandboxState.STOPPING)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))

        await asyncio.wait_for(sandbox.wait_for_sandbox_stop(timeout=0), timeout=2)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()


class TestAsyncSandboxOperationWaitBehaviour:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    @staticmethod
    def _sandbox(toolbox_api, sandbox_api, manager, state=SandboxState.STARTED):
        sandbox = _make_with_manager(make_sandbox_dto(state=state), toolbox_api, sandbox_api, manager)
        sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=state))
        return sandbox

    @pytest.mark.asyncio
    async def test_reconnect_during_the_request_forces_the_first_read(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)

        async def stop_sandbox(*_args, **_kwargs):
            # The socket drops and reconnects while the request is in flight; the `stopped` event is lost.
            manager.connection_epoch += 1
            return _async_dto(state=SandboxState.STOPPING)

        mock_async_sandbox_api.stop_sandbox = AsyncMock(side_effect=stop_sandbox)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))

        await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_delete_and_wait_with_an_empty_response_reads_first(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.DESTROYED))

        await asyncio.wait_for(sandbox.delete(timeout=0, wait=True), timeout=2)

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.DESTROYED
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_stop_with_a_swallowed_fallback_read_reads_first(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            side_effect=[make_pydantic_validation_error(), _async_dto(state=SandboxState.STOPPED)]
        )

        await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)

        # The swallowed fallback read, then the wait's immediate read.
        assert mock_async_sandbox_api.get_sandbox.await_count == 2
        assert sandbox.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_destroyed_fork_keeps_the_wait_error_prefix(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.fork_sandbox = AsyncMock(
            return_value=_async_dto(
                sandbox_id="forked-id",
                state=SandboxState.DESTROYED,
                toolbox_proxy_url="http://forked:2280",
                queue_timed_out_at="2026-01-01T00:00:00Z",
            )
        )

        with pytest.raises(DaytonaQueueTimeoutError) as exc_info:
            await sandbox.fork(timeout=0)

        assert str(exc_info.value) == (
            "Failed to fork sandbox: Failure during waiting for sandbox to start: Sandbox forked-id was destroyed"
            + " after waiting for a runner (queue timed out at 2026-01-01T00:00:00Z)"
        )
        manager.subscribe.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["stop", "fork", "resize"])
    async def test_operation_waits_keep_their_span_and_metric_labels(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, otel_recorder, operation
    ):
        from daytona.common.sandbox import Resources

        sandbox = self._sandbox(
            mock_async_toolbox_api_client, mock_async_sandbox_api, _subscription_manager(connected=False)
        )
        if operation == "stop":
            mock_async_sandbox_api.stop_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPING))
            mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STOPPED))
            await asyncio.wait_for(sandbox.stop(timeout=0), timeout=2)
            method = "wait_for_sandbox_stop"
        elif operation == "fork":
            forked = {"sandbox_id": "forked-id", "toolbox_proxy_url": "http://forked:2280"}
            mock_async_sandbox_api.fork_sandbox = AsyncMock(
                return_value=_async_dto(state=SandboxState.FORKING, **forked)
            )
            mock_async_sandbox_api.get_sandbox = AsyncMock(
                return_value=_async_dto(state=SandboxState.STARTED, **forked)
            )
            _ = await asyncio.wait_for(sandbox.fork(timeout=0), timeout=2)
            method = "wait_for_sandbox_start"
        else:
            mock_async_sandbox_api.resize_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.RESIZING))
            mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.STARTED))
            await asyncio.wait_for(sandbox.resize(Resources(cpu=2), timeout=0), timeout=2)
            method = "wait_for_resize_complete"

        attributes = {"component": "AsyncSandbox", "method": method}
        assert (f"AsyncSandbox.{method}", attributes) in otel_recorder.spans
        assert (f"async_sandbox_{method}_duration", {**attributes, "status": "success"}) in otel_recorder.metrics


class _Interrupt(BaseException):
    """Stands in for a timeout or KeyboardInterrupt landing between an operation's request and its wait."""


class TestAsyncSandboxOperationSubscriptionRelease:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "interrupted_method", "response_state"),
        [
            ("stop", "_wait_for_sandbox_stop_after_request", SandboxState.STOPPING),
            ("delete", "_wait_for_state", SandboxState.DESTROYING),
            ("pause", "_wait_for_state", SandboxState.PAUSING),
            ("resize", "_wait_for_resize_after_request", SandboxState.RESIZING),
            ("fork", "_wait_for_state", SandboxState.FORKING),
        ],
    )
    async def test_interrupt_after_the_request_releases_the_subscription(
        self,
        mock_async_toolbox_api_client,
        mock_async_sandbox_api,
        monkeypatch,
        operation,
        interrupted_method,
        response_state,
    ):
        from daytona._async.sandbox import AsyncSandbox
        from daytona.common.sandbox import Resources
        from daytona.internal.event_subscription_manager import AsyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        sandbox = _make_with_manager(
            make_sandbox_dto(),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            AsyncEventSubscriptionManager(dispatcher),
        )
        response = _async_dto(
            state=response_state, sandbox_id="forked-id" if operation == "fork" else "test-sandbox-id"
        )
        for api_method in ("stop_sandbox", "delete_sandbox", "pause_sandbox", "resize_sandbox", "fork_sandbox"):
            setattr(mock_async_sandbox_api, api_method, AsyncMock(return_value=response))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=response)

        async def interrupt(*_args, **_kwargs):
            raise _Interrupt()

        monkeypatch.setattr(AsyncSandbox, interrupted_method, interrupt)
        calls = {
            "stop": lambda: sandbox.stop(timeout=0),
            "delete": lambda: sandbox.delete(timeout=0, wait=True),
            "pause": lambda: sandbox.pause(timeout=0),
            "resize": lambda: sandbox.resize(Resources(cpu=2), timeout=0),
            "fork": lambda: sandbox.fork(timeout=0),
        }

        with pytest.raises(_Interrupt):
            await calls[operation]()

        resource_id = response.id
        assert dispatcher.listeners == {}
        assert dispatcher.sent == [("subscribe", resource_id), ("unsubscribe", resource_id)]


class TestAsyncSandboxResponsesWithoutState:
    """A response without a state is treated as an empty body: it is read again, never skipped on."""

    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "api_method", "final_state"),
        [
            ("stop", "stop_sandbox", SandboxState.STOPPED),
            ("delete", "delete_sandbox", SandboxState.DESTROYED),
            ("pause", "pause_sandbox", SandboxState.PAUSED),
            ("resize", "resize_sandbox", SandboxState.STARTED),
            ("fork", "fork_sandbox", SandboxState.STARTED),
        ],
    )
    async def test_operation_reads_before_waiting(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api, monkeypatch, operation, api_method, final_state
    ):
        from daytona.common.sandbox import Resources

        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTED),
            mock_async_toolbox_api_client,
            mock_async_sandbox_api,
            manager,
        )
        sandbox_id = "forked-id" if operation == "fork" else "test-sandbox-id"
        dto = {"sandbox_id": sandbox_id, "toolbox_proxy_url": "http://proxy:2280"}
        setattr(mock_async_sandbox_api, api_method, AsyncMock(return_value=_async_dto(state=None, **dto)))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=final_state, **dto))
        calls = {
            "stop": lambda: sandbox.stop(timeout=0),
            "delete": lambda: sandbox.delete(timeout=0, wait=True),
            "pause": lambda: sandbox.pause(timeout=0),
            "resize": lambda: sandbox.resize(Resources(cpu=2), timeout=0),
            "fork": lambda: sandbox.fork(timeout=0),
        }

        result = await asyncio.wait_for(calls[operation](), timeout=2)

        # Resolved by one read before any poll: no wait skipped its first read on the stateless response.
        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert mock_async_sandbox_api.get_sandbox.await_args.args[0] == sandbox_id
        assert poll_intervals == []
        assert (result if operation == "fork" else sandbox).state == final_state

    @pytest.mark.asyncio
    async def test_archive_reads_instead_of_applying_the_response(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(
            make_sandbox_dto(state=SandboxState.STOPPED), mock_async_toolbox_api_client, mock_async_sandbox_api
        )
        mock_async_sandbox_api.archive_sandbox = AsyncMock(return_value=_async_dto(state=None))
        mock_async_sandbox_api.get_sandbox = AsyncMock(return_value=_async_dto(state=SandboxState.ARCHIVING))

        await sandbox.archive()

        mock_async_sandbox_api.get_sandbox.assert_awaited_once()
        assert sandbox.state == SandboxState.ARCHIVING


class TestAsyncSandboxForkAndDeleteResponses:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    @pytest.mark.asyncio
    async def test_fork_whose_proxy_url_was_fetched_reads_first(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.fork_sandbox = AsyncMock(
            return_value=_async_dto(sandbox_id="forked-id", state=SandboxState.FORKING, toolbox_proxy_url="")
        )
        mock_async_sandbox_api.get_toolbox_proxy_url = AsyncMock(return_value=MagicMock(url="http://fetched:2280"))
        mock_async_sandbox_api.get_sandbox = AsyncMock(
            return_value=_async_dto(sandbox_id="forked-id", state=SandboxState.FORKING, toolbox_proxy_url="")
        )

        task = asyncio.create_task(sandbox.fork(timeout=0))
        await _deliver_once_waiting(manager, "started")
        forked = await asyncio.wait_for(task, timeout=2)

        assert forked.state == SandboxState.STARTED
        assert forked.toolbox_proxy_url == "http://fetched:2280"
        mock_async_sandbox_api.get_sandbox.assert_awaited_once_with("forked-id", _request_timeout=None)
        manager.unsubscribe.assert_called_once_with("sub-1")

    @pytest.mark.asyncio
    async def test_delete_without_wait_applies_a_response_without_state(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_async_toolbox_api_client, mock_async_sandbox_api, manager)
        mock_async_sandbox_api.delete_sandbox = AsyncMock(
            return_value=_async_dto(state=None, labels={"deleted-by": "test"})
        )
        mock_async_sandbox_api.get_sandbox = AsyncMock()

        await sandbox.delete(timeout=0)

        assert sandbox.labels == {"deleted-by": "test"}
        mock_async_sandbox_api.get_sandbox.assert_not_called()
        manager.subscribe.assert_not_called()
