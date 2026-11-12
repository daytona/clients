# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mark credential-bearing properties in a down-converted spec for the model templates.

gen-contract.sh runs this on the ephemeral OpenAPI 3.0 copy, never on
contract/. A marked property (``x-daytona-redact``) keeps its wire value in
``model_dump`` / ``to_dict`` / ``to_json``, but ``to_str()`` and ``repr()``
show a placeholder instead. A model is marked (``x-daytona-redact-model``)
when it has such a property or nests a marked model, so a parent's
``to_str()`` redacts the credentials of the models inside it too; a marked
model also redacts credential-named keys (SENSITIVE_NAMES) among the
undeclared fields it keeps in ``additional_properties``.

A property is sensitive when the contract says so (``format: password`` or
``writeOnly``, on the value or on its items / map values), when its name is
one of SENSITIVE_NAMES, or when it is listed in SENSITIVE_PROPERTIES (the
contract describes it as secret but carries no marker).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Set, Tuple, Union

Json = Union[None, bool, int, float, str, List["Json"], Dict[str, "Json"]]

SENSITIVE_NAMES = frozenset(
    {"token", "access_token", "refresh_token", "id_token", "subject_token", "password"}
)
SENSITIVE_PROPERTIES = frozenset(
    {
        ("SetSecretRequest", "value"),
        # A presigned storage URL: its query string is the credential.
        ("BuildContextUploadPart", "url"),
        # The signed token is the hostname's first label.
        ("SignedPreviewUrl", "url"),
    }
)
REF_PREFIX = "#/components/schemas/"


def _is_marked_secret(schema: Json) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("format") == "password" or schema.get("writeOnly") is True:
        return True
    return _is_marked_secret(schema.get("items")) or _is_marked_secret(schema.get("additionalProperties"))


def _object_parts(schema: Dict[str, Json]) -> Iterator[Dict[str, Json]]:
    """The schema and its inline allOf members (the generator flattens them)."""
    yield schema
    members = schema.get("allOf")
    if isinstance(members, list):
        for member in members:
            if isinstance(member, dict) and "$ref" not in member:
                yield from _object_parts(member)


def _refs(node: Json) -> Iterator[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            yield ref[len(REF_PREFIX) :]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def mark(spec: Dict[str, Json]) -> Tuple[Set[Tuple[str, str]], Set[str]]:
    """Mark ``spec`` in place; return the marked (schema, property) pairs and models."""
    components = spec.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    if not isinstance(schemas, dict):
        return set(), set()
    properties: Set[Tuple[str, str]] = set()
    models: Set[str] = set()
    for name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        for part in _object_parts(schema):
            props = part.get("properties")
            if not isinstance(props, dict):
                continue
            for prop, prop_schema in props.items():
                if not isinstance(prop_schema, dict) or "$ref" in prop_schema:
                    continue
                if (
                    prop in SENSITIVE_NAMES
                    or (name, prop) in SENSITIVE_PROPERTIES
                    or _is_marked_secret(prop_schema)
                ):
                    prop_schema["x-daytona-redact"] = True
                    properties.add((name, prop))
                    models.add(name)
    referrers: Dict[str, Set[str]] = {}
    for name, schema in schemas.items():
        for ref in _refs(schema):
            referrers.setdefault(ref, set()).add(name)
    pending = list(models)
    while pending:
        for parent in referrers.get(pending.pop(), set()):
            if parent not in models:
                models.add(parent)
                pending.append(parent)
    for name in models:
        schema = schemas[name]
        if isinstance(schema, dict):
            schema["x-daytona-redact-model"] = True
            schema["x-daytona-credential-keys"] = sorted(SENSITIVE_NAMES)
    return properties, models


def main(argv: List[str]) -> int:
    for arg in argv:
        path = Path(arg)
        spec = json.loads(path.read_text())
        mark(spec)
        path.write_text(json.dumps(spec, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
