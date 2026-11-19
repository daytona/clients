# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock

import pytest

import daytona_api_client
import daytona_api_client_async
from daytona import AsyncSandbox, Sandbox

from .conftest import make_sandbox_dto


@pytest.mark.parametrize("api", [daytona_api_client, daytona_api_client_async])
def test_scalar_create_and_legacy_region_quota(api):
    resources = {"cpu": 2, "memory": 4, "disk": 20}
    request = api.CreateSandbox(**resources)
    assert (request.cpu, request.memory, request.disk) == (2, 4, 20)
    assert api.CreateSandbox.from_json(request.to_json()).to_dict() == request.to_dict()
    for name in ("cpuPerNode", "memoryPerNode", "diskPerNode"):
        assert name not in api.CreateSandbox.from_dict(resources).to_dict()
        assert api.CreateSandbox.from_dict({**resources, name: None}).to_dict()[name] is None
    request.cpu = 0
    assert request.to_dict()["cpu"] == 0
    assert not {"cpuPerNode", "memoryPerNode", "diskPerNode"}.intersection(request.to_dict())
    assert api.CreateSandbox.from_dict({"cpuPerNode": [2, 4]}).to_dict()["cpuPerNode"] == [2, 4]

    quota = api.RegionQuota.from_dict(
        {
            "organizationId": "org",
            "regionId": "region",
            "sandboxClass": "container",
            "totalCpuQuota": 8,
            "totalMemoryQuota": 32,
            "totalDiskQuota": 100,
            "totalGpuQuota": 0,
        }
    )
    assert "maxNodesPerSandbox" not in quota.to_dict()
    assert api.RegionQuota.from_dict({**quota.to_dict(), "maxNodesPerSandbox": 0}).max_nodes_per_sandbox == 0


@pytest.mark.parametrize("cls,api", [(Sandbox, daytona_api_client), (AsyncSandbox, daytona_api_client_async)])
def test_single_node_handle_schema_and_wire_data(cls, api):
    for mode in ("validation", "serialization"):
        schema = cls.model_json_schema(mode=mode)
        assert "nodes" in schema["properties"]
    dto = api.Sandbox.from_dict(
        {
            "id": "ordinary",
            "name": "ordinary",
            "organizationId": "org",
            "user": "daytona",
            "env": {},
            "labels": {},
            "public": False,
            "networkBlockAll": False,
            "kvm": False,
            "target": "region",
            "cpu": 2,
            "memory": 4,
            "disk": 20,
            "gpu": 0,
            "state": "started",
            "toolboxProxyUrl": "https://proxy.example.com",
        }
    )
    kwargs = {"http_client": MagicMock()} if cls is Sandbox else {}
    sandbox = cls(dto, MagicMock(), MagicMock(), "python", MagicMock(), **kwargs)
    assert sandbox.nodes[0].id == dto.id
    assert "nodes" not in sandbox.to_dict()
    assert "nodes" not in json.loads(sandbox.model_dump_json(by_alias=True))


@pytest.mark.parametrize("cls,api", [(Sandbox, daytona_api_client), (AsyncSandbox, daytona_api_client_async)])
def test_empty_reported_nodes_round_trip_without_exposing_the_local_head(cls, api):
    full = make_sandbox_dto().to_dict()
    empty = {**full, "nodes": []}
    kwargs = {"http_client": MagicMock()} if cls is Sandbox else {}
    sandbox = cls(api.Sandbox.from_dict(empty), MagicMock(), MagicMock(), "python", MagicMock(), **kwargs)

    def assert_empty_membership():
        assert sandbox.nodes[0].id == sandbox.id
        assert sandbox.to_dict()["nodes"] == []
        assert json.loads(sandbox.to_json())["nodes"] == []
        assert json.loads(sandbox.model_dump_json(by_alias=True))["nodes"] == []
        assert "nodes" not in sandbox.model_dump(exclude={"nodes"})

    assert_empty_membership()
    sandbox._handle_event("sandbox.created", {"sandbox": full})
    assert [node["id"] for node in sandbox.to_dict()["nodes"]] == [sandbox.id]
    assert [node["id"] for node in sandbox.model_dump()["nodes"]] == [sandbox.id]
    sparse = full.copy()
    sparse.pop("nodes")
    sandbox._handle_event("sandbox.created", {"sandbox": sparse})
    assert [node["id"] for node in sandbox.to_dict()["nodes"]] == [sandbox.id]

    sandbox._handle_event("sandbox.created", {"sandbox": {**sparse, "incarnation": 2}})
    assert sandbox.nodes[0].incarnation == 2
    assert "nodes" not in sandbox.to_dict()

    sandbox._handle_event("sandbox.created", {"sandbox": full})
    sandbox._handle_event("sandbox.created", {"sandbox": empty})
    assert_empty_membership()
