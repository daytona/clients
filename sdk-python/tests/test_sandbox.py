# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import time
import warnings
from unittest.mock import MagicMock

import pytest

from daytona.common.errors import (
    DaytonaConflictError,
    DaytonaError,
    DaytonaQueueTimeoutError,
    DaytonaSpotEvictedError,
    DaytonaTimeoutError,
    DaytonaValidationError,
)
from daytona.common.process import ExecuteResponse
from daytona.common.sandbox import NodeResources
from daytona.internal import wait_poll
from daytona_api_client import Sandbox as SandboxDto
from daytona_api_client import SandboxState, UpdateSandboxSecrets

from .conftest import FakeEventDispatcher, make_pydantic_validation_error, make_sandbox_dto


def _polling_only_manager() -> MagicMock:
    """A manager without event streaming: waits take no subscription and poll at the fast cadence."""
    manager = MagicMock()
    manager.subscribe.return_value = ""
    manager.dispatcher = None
    manager.is_event_backed.return_value = False
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


def error_event(reason):
    """A `sandbox.state.updated` event as the API sends it to SDK sockets (fields without a value are omitted)."""
    return {
        "sandbox": {"id": "test-sandbox-id", "state": "error", "desiredState": "started", "errorReason": reason},
        "oldState": "starting",
        "newState": "error",
    }


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
    def test_sparse_sandbox_update_preserves_node_details(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            nodes=[
                {
                    "id": "test-sandbox-id",
                    "rank": 0,
                    "incarnation": 3,
                    "fabricHealthy": True,
                }
            ]
        )
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        head = sandbox.nodes[0]
        event = dto.to_dict()
        event.pop("nodes")
        event.update(cpu=6, gpu=1, memory=12, disk=40, state="stopped")
        sandbox._Sandbox__process_sandbox_dto(SandboxDto.from_dict(event))

        assert sandbox.nodes[0] is head
        assert (head.cpu, head.gpu, head.memory, head.disk, head.state.value) == (6, 1, 12, 40, "stopped")
        assert head.incarnation == 3
        assert head.fabric_healthy is True

        event["incarnation"] = 4
        sandbox._Sandbox__process_sandbox_dto(SandboxDto.from_dict(event))
        assert head.incarnation == 4
        assert head.fabric_healthy is None

        sandbox._handle_event("sandbox.state.updated", {"newState": "started"})
        assert head.state.value == "started"

    @pytest.mark.parametrize("invalid_id", ["", ".", "..", "two/segments"])
    def test_rejects_invalid_worker_id(self, invalid_id, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(
            node_count=2,
            nodes=[{"rank": 0, "id": "test-sandbox-id"}, {"rank": 1, "id": invalid_id}],
        )
        with pytest.raises(DaytonaError, match="path segment"):
            make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)

    def test_refresh_updates_single_node_without_member_array(self, mock_toolbox_api_client, mock_sandbox_api):
        payload = make_sandbox_dto().to_dict()
        payload.pop("nodes")
        initial = SandboxDto.from_dict(dict(payload, state="stopped", cpu=2))
        updated = SandboxDto.from_dict(dict(payload, state="started", cpu=4))
        sandbox = make_sandbox(initial, mock_toolbox_api_client, mock_sandbox_api)
        head = sandbox.nodes[0]
        mock_sandbox_api.get_sandbox.return_value = updated

        sandbox.refresh_data()

        assert sandbox.nodes[0] is head
        assert head.cpu == 4
        assert head.state.value == "started"

    def test_sparse_gang_waits_for_member_details(self, mock_toolbox_api_client, mock_sandbox_api):
        full = make_sandbox_dto(node_count=2).to_dict()
        sparse = dict(full)
        sparse.pop("nodes")
        sandbox = make_sandbox(SandboxDto.from_dict(sparse), mock_toolbox_api_client, mock_sandbox_api)
        nodes = sandbox.nodes

        assert sandbox.node_count == 2
        assert len(nodes) == 0
        with pytest.raises(DaytonaError, match="membership is unavailable"):
            nodes.process.exec("hostname")
        with pytest.raises(DaytonaError, match="membership is unavailable"):
            nodes.fs.upload_file(b"data", "/file")

        mock_sandbox_api.get_sandbox.return_value = SandboxDto.from_dict(full)
        sandbox.refresh_data()
        assert [node.id for node in nodes] == [sandbox.id, f"{sandbox.id}-worker-1"]

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

    def test_worker_lookup_keeps_its_physical_rank(self, mock_toolbox_api_client, mock_sandbox_api):
        dto = make_sandbox_dto(sandbox_id="worker-2", nodes=[{"rank": 2, "id": "worker-2"}])
        dto.head_sandbox_id, dto.node_rank = "head", 2
        sandbox = make_sandbox(dto, mock_toolbox_api_client, mock_sandbox_api)
        assert [(node.rank, node.id) for node in sandbox.nodes] == [(2, "worker-2")]

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

    def test_stale_release_uses_the_observed_generation(self, mock_toolbox_api_client, mock_sandbox_api):
        from daytona_api_client.exceptions import ApiException

        sandbox = make_sandbox(
            make_sandbox_dto(node_count=3, topology_generation=4), mock_toolbox_api_client, mock_sandbox_api
        )
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(node_count=4, topology_generation=5)
        mock_sandbox_api.remove_sandbox_nodes.side_effect = ApiException(status=409, reason="Topology changed")
        with pytest.raises(DaytonaConflictError):
            sandbox.nodes.release(timeout=0)
        assert mock_sandbox_api.remove_sandbox_nodes.call_args.args[1].expected_generation == 4
        mock_sandbox_api.get_sandbox.assert_not_called()

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
        mock_sandbox_api.get_sandbox.return_value = updated
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

    def test_error_event_reports_its_reason(self, mock_toolbox_api_client, mock_sandbox_api):
        starting = make_sandbox_dto(state=SandboxState.STARTING)
        sandbox = make_sandbox(starting, mock_toolbox_api_client, mock_sandbox_api)

        def poll_overtaken_by_the_event(*_args, **_kwargs):
            sandbox._handle_event("sandbox.state.updated", error_event("disk full"))
            return starting

        mock_sandbox_api.get_sandbox.side_effect = poll_overtaken_by_the_event
        with pytest.raises(DaytonaError, match="error reason: disk full"):
            sandbox.wait_for_sandbox_start(timeout=10)

    def test_state_event_without_reason_clears_it(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(
            make_sandbox_dto(state=SandboxState.ERROR, error_reason="transient"),
            mock_toolbox_api_client,
            mock_sandbox_api,
        )
        sandbox._handle_event(
            "sandbox.state.updated",
            {"sandbox": {"id": "test-sandbox-id", "state": "started"}, "oldState": "error", "newState": "started"},
        )
        assert sandbox.state == SandboxState.STARTED
        assert sandbox.error_reason is None


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
    manager.connection_epoch = 1
    # Every id is served: event-backed exactly while connected.
    manager.is_event_backed.side_effect = lambda _resource_id: manager.dispatcher.is_connected
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

    def test_state_event_applies_the_state_and_its_error_reason(self, mock_toolbox_api_client, mock_sandbox_api):
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
        assert sandbox.error_reason == "boom"
        assert sandbox.cpu == 4  # other payload fields are ignored

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

        # Fallback backoff 1 s, 1.5 s; 5 s while event-backed; the backoff starts over at 1 s after it.
        assert [interval for interval, _ in poll_intervals] == pytest.approx([1.0, 1.5, 5.0, 5.0, 1.0])

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

    @pytest.mark.parametrize(("random_value", "jitter"), [(0.0, 0.8), (1.0, 1.2)])
    def test_subscribed_wait_without_connection_backs_off_from_one_second(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, random_value, jitter
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

        assert [interval for interval, _ in poll_intervals] == pytest.approx(
            [1.0 * jitter, 1.5 * jitter, 2.25 * jitter]
        )

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

    def wait_for_wake(wake: threading.Event, timeout: float) -> bool:
        refreshes = sandbox_api.get_sandbox.call_count if sandbox_api is not None else -1
        poll_intervals.append((timeout, refreshes))
        return wake.wait(0)

    monkeypatch.setattr(sandbox_module, "_wait_for_wake", wait_for_wake)
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


def _deliver_once_waiting(manager: MagicMock, state: str, event: dict[str, object] | None = None) -> threading.Thread:
    """Sends a state event from another thread, as the dispatcher does, once the wait is in its poll loop.

    An operation's wait renews its subscription once when it starts and once per loop iteration.
    """

    def deliver() -> None:
        if _wait_until(lambda: manager.refresh.call_count >= 2):
            manager.subscribe.call_args.args[1]("sandbox.state.updated", event or _state_event(state))

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


class TestSandboxOperationWaitBehaviour:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    @staticmethod
    def _sandbox(toolbox_api, sandbox_api, manager, state=SandboxState.STARTED):
        sandbox = _make_with_manager(make_sandbox_dto(state=state), toolbox_api, sandbox_api, manager)
        sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=state)
        return sandbox

    def test_reconnect_during_the_request_forces_the_first_read(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)

        def stop_sandbox(*_args, **_kwargs):
            # The socket drops and reconnects while the request is in flight; the `stopped` event is lost.
            manager.connection_epoch += 1
            return make_sandbox_dto(state=SandboxState.STOPPING)

        mock_sandbox_api.stop_sandbox.side_effect = stop_sandbox
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=2)

        assert poll_intervals == []  # resolved by the immediate read, not a later poll
        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.STOPPED

    def test_delete_and_wait_with_an_empty_response_reads_first(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.delete_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.DESTROYED)

        sandbox.delete(timeout=2, wait=True)

        assert poll_intervals == []  # resolved by the immediate read, not a later poll
        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.DESTROYED
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_stop_with_a_swallowed_fallback_read_reads_first(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = None
        mock_sandbox_api.get_sandbox.side_effect = [
            make_pydantic_validation_error(),
            make_sandbox_dto(state=SandboxState.STOPPED),
        ]

        sandbox.stop(timeout=2)

        # The swallowed fallback read, then the wait's immediate read.
        assert poll_intervals == []
        assert mock_sandbox_api.get_sandbox.call_count == 2
        assert sandbox.state == SandboxState.STOPPED

    def test_destroyed_fork_keeps_the_wait_error_prefix(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.fork_sandbox.return_value = make_sandbox_dto(
            sandbox_id="forked-id",
            state=SandboxState.DESTROYED,
            toolbox_proxy_url="http://forked:2280",
            queue_timed_out_at="2026-01-01T00:00:00Z",
        )

        with pytest.raises(DaytonaQueueTimeoutError) as exc_info:
            sandbox.fork(timeout=2)

        assert str(exc_info.value) == (
            "Failed to fork sandbox: Failure during waiting for sandbox to start: Sandbox forked-id was destroyed"
            + " after waiting for a runner (queue timed out at 2026-01-01T00:00:00Z)"
        )
        manager.subscribe.assert_not_called()

    @pytest.mark.parametrize("operation", ["stop", "fork", "resize"])
    def test_operation_waits_keep_their_span_and_metric_labels(
        self, mock_toolbox_api_client, mock_sandbox_api, otel_recorder, operation
    ):
        from daytona.common.sandbox import Resources

        sandbox = self._sandbox(mock_toolbox_api_client, mock_sandbox_api, _subscription_manager(connected=False))
        if operation == "stop":
            mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPING)
            mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)
            sandbox.stop(timeout=2)
            method = "wait_for_sandbox_stop"
        elif operation == "fork":
            forked = {"sandbox_id": "forked-id", "toolbox_proxy_url": "http://forked:2280"}
            mock_sandbox_api.fork_sandbox.return_value = make_sandbox_dto(state=SandboxState.FORKING, **forked)
            mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTED, **forked)
            _ = sandbox.fork(timeout=2)
            method = "wait_for_sandbox_start"
        else:
            mock_sandbox_api.resize_sandbox.return_value = make_sandbox_dto(state=SandboxState.RESIZING)
            mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STARTED)
            sandbox.resize(Resources(cpu=2), timeout=2)
            method = "wait_for_resize_complete"

        attributes = {"component": "Sandbox", "method": method}
        assert (f"Sandbox.{method}", attributes) in otel_recorder.spans
        assert (f"sandbox_{method}_duration", {**attributes, "status": "success"}) in otel_recorder.metrics


class _Interrupt(BaseException):
    """Stands in for a timeout or KeyboardInterrupt landing between an operation's request and its wait."""


class TestSandboxOperationSubscriptionRelease:
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
    def test_interrupt_after_the_request_releases_the_subscription(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, operation, interrupted_method, response_state
    ):
        from daytona._sync.sandbox import Sandbox
        from daytona.common.sandbox import Resources
        from daytona.internal.event_subscription_manager import SyncEventSubscriptionManager

        dispatcher = FakeEventDispatcher(connected=True)
        manager = SyncEventSubscriptionManager(dispatcher)  # pyright: ignore[reportArgumentType]
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        resource_id = "forked-id" if operation == "fork" else "test-sandbox-id"
        response = make_sandbox_dto(state=response_state, sandbox_id=resource_id)
        for api_method in ("stop_sandbox", "delete_sandbox", "pause_sandbox", "resize_sandbox", "fork_sandbox"):
            getattr(mock_sandbox_api, api_method).return_value = response
        mock_sandbox_api.get_sandbox.return_value = response

        def interrupt(*_args, **_kwargs):
            raise _Interrupt()

        monkeypatch.setattr(Sandbox, interrupted_method, interrupt)
        calls = {
            "stop": lambda: sandbox.stop(timeout=0),
            "delete": lambda: sandbox.delete(timeout=0, wait=True),
            "pause": lambda: sandbox.pause(timeout=0),
            "resize": lambda: sandbox.resize(Resources(cpu=2), timeout=0),
            "fork": lambda: sandbox.fork(timeout=0),
        }

        try:
            with pytest.raises(_Interrupt):
                calls[operation]()
            assert dispatcher.listeners == {}
            assert dispatcher.sent == [("subscribe", resource_id), ("unsubscribe", resource_id)]
        finally:
            manager.shutdown()


class TestSandboxResponsesWithoutState:
    """A response without a state is treated as an empty body: it is read again, never skipped on."""

    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

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
    def test_operation_reads_before_waiting(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, operation, api_method, final_state
    ):
        from daytona.common.sandbox import Resources

        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTED), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        sandbox_id = "forked-id" if operation == "fork" else "test-sandbox-id"
        dto = {"sandbox_id": sandbox_id, "toolbox_proxy_url": "http://proxy:2280"}
        getattr(mock_sandbox_api, api_method).return_value = make_sandbox_dto(state=None, **dto)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=final_state, **dto)
        calls = {
            "stop": lambda: sandbox.stop(timeout=2),
            "delete": lambda: sandbox.delete(timeout=2, wait=True),
            "pause": lambda: sandbox.pause(timeout=2),
            "resize": lambda: sandbox.resize(Resources(cpu=2), timeout=2),
            "fork": lambda: sandbox.fork(timeout=2),
        }

        result = calls[operation]()

        # Resolved by one read before any poll: no wait skipped its first read on the stateless response.
        mock_sandbox_api.get_sandbox.assert_called_once()
        assert mock_sandbox_api.get_sandbox.call_args.args[0] == sandbox_id
        assert poll_intervals == []
        assert (result if operation == "fork" else sandbox).state == final_state

    def test_archive_reads_instead_of_applying_the_response(self, mock_toolbox_api_client, mock_sandbox_api):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STOPPED), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.archive_sandbox.return_value = make_sandbox_dto(state=None)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.ARCHIVING)

        sandbox.archive()

        mock_sandbox_api.get_sandbox.assert_called_once()
        assert sandbox.state == SandboxState.ARCHIVING


class TestSandboxForkAndDeleteResponses:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    def test_fork_whose_proxy_url_was_fetched_reads_first(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.fork_sandbox.return_value = make_sandbox_dto(
            sandbox_id="forked-id", state=SandboxState.FORKING, toolbox_proxy_url=""
        )
        mock_sandbox_api.get_toolbox_proxy_url.return_value = MagicMock(url="http://fetched:2280")
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(
            sandbox_id="forked-id", state=SandboxState.FORKING, toolbox_proxy_url=""
        )

        sender = _deliver_once_waiting(manager, "started")
        forked = sandbox.fork(timeout=5)
        sender.join(timeout=5)

        assert forked.state == SandboxState.STARTED
        assert forked.toolbox_proxy_url == "http://fetched:2280"
        mock_sandbox_api.get_sandbox.assert_called_once_with("forked-id", _request_timeout=None)
        manager.unsubscribe.assert_called_once_with("sub-1")

    def test_delete_without_wait_applies_a_response_without_state(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.delete_sandbox.return_value = make_sandbox_dto(state=None, labels={"deleted-by": "test"})

        sandbox.delete(timeout=5)

        assert sandbox.labels == {"deleted-by": "test"}
        mock_sandbox_api.get_sandbox.assert_not_called()
        manager.subscribe.assert_not_called()


def _rate_limited(retry_after: str | None = None) -> Exception:
    from daytona_api_client.exceptions import ApiException

    exc = ApiException(status=429, reason="Too Many Requests")
    exc.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return exc


class TestSandboxFallbackBackoffAndRateLimits:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    def _waiting_sandbox(self, toolbox_api, sandbox_api, connected: bool = False):
        manager = _subscription_manager(connected=connected)
        return manager, _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), toolbox_api, sandbox_api, manager
        )

    def test_not_event_backed_wait_backs_off_up_to_ten_seconds(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        _, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [make_sandbox_dto(state=SandboxState.STARTING)] * 9 + [
            make_sandbox_dto(state=SandboxState.STARTED)
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [i for i, _ in poll_intervals] == pytest.approx(
            [1.0, 1.5, 2.25, 3.375, 5.0625, 7.59375, 10.0, 10.0, 10.0]
        )

    def test_backoff_starts_over_after_a_stretch_of_events(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api)
        connected = iter([False, False, False, True, False, False])
        reads = 0

        def get_sandbox(*_args, **_kwargs):
            nonlocal reads
            reads += 1
            manager.dispatcher.is_connected = next(connected, False)
            return make_sandbox_dto(state=SandboxState.STARTED if reads == 7 else SandboxState.STARTING)

        mock_sandbox_api.get_sandbox.side_effect = get_sandbox

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [i for i, _ in poll_intervals] == pytest.approx([1.0, 1.5, 2.25, 5.0, 1.0, 1.5])

    def test_rate_limited_poll_waits_for_retry_after_and_the_wait_resolves(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        _, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.STARTING),
            _rate_limited("3"),
            make_sandbox_dto(state=SandboxState.STARTED),
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        # Retry-After decides, with upward jitter only (x1.1 at the stubbed midpoint).
        assert [i for i, _ in poll_intervals] == pytest.approx([1.0, 3.3])
        assert sandbox.state == SandboxState.STARTED

    def test_rate_limited_poll_without_retry_after_waits_the_current_interval(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        _, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api, connected=True)
        mock_sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.STARTING),
            _rate_limited(),
            make_sandbox_dto(state=SandboxState.STARTED),
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        assert [i for i, _ in poll_intervals] == pytest.approx([5.0, 5.0])
        assert sandbox.state == SandboxState.STARTED

    def test_rate_limited_first_read_does_not_fail_the_wait(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        _, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [_rate_limited("2"), make_sandbox_dto(state=SandboxState.STARTED)]

        sandbox.wait_for_sandbox_start(timeout=0)

        # max(1 s fallback backoff, Retry-After 2 s), Retry-After with upward jitter (x1.1).
        assert [i for i, _ in poll_intervals] == pytest.approx([2.2])

    def test_server_error_during_a_poll_still_fails_the_wait(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        from daytona_api_client.exceptions import ApiException

        _ = _record_poll_intervals(monkeypatch)
        _, sandbox = self._waiting_sandbox(mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.STARTING),
            ApiException(status=500, reason="Internal Server Error"),
            make_sandbox_dto(state=SandboxState.STARTING),
        ]

        with pytest.raises(DaytonaError, match="Internal Server Error"):
            sandbox.wait_for_sandbox_start(timeout=0)

        # The poll's error, then the final read of the error path, as before.
        assert mock_sandbox_api.get_sandbox.call_count == 3


class TestSandboxReviewRound9:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    @staticmethod
    def _record_wakes(monkeypatch) -> list[tuple[float, bool]]:
        """Records each wait between polls as (interval, woken early) without sleeping."""
        from daytona._sync import sandbox as sandbox_module

        waits: list[tuple[float, bool]] = []

        def wait_for_wake(wake: threading.Event, timeout: float) -> bool:
            woken = wake.wait(0)
            waits.append((timeout, woken))
            return woken

        monkeypatch.setattr(sandbox_module, "_wait_for_wake", wait_for_wake)
        return waits

    @pytest.mark.parametrize(("random_value", "jitter"), [(0.0, 0.8), (1.0, 1.2)])
    def test_rejection_during_a_read_is_covered_by_it_and_the_next_poll_falls_back(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch, random_value, jitter
    ):
        from daytona.internal.event_dispatcher import SUBSCRIPTION_REJECTED_EVENT

        monkeypatch.setattr(wait_poll, "random_unit", lambda: random_value)
        waits = self._record_wakes(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(
            make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api, manager
        )
        reads = 0
        in_flight = 0
        max_in_flight = 0
        guard = threading.Lock()

        def get_sandbox(*_args, **_kwargs):
            nonlocal reads, in_flight, max_in_flight
            with guard:
                reads += 1
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                current = reads
            if current == 2:
                # The server rejects the subscription on the socket's thread while this read is in flight.
                manager.dispatcher.is_connected = False
                rejecter = threading.Thread(
                    target=sandbox._handle_event, args=(SUBSCRIPTION_REJECTED_EVENT, {"error": "OVERLOADED"})
                )
                rejecter.start()
                rejecter.join()
            with guard:
                in_flight -= 1
            return make_sandbox_dto(state=SandboxState.STARTED if current == 4 else SandboxState.STARTING)

        mock_sandbox_api.get_sandbox.side_effect = get_sandbox

        sandbox.wait_for_sandbox_start(timeout=0)

        # 5 s while event-backed; the rejection during read 2 is covered by that read (no extra read and
        # no early wake): the next delay is chosen after it, at the fallback cadence, then backs off.
        assert waits == [
            (pytest.approx(5.0 * jitter), False),
            (pytest.approx(1.0 * jitter), False),
            (pytest.approx(1.5 * jitter), False),
        ]
        assert reads == 4 and max_in_flight == 1

    def test_repeated_rate_limits_back_off_without_a_subscription(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = [_rate_limited()] * 3 + [
            make_sandbox_dto(state=SandboxState.STARTING),
            make_sandbox_dto(state=SandboxState.STARTED),
        ]

        sandbox.wait_for_sandbox_start(timeout=0)

        # 1, 1.5, 2.25 s after each rate limit, then the fast cadence again once a read succeeds.
        assert [i for i, _ in poll_intervals] == pytest.approx([1.0, 1.5, 2.25, 0.1])

    def test_expired_operation_subscription_is_retaken_and_the_wait_reads_first(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        manager.refresh.side_effect = lambda sub_id: sub_id != "sub-1"  # expired during the request
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPING)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPED)

        sandbox.stop(timeout=0)

        assert manager.subscribe.call_count == 2
        mock_sandbox_api.get_sandbox.assert_called_once()
        assert poll_intervals == []  # resolved by the immediate read
        assert sorted(c.args[0] for c in manager.unsubscribe.call_args_list) == ["sub-1", "sub-2"]

    def test_error_event_with_a_reason_ends_the_wait_with_it(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPING)
        event = {
            "sandbox": {"id": "test-sandbox-id", "state": "error", "errorReason": "disk full"},
            "newState": "error",
        }

        sender = _deliver_once_waiting(manager, "error", event)
        with pytest.raises(DaytonaError, match="error reason: disk full"):
            sandbox.stop(timeout=5)
        sender.join(timeout=5)

        mock_sandbox_api.get_sandbox.assert_not_called()

    def test_error_event_without_a_reason_reads_it_before_failing(self, mock_toolbox_api_client, mock_sandbox_api):
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPING)
        mock_sandbox_api.get_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR, error_reason="disk full")

        sender = _deliver_once_waiting(manager, "error")  # a slim event: no errorReason
        with pytest.raises(DaytonaError, match="entered error state: .*, error reason: disk full"):
            sandbox.stop(timeout=5)
        sender.join(timeout=5)

        mock_sandbox_api.get_sandbox.assert_called_once()


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


class TestSandboxReviewRound10:
    @pytest.fixture(autouse=True)
    def _stub_jitter(self, monkeypatch):
        monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)

    def test_rate_limited_first_read_does_not_fail_on_a_cached_error(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        poll_intervals = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        # An error response forces the first read, which is rate limited; the next read is the truth.
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR)
        mock_sandbox_api.get_sandbox.side_effect = [_rate_limited(), make_sandbox_dto(state=SandboxState.STOPPED)]

        sandbox.stop(timeout=0)

        assert sandbox.state == SandboxState.STOPPED
        assert mock_sandbox_api.get_sandbox.call_count == 2
        assert len(poll_intervals) == 1

    def test_a_cached_error_confirmed_after_a_rate_limited_first_read_fails_the_wait(
        self, mock_toolbox_api_client, mock_sandbox_api, monkeypatch
    ):
        _ = _record_poll_intervals(monkeypatch)
        manager = _subscription_manager(connected=True)
        sandbox = _make_with_manager(make_sandbox_dto(), mock_toolbox_api_client, mock_sandbox_api, manager)
        mock_sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.ERROR)
        mock_sandbox_api.get_sandbox.side_effect = [
            _rate_limited(),
            make_sandbox_dto(state=SandboxState.ERROR, error_reason="disk full"),
        ]

        with pytest.raises(DaytonaError, match="entered error state: .*, error reason: disk full"):
            sandbox.stop(timeout=0)

        assert mock_sandbox_api.get_sandbox.call_count == 2

    def test_timeout_lands_on_time_during_a_long_retry_after_off_the_main_thread(
        self, mock_toolbox_api_client, mock_sandbox_api
    ):
        sandbox = make_sandbox(make_sandbox_dto(state=SandboxState.STARTING), mock_toolbox_api_client, mock_sandbox_api)
        mock_sandbox_api.get_sandbox.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(_rate_limited("6"))
        outcome: list[BaseException | None] = []
        elapsed: list[float] = []

        def wait() -> None:
            start = time.monotonic()
            try:
                sandbox.wait_for_sandbox_start(timeout=1)
                outcome.append(None)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                outcome.append(exc)
            elapsed.append(time.monotonic() - start)

        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        waiter.join(timeout=10)

        assert not waiter.is_alive()
        assert isinstance(outcome[0], DaytonaTimeoutError)
        # The next poll is 6.6 s away; the timeout still lands within the 1 s wait slice.
        assert elapsed[0] < 2.5
