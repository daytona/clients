# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Generated wire models keep an explicit ``null`` apart from an omitted field,
for nullable fields a model declares and for those it inherits through
``allOf``, primitives and referenced objects alike."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Dict, Iterator, Set, Tuple

import pytest
from _repo import _repo_file
from nullable_refs import normalize
from pydantic import BaseModel

from daytona.v2._gen import api as api_models
from daytona.v2._gen import toolbox as toolbox_models
from daytona.v2._gen.api.models.sandbox_list_item import SandboxListItem

REF_PREFIX = "#/components/schemas/"
NULL = {"type": "null"}
_RESTORED = re.compile(r"_dict\['([^']+)'\] = None")

_SANDBOX: Dict[str, Any] = {
    "class": "linux",
    "cores_max": 1,
    "cpu": 1,
    "disk_mib": 1024,
    "id": "sbx",
    "memory_mib": 1024,
    "name": "sbx",
    "port_map": [],
    "region_id": "r",
    "status": "running",
    "cursor": "c",
}
_NULLABLE_OBJECTS = ("image", "lifecycle_policy", "snapshot")


def _is_nullable(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    kind = schema.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    return any(NULL in (schema.get(key) or []) for key in ("oneOf", "anyOf"))


def _properties(schemas: Dict[str, Any], schema: Dict[str, Any]) -> Iterator[Tuple[str, Any]]:
    """Every property of ``schema``, its ``allOf`` members' included."""
    if "$ref" in schema:
        yield from _properties(schemas, schemas[schema["$ref"][len(REF_PREFIX) :]])
        return
    yield from (schema.get("properties") or {}).items()
    for member in schema.get("allOf") or []:
        yield from _properties(schemas, member)


def _cases() -> Iterator[Any]:
    for spec, package in (
        ("contract/api-openapi.json", api_models),
        ("contract/toolbox-openapi.json", toolbox_models),
    ):
        schemas = json.loads(_repo_file(spec).read_text())["components"]["schemas"]
        for name, schema in sorted(schemas.items()):
            model = getattr(package, name, None)
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                continue
            nullable = {prop for prop, value in _properties(schemas, schema) if _is_nullable(value)}
            yield pytest.param(model, nullable, id=f"{package.__name__.rsplit('.', 1)[-1]}.{name}")


@pytest.mark.parametrize(("model", "nullable"), list(_cases()))
def test_to_dict_restores_explicit_null_for_every_nullable_contract_field(
    model: type[BaseModel], nullable: Set[str]
) -> None:
    restored = set(_RESTORED.findall(inspect.getsource(model.to_dict)))  # type: ignore[attr-defined]
    assert restored == nullable


@pytest.mark.parametrize("field", _NULLABLE_OBJECTS)
def test_inherited_nullable_object_keeps_explicit_null(field: str) -> None:
    explicit = SandboxListItem.from_dict({**_SANDBOX, field: None})
    omitted = SandboxListItem.from_dict(_SANDBOX)
    assert explicit is not None and omitted is not None

    assert explicit.to_dict()[field] is None
    assert json.loads(explicit.to_json())[field] is None
    assert field not in omitted.to_dict()
    assert field not in json.loads(omitted.to_json())


def test_inherited_nullable_primitive_keeps_explicit_null() -> None:
    explicit = SandboxListItem.from_dict({**_SANDBOX, "error": None})
    omitted = SandboxListItem.from_dict(_SANDBOX)
    assert explicit is not None and omitted is not None
    assert explicit.to_dict()["error"] is None
    assert "error" not in omitted.to_dict()


def test_inherited_nullable_object_keeps_a_value() -> None:
    item = SandboxListItem.from_dict({**_SANDBOX, "lifecycle_policy": {}})
    assert item is not None
    assert item.to_dict()["lifecycle_policy"] == {}


def test_normalize_rewrites_nullable_refs_to_the_3_0_form() -> None:
    ref = {"$ref": REF_PREFIX + "X"}
    spec: Dict[str, Any] = {
        "components": {
            "schemas": {
                "A": {
                    "properties": {
                        "first": {"description": "d", "oneOf": [NULL, dict(ref)]},
                        "second": {"anyOf": [dict(ref), NULL]},
                        "two_refs": {"oneOf": [NULL, dict(ref), {"$ref": REF_PREFIX + "Y"}]},
                        "inline": {"oneOf": [NULL, {"type": "string"}]},
                        "annotated_ref": {"oneOf": [NULL, {"$ref": REF_PREFIX + "X", "description": "x"}]},
                        "described_twice": {
                            "description": "outer",
                            "oneOf": [NULL, {"$ref": REF_PREFIX + "X", "description": "inner"}],
                        },
                        "ref_with_constraints": {"oneOf": [NULL, {"$ref": REF_PREFIX + "X", "minItems": 1}]},
                        "plain": dict(ref),
                    }
                }
            }
        }
    }
    assert normalize(spec) == 4
    props = spec["components"]["schemas"]["A"]["properties"]
    assert props["first"] == {"description": "d", "allOf": [ref], "nullable": True}
    assert props["second"] == {"allOf": [ref], "nullable": True}
    assert props["two_refs"] == {"oneOf": [NULL, ref, {"$ref": REF_PREFIX + "Y"}]}
    assert props["inline"] == {"oneOf": [NULL, {"type": "string"}]}
    assert props["annotated_ref"] == {"description": "x", "allOf": [ref], "nullable": True}
    assert props["described_twice"] == {"description": "outer", "allOf": [ref], "nullable": True}
    assert "oneOf" in props["ref_with_constraints"]
    assert props["plain"] == ref
