# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
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
from daytona.common.process import ExecuteResponse
from daytona.common.sandbox import NodeResources
from daytona_api_client import Sandbox as SandboxDto
from daytona_api_client import SandboxState, UpdateSandboxSecrets

from .conftest import make_pydantic_validation_error, make_sandbox_dto


def make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
    from daytona._sync.sandbox import Sandbox

    return Sandbox(
        sandbox_dto,
        mock_toolbox_api_client,
        mock_sandbox_api,
        "python",
        subscription_manager=MagicMock(),
        http_client=MagicMock(),
    )


class TestSandboxNodeSsh:
    @pytest.mark.parametrize("rank", [0, 1])
    @pytest.mark.parametrize(
        "expires_in_minutes,request_timeout,expected_timeout",
        [(None, None, None), (30, 0.25, 1)],
    )
    def test_node_ssh_targets_selected_member(
        self,
        mock_toolbox_api_client,
        mock_sandbox_api,
        rank,
        expires_in_minutes,
        request_timeout,
        expected_timeout,
    ):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        access = MagicMock(token="ssh-token", ssh_command="ssh ssh-token@gateway.example.com")
        mock_sandbox_api.create_ssh_access = MagicMock(return_value=access)
        mock_sandbox_api.revoke_ssh_access = MagicMock()
        node = sandbox.nodes[rank]

        result = node.create_ssh_access(expires_in_minutes, request_timeout=request_timeout)
        node.revoke_ssh_access(access.token, request_timeout=request_timeout)

        assert result is access
        mock_sandbox_api.create_ssh_access.assert_called_once_with(
            node.id,
            expires_in_minutes=expires_in_minutes,
            _request_timeout=expected_timeout,
        )
        mock_sandbox_api.revoke_ssh_access.assert_called_once_with(
            node.id,
            token=access.token,
            _request_timeout=expected_timeout,
        )


class TestSandboxInit:
    def test_old_api_response_defaults_to_one_head_node(self, mock_toolbox_api_client, mock_sandbox_api):
        payload = make_sandbox_dto().to_dict()
        payload.pop("nodes")
        payload.pop("nodeCount")
        dto = SandboxDto.from_dict(payload)

        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        assert sandbox.node_count == 1
        assert [(node.rank, node.id) for node in sandbox.nodes] == [(0, sandbox.id)]

    def test_sandbox_nodes_are_rank_ordered_handles(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[
                {"rank": 0, "id": "test-sandbox-id", "fabricHealthy": True},
                {"rank": 1, "id": "worker-1", "fabricHealthy": False},
            ],
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        assert [(node.rank, node.id) for node in sandbox.nodes] == [(0, "test-sandbox-id"), (1, "worker-1")]
        assert sandbox.nodes[1].fabric_healthy is False

    def test_sandbox_rejects_invalid_node_rank_order(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 2, "id": "worker-1"}],
        )
        with pytest.raises(DaytonaError, match=r"nodes\[i\]\.rank must equal i"):
            make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

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

    def test_sandbox_serialization_includes_live_nodes(self, sandbox_dto, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(sandbox_dto, mock_toolbox_api_client, mock_sandbox_api)

        payload = json.loads(sandbox.model_dump_json(by_alias=True))

        assert payload["nodes"][0]["id"] == sandbox.id
        assert payload["nodes"][0]["rank"] == 0
        assert sandbox.to_dict()["nodes"][0]["incarnation"] == 1

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
    def test_exec_all_runs_same_command_on_every_node(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        for node in sandbox.nodes:
            node.process.exec = MagicMock(
                side_effect=[
                    ExecuteResponse.model_construct(exit_code=0, result="", additional_properties={}),
                    ExecuteResponse.model_construct(
                        exit_code=node.rank, result=f"node-{node.rank}", additional_properties={}
                    ),
                ]
            )

        results = sandbox.nodes.process.exec("hostname")

        assert list(results) == [0, 1]
        assert results[0].result == "node-0"
        assert results[1].exit_code == 1
        assert all(node.process.exec.call_args_list[1].args == ("hostname",) for node in sandbox.nodes)

    def test_nodes_upload_file_reports_all_failed_ranks(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        sandbox.nodes[0].fs.upload_file = MagicMock(return_value=None)
        sandbox.nodes[1].fs.upload_file = MagicMock(side_effect=RuntimeError("disk full"))

        with pytest.raises(DaytonaError, match=r"node ranks \[1\].*disk full"):
            sandbox.nodes.fs.upload_file(b"data", "/workspace/data.bin")

        assert sandbox.nodes[0].fs.upload_file.call_count == 1
        assert sandbox.nodes[1].fs.upload_file.call_count == 1

    def test_nodes_collection_stays_live_after_add(self, mock_toolbox_api_client, mock_sandbox_api):
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
        mock_sandbox_api.get_sandbox.side_effect = [dto, updated]
        mock_sandbox_api.add_sandbox_nodes.return_value = operation
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        nodes = sandbox.nodes

        result = nodes.add([NodeResources(cpu=32, memory=256, disk=300)], timeout=0)

        assert sandbox.nodes is nodes
        assert [(node.rank, node.id) for node in nodes] == [(0, "test-sandbox-id"), (1, "worker-1")]
        assert result is operation
        request = mock_sandbox_api.add_sandbox_nodes.call_args.args[1]
        assert request.expected_generation == 1
        assert request.nodes[0].model_dump(exclude={"additional_properties"}) == {
            "cpu": 32,
            "memory": 256,
            "disk": 300,
        }

    def test_nodes_release_and_replace_send_generation(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(topology_generation=1)
        mock_sandbox_api.get_sandbox.return_value = dto
        mock_sandbox_api.remove_sandbox_nodes.return_value = MagicMock(
            id="remove-1", status="COMPLETED", type="REMOVE", error_reason=None
        )
        mock_sandbox_api.replace_sandbox_node.return_value = MagicMock(
            id="replace-1", status="COMPLETED", type="REPLACE", error_reason=None
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

        sandbox.nodes.release(count=1, timeout=0)
        sandbox.nodes.replace(rank=2, timeout=0)

        remove_request = mock_sandbox_api.remove_sandbox_nodes.call_args.args[1]
        replace_request = mock_sandbox_api.replace_sandbox_node.call_args.args[2]
        assert remove_request.expected_generation == 1
        assert remove_request.count == 1
        assert replace_request.expected_generation == 1

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
        mock_sandbox_api.revoke_ssh_access.assert_called_once_with(sandbox.id, token="token", _request_timeout=None)
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


def test_lightweight_event_preserves_gang_metadata(mock_toolbox_api_client, mock_sandbox_api):
    dto = make_sandbox_dto(
        node_count=2,
        topology_generation=7,
        degraded=True,
        nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": "worker-1"}],
    )
    sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
    from daytona_api_client import SandboxListItem

    update = SandboxListItem.model_validate(dto.model_dump())

    assert sandbox.degraded is True
    sandbox._Sandbox__process_sandbox_dto(update)

    assert sandbox.node_count == 2
    assert sandbox.degraded is True
    assert sandbox.topology_generation == 7
    assert len(sandbox.nodes) == 2


@pytest.mark.parametrize("with_nodes", [True, False])
def test_proxy_refresh_updates_existing_node_clients(mock_toolbox_api_client, mock_sandbox_api, with_nodes):
    dto = make_sandbox_dto(node_count=2, toolbox_proxy_url="https://old.example/toolbox")
    sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
    handles = list(sandbox.nodes)
    processes = [node.process for node in handles]
    update = dto.model_copy(update={"toolbox_proxy_url": "https://new.example/toolbox"})
    if not with_nodes:
        from daytona_api_client import SandboxListItem

        update = SandboxListItem.model_validate(update.model_dump())
    sandbox._Sandbox__process_sandbox_dto(update)
    assert len(sandbox.nodes) == len(handles) == 2
    assert all(node is old for node, old in zip(sandbox.nodes, handles))
    assert all(node.process is old for node, old in zip(sandbox.nodes, processes))
    assert all(node._toolbox_api._toolbox_base_url == "https://new.example/toolbox" for node in handles)
