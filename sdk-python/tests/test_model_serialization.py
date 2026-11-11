# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""SDK-returned models are plain frozen dataclasses: ``dataclasses.asdict``,
``copy.deepcopy`` and a ``pickle`` round trip work on each, and their
JSON-native fields (``Volume.options``) serialize with ``json.dumps``."""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle

import pytest

from daytona.v2._api_core import (
    expect_audit_actions,
    expect_current_api_key,
    expect_image,
    expect_mac_catalog,
    expect_organization_limits,
    expect_project_budget,
    expect_secret,
    expect_volume,
)
from daytona.v2._core import parse_snapshot
from daytona.v2.services import _mac_reservation, _volume
from daytona.v2.types import JsonObject, ListResult, SearchResult, Volume

_TIME = "2026-01-01T00:00:00Z"
_USAGE: JsonObject = {"limit": 5, "usage": 1}
_MAC: JsonObject = {"cores": 4, "memory_mib": 8192, "disk_mib": 81920}
_MODEL: JsonObject = {
    "id": "m4",
    "name": "Apple M4",
    "cores": 10,
    "memory_mib": 16384,
    "disk_mib": 244198,
    "sandbox_disk_mib": 235520,
    "max_sandboxes": 2,
    "default_size": _MAC,
}

_MODELS: dict[str, object] = {
    "Volume": _volume(
        expect_volume(
            {
                "id": "vol",
                "name": "data",
                "type": "s3",
                "status": "ready",
                "created_at": _TIME,
                "updated_at": _TIME,
                "options": {"bucket": "b", "nested": {"depth": [1, 2]}},
            }
        )
    ),
    "Image": expect_image(
        {
            "id": "img",
            "class": "linux",
            "status": "ready",
            "region_id": "r",
            "region_ids": ["r"],
            "anonymous": False,
            "retention": "permanent",
            "origin": "registry",
            "created_at": _TIME,
            "updated_at": _TIME,
            "format_version": 2,
            "format_outdated": False,
            "platform": False,
            "default": False,
            "deprecated": False,
            "source": {"type": "registry", "ref": "alpine"},
        }
    ),
    "Snapshot": parse_snapshot(
        {
            "id": "snap",
            "name": "s",
            "class": "linux",
            "kind": "full",
            "status": "ready",
            "region_id": "r",
            "region_ids": ["r"],
            "created_at": _TIME,
            "updated_at": _TIME,
        }
    ),
    "Secret": expect_secret(
        {"name": "KEY", "allowed_domains": ["example.com"], "created_at": _TIME, "updated_at": _TIME}
    ),
    "MacReservation": _mac_reservation(
        {
            "id": "res",
            "org_id": "o",
            "project_id": "p",
            "region_id": "r",
            "status": "active",
            "origin": "explicit",
            "auto_renew": True,
            "slots_used": 0,
            "period_n": 1,
            "period_seconds": 86400,
            "renewal_pending": False,
            "created_at": _TIME,
            "resources_used": _MAC,
        }
    ),
    "MacCatalog": expect_mac_catalog(
        {
            "regions": [
                {
                    "id": "r",
                    "default_model": "m4",
                    "models": [{"model": _MODEL, "available": 1, "price": None}],
                }
            ]
        }
    ),
    "AuditActionList": expect_audit_actions({"targets": [{"target_type": "sandbox", "actions": ["create"]}]}),
    "ProjectApiKey": expect_current_api_key(
        {
            "id": "k",
            "name": "key",
            "project_id": "p",
            "created_at": _TIME,
            "project_permissions": ["read"],
            "sync_to_v0": False,
            "context": {"org_id": "o", "org_name": "org", "project_name": "proj"},
        }
    ),
    "ProjectBudget": expect_project_budget(
        {"monthly_limit_cents": 100, "period_start_date": None, "created_at": None, "updated_at": None}
    ),
    "OrganizationLimits": expect_organization_limits(
        {
            "sandboxes": {"concurrent": _USAGE, "total": _USAGE},
            "sandbox_timeout": {"max_seconds": 86400, "ceiling_seconds": 604800},
            "image_builds": {"concurrent": _USAGE},
            "storage": {"volumes": _USAGE, "images": _USAGE, "snapshots": _USAGE},
            "secrets_per_project": _USAGE,
            "api_keys_per_project": _USAGE,
            "api_keys_per_organization": _USAGE,
        }
    ),
    "ListResult": ListResult(items=["a", "b"], next_cursor="next"),
    "SearchResult": SearchResult(items=["a"], total=1, total_relation="gte", raw_total_relation="gte"),
}


@pytest.mark.parametrize("model", list(_MODELS.values()), ids=list(_MODELS))
def test_a_model_is_a_plain_dataclass(model: object) -> None:
    assert dataclasses.is_dataclass(model) and not isinstance(model, type)
    as_dict = dataclasses.asdict(model)
    assert isinstance(as_dict, dict)
    assert copy.deepcopy(model) == model
    assert pickle.loads(pickle.dumps(model)) == model
    # Frozen: assigning any field raises.
    field = dataclasses.fields(model)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(model, field, getattr(model, field))


def test_volume_options_are_json() -> None:
    volume = _MODELS["Volume"]
    assert isinstance(volume, Volume)
    options = volume.options
    assert json.loads(json.dumps(options)) == {"bucket": "b", "nested": {"depth": [1, 2]}}
    assert json.loads(json.dumps(dataclasses.asdict(volume)["options"])) == options
