# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
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
from daytona.common.process import ExecuteResponse
from daytona.common.sandbox import NodeResources
from daytona_api_client import SandboxState
from daytona_api_client_async import Sandbox as AsyncSandboxDto
from daytona_api_client_async import UpdateSandboxSecrets

from .conftest import make_pydantic_validation_error, make_sandbox_dto


def make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api):
    from daytona._async.sandbox import AsyncSandbox

    return AsyncSandbox(
        sandbox_dto,
        mock_async_toolbox_api_client,
        mock_async_sandbox_api,
        "python",
        subscription_manager=MagicMock(),
    )


class TestAsyncSandboxNodeSsh:
    @pytest.mark.parametrize("rank", [0, 1])
    @pytest.mark.parametrize(
        "expires_in_minutes,request_timeout,expected_timeout",
        [(None, None, None), (30, 0.25, 1)],
    )
    @pytest.mark.asyncio
    async def test_node_ssh_targets_selected_member(
        self,
        mock_async_toolbox_api_client,
        mock_async_sandbox_api,
        rank,
        expires_in_minutes,
        request_timeout,
        expected_timeout,
    ):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        access = MagicMock(token="ssh-token", ssh_command="ssh ssh-token@gateway.example.com")
        mock_async_sandbox_api.create_ssh_access = AsyncMock(return_value=access)
        mock_async_sandbox_api.revoke_ssh_access = AsyncMock()
        node = sandbox.nodes[rank]

        result = await node.create_ssh_access(expires_in_minutes, request_timeout=request_timeout)
        await node.revoke_ssh_access(access.token, request_timeout=request_timeout)

        assert result is access
        mock_async_sandbox_api.create_ssh_access.assert_awaited_once_with(
            node.id,
            expires_in_minutes=expires_in_minutes,
            _request_timeout=expected_timeout,
        )
        mock_async_sandbox_api.revoke_ssh_access.assert_awaited_once_with(
            node.id,
            token=access.token,
            _request_timeout=expected_timeout,
        )


class TestAsyncSandboxInit:
    def test_old_api_response_defaults_to_one_head_node(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        payload = make_sandbox_dto().to_dict()
        payload.pop("nodes")
        payload.pop("nodeCount")
        dto = AsyncSandboxDto.from_dict(payload)

        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)

        assert sandbox.node_count == 1
        assert [(node.rank, node.id) for node in sandbox.nodes] == [(0, sandbox.id)]

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

    def test_sandbox_serialization_includes_live_nodes(
        self, sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        sandbox = make_async_sandbox(sandbox_dto, mock_async_toolbox_api_client, mock_async_sandbox_api)

        payload = json.loads(sandbox.model_dump_json(by_alias=True))

        assert payload["nodes"][0]["id"] == sandbox.id
        assert payload["nodes"][0]["rank"] == 0
        assert sandbox.to_dict()["nodes"][0]["incarnation"] == 1

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
    async def test_exec_all_runs_same_command_on_every_node(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        for node in sandbox.nodes:
            node.process.exec = AsyncMock(
                side_effect=[
                    ExecuteResponse.model_construct(exit_code=0, result="", additional_properties={}),
                    ExecuteResponse.model_construct(
                        exit_code=node.rank, result=f"node-{node.rank}", additional_properties={}
                    ),
                ]
            )

        results = await sandbox.nodes.process.exec("hostname")

        assert list(results) == [0, 1]
        assert results[0].result == "node-0"
        assert results[1].exit_code == 1
        assert all(node.process.exec.await_args_list[1].args == ("hostname",) for node in sandbox.nodes)

    @pytest.mark.asyncio
    async def test_nodes_upload_file_reports_all_failed_ranks(
        self, mock_async_toolbox_api_client, mock_async_sandbox_api
    ):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        sandbox.nodes[0].fs.upload_file = AsyncMock(return_value=None)
        sandbox.nodes[1].fs.upload_file = AsyncMock(side_effect=RuntimeError("disk full"))

        with pytest.raises(DaytonaError, match=r"node ranks \[1\].*disk full"):
            await sandbox.nodes.fs.upload_file(b"data", "/workspace/data.bin")

        assert sandbox.nodes[0].fs.upload_file.await_count == 1
        assert sandbox.nodes[1].fs.upload_file.await_count == 1

    @pytest.mark.asyncio
    async def test_nodes_collection_stays_live_after_add(self, mock_async_toolbox_api_client, mock_async_sandbox_api):
        dto = make_sandbox_dto(topology_generation=1)
        updated = make_sandbox_dto(
            topology_generation=2,
            node_count=2,
            cpu=36,
            memory=264,
            disk=330,
            nodes=[
                {"rank": 0, "id": "test-sandbox-id", "cpu": 4, "memory": 8, "disk": 30},
                {"rank": 1, "id": "worker-1", "cpu": 32, "memory": 256, "disk": 300},
            ],
        )
        operation = MagicMock(id="op-1", status="COMPLETED", type="ADD", error_reason=None)
        mock_async_sandbox_api.get_sandbox = AsyncMock(side_effect=[dto, updated])
        mock_async_sandbox_api.add_sandbox_nodes = AsyncMock(return_value=operation)
        sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
        nodes = sandbox.nodes

        result = await nodes.add([NodeResources(cpu=32, memory=256, disk=300)], timeout=0)

        assert sandbox.nodes is nodes
        assert [(node.rank, node.id) for node in nodes] == [(0, "test-sandbox-id"), (1, "worker-1")]
        assert result is operation
        request = mock_async_sandbox_api.add_sandbox_nodes.await_args.args[1]
        assert request.expected_generation == 1
        assert request.nodes[0].model_dump(exclude={"additional_properties"}) == {
            "cpu": 32,
            "memory": 256,
            "disk": 300,
        }

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
        mock_async_sandbox_api.revoke_ssh_access.assert_awaited_once_with(
            sandbox.id, token="token", _request_timeout=None
        )
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


def test_lightweight_event_preserves_gang_metadata(mock_async_toolbox_api_client, mock_async_sandbox_api):
    dto = make_sandbox_dto(
        node_count=2,
        topology_generation=7,
        degraded=True,
        nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
    )
    sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
    from daytona_api_client_async import SandboxListItem

    update = SandboxListItem.model_validate(dto.model_dump())

    assert sandbox.degraded is True
    sandbox._AsyncSandbox__process_sandbox_dto(update)

    assert sandbox.node_count == 2
    assert sandbox.degraded is True
    assert sandbox.topology_generation == 7
    assert len(sandbox.nodes) == 2


@pytest.mark.parametrize("with_nodes", [True, False])
def test_proxy_refresh_updates_existing_node_clients(mock_async_toolbox_api_client, mock_async_sandbox_api, with_nodes):
    dto = make_sandbox_dto(node_count=2, toolbox_proxy_url="https://old.example/toolbox")
    sandbox = make_async_sandbox(dto, mock_async_toolbox_api_client, mock_async_sandbox_api)
    handles = list(sandbox.nodes)
    processes = [node.process for node in handles]
    update = dto.model_copy(update={"toolbox_proxy_url": "https://new.example/toolbox"})
    if not with_nodes:
        from daytona_api_client_async import SandboxListItem

        update = SandboxListItem.model_validate(update.model_dump())
    sandbox._AsyncSandbox__process_sandbox_dto(update)
    assert len(sandbox.nodes) == len(handles) == 2
    assert all(node is old for node, old in zip(sandbox.nodes, handles))
    assert all(node.process is old for node, old in zip(sandbox.nodes, processes))
    assert all(node._toolbox_api._toolbox_base_url == "https://new.example/toolbox" for node in handles)
